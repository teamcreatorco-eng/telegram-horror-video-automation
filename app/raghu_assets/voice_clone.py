"""Synthesize Hindi narrator lines on the existing Vast worker."""

import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from chatterbox.models.tokenizers.tokenizer import ChineseCangjieConverter
from chatterbox.mtl_tts import ChatterboxMultilingualTTS
from huggingface_hub import snapshot_download

from common import ROOT, event
from narration_layout import VERSION, paragraph_groups, aligned_spans


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    reference = ROOT / manifest["reference"]
    output = ROOT / "results" / manifest["output"]
    output.mkdir(exist_ok=True)
    groups = paragraph_groups(manifest["lines"])
    if not groups:
        return

    # Hindi never uses the Chinese segmenter in this pinned multilingual build.
    ChineseCangjieConverter._init_segmenter = lambda self: setattr(self, "segmenter", None)
    repo = "ResembleAI/chatterbox"
    start = time.monotonic()
    weights = snapshot_download(
        repo,
        revision="5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
        allow_patterns=["ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen.pt",
                        "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json"],
    )
    event("narrator_model_ready", repo=repo, seconds=time.monotonic() - start)
    model = ChatterboxMultilingualTTS.from_local(weights, device="cuda", t3_model="v3")
    # Keep one voice conditioning for the whole job instead of reloading it per shot.
    model.prepare_conditionals(str(reference), exaggeration=.46)
    from faster_whisper import WhisperModel
    aligner = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=4,
                           download_root=str(ROOT / "hf-cache" / "narration-alignment"))
    complete_audio, spans, cursor = [], [], 0
    for index, group in enumerate(groups):
        target = output / f"paragraph-{index:03}.wav"
        start = time.monotonic()
        words_file = target.with_suffix(".words.json")
        wav = None
        if target.is_file():
            try:
                # libsndfile tolerates truncated data; check declared sample count too.
                import wave
                with wave.open(str(target), 'rb') as header:
                    expected = header.getnframes()
                saved, rate = sf.read(target, dtype="float32")
                if rate == model.sr and saved.ndim == 1 and len(saved) == expected and len(saved):
                    wav = saved
            except (OSError, ValueError, RuntimeError, EOFError, wave.Error):
                pass
        if wav is None:
            words_file.unlink(missing_ok=True)
            torch.manual_seed(4291 + index)
            text = " ".join(line["text"] for line in group)
            wav = model.generate(text, language_id="hi", exaggeration=.46,
                                 cfg_weight=.45, temperature=.6).squeeze().cpu().numpy()
            if not len(wav) or not np.any(np.abs(wav) > .008):
                raise ValueError("Narrator returned silence for a paragraph.")
            # Do not strip breaths or natural edge pauses.
            partial = target.with_suffix(".partial.wav")
            sf.write(partial, wav, model.sr, subtype="PCM_16")
            partial.replace(target)
            wav, _ = sf.read(target, dtype="float32")
            event("narrator_paragraph_ready", index=index, seconds=time.monotonic() - start,
                  duration=len(wav) / model.sr, lines=[line["id"] for line in group])
        duration = len(wav) / model.sr
        def transcribe():
            # Whisper array input is mono 16 kHz. Keep the mastered WAV at its native rate.
            from math import gcd
            from scipy.signal import resample_poly
            factor = gcd(model.sr, 16000)
            samples = resample_poly(wav, 16000 // factor, model.sr // factor).astype(np.float32, copy=False)
            segments, _ = aligner.transcribe(samples, language="hi", word_timestamps=True,
                                             initial_prompt=" ".join(line["text"] for line in group),
                                             vad_filter=False, beam_size=5, condition_on_previous_text=False)
            return [dict(word=w.word, start=w.start, end=w.end) for segment in segments for w in segment.words or []]
        local = aligned_spans(words_file, group, duration, transcribe)
        # A failed alignment leaves paragraph WAV intact for the next retry.
        edges = [0] + [round(span["end"] * model.sr) for span in local[:-1]] + [len(wav)]
        for i, span in enumerate(local):
            path = output / f"narration-{span['id']}.wav"
            part = path.with_suffix(".partial.wav")
            sf.write(part, wav[edges[i]:edges[i + 1]], model.sr, subtype="PCM_16")
            part.replace(path)
            spans.append(dict(id=span["id"], start=(cursor + edges[i]) / model.sr,
                              end=(cursor + edges[i + 1]) / model.sr))
        complete_audio.append(wav)
        cursor += len(wav)
    master = output / "narration-master.wav"
    partial = master.with_suffix(".partial.wav")
    sf.write(partial, np.concatenate(complete_audio), model.sr, subtype="PCM_16")
    partial.replace(master)
    timing = output / "narration-timing.json"
    partial = timing.with_suffix(".partial.json")
    partial.write_text(json.dumps(dict(version=VERSION, key=manifest["key"], lines=manifest["lines"],
                                      sample_rate=model.sr, duration=cursor/model.sr, spans=spans),
                                  ensure_ascii=False), encoding="utf-8")
    partial.replace(timing)
    event("narrator_story_ready", paragraphs=len(groups), duration=cursor/model.sr)


if __name__ == "__main__":
    main()
