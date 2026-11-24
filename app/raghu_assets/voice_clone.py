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
from narration_layout import paragraph_groups


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
    event("narrator_recordings_ready", paragraphs=len(groups))


if __name__ == "__main__":
    main()
