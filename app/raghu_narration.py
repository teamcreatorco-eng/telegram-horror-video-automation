"""Use the same Hindi voice for every shot in the approved narration path."""

import asyncio
import hashlib
import math
import os
import re
import tempfile
import subprocess
import wave
from pathlib import Path

from . import media, store, bundled_media
from .narration_layout import VERSION, request_key, paragraph_groups, aligned_spans, pause_spans
from .raghu_vast import VastRemoteCommandFailed

VOICE = "hi-IN-MadhurNeural"


def silent():
    return os.getenv("RAGHU_NARRATION_PROVIDER", "").lower() == "none"


def narrator_folder(folder):
    return folder / "narration-madhur"


def uses_clone():
    return os.getenv("RAGHU_NARRATION_PROVIDER", "gemini").lower() == "chatterbox"


def clone_folder(folder):
    return folder / "narration-clone"


def clone_reference():
    configured = os.getenv("RAGHU_NARRATOR_REFERENCE")
    path = Path(configured) if configured else store.DATA / "_voice_refs" / "web-6-narrator-reference.wav"
    if not configured and not path.is_file():
        path = bundled_media.available('narrator.wav') or path
    if not path.is_file() or not 6 <= media.duration(path) <= 30:
        raise RuntimeError("The narrator voice reference is missing or unusable. No GPU was rented.")
    return path


def narrator_line(shot):
    # One established narrator voices the entire film, including reported dialogue.
    return True


def narration_lines(shots):
    return [dict(id=s["id"], text=s["narration"],
                 speaker="NARRATOR" if narrator_line(s) else "DIALOGUE") for s in shots]


def readable_voice(path):
    try:
        with wave.open(str(path), "rb") as stream:
            expected=stream.getnframes()*stream.getnchannels()*stream.getsampwidth()
            if expected<=0:return False
            read=0
            while chunk:=stream.readframes(65536):read+=len(chunk)
            return read==expected
    except (OSError, EOFError, wave.Error):
        return False


def pending_clone(folder, shots):
    if not uses_clone():
        return []
    narrator = [s for s in shots if narrator_line(s)]
    if not narrator:
        return []
    output = clone_folder(folder)
    try:
        timing = store.read(output / "narration-timing.json")
        if not isinstance(timing, dict):
            return narrator
        reference = clone_reference()
        key = request_key(narration_lines(shots), hashlib.sha256(reference.read_bytes()).hexdigest())
        if (timing.get("version") != VERSION or timing.get("key") != key
                or not readable_voice(output / "narration-master.wav")):
            return narrator
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return narrator
    missing = [s for s in narrator if not readable_voice(output / f"narration-{s['id']}.wav")]
    if missing:
        # Saved master + timestamps can restore lost scene files locally, with no rental.
        try:
            spans = timing['spans']
            if [span['id'] for span in spans] != [s['id'] for s in narrator]:
                return narrator
            with wave.open(str(output / 'narration-master.wav'), 'rb') as stream:
                rate, cursor = stream.getframerate(), 0
                for span in spans:
                    start, end = float(span['start']), float(span['end'])
                    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                        return narrator
                    first, last = round(start * rate), round(end * rate)
                    if first != cursor or last > stream.getnframes():
                        return narrator
                    cursor = last
                if cursor != stream.getnframes():
                    return narrator
                wanted = {s['id'] for s in missing}
                for span in spans:
                    if span['id'] not in wanted:
                        continue
                    first, last = round(span['start'] * rate), round(span['end'] * rate)
                    stream.setpos(first)
                    target = output / f"narration-{span['id']}.wav"
                    partial = target.with_suffix('.partial.wav')
                    with wave.open(str(partial), 'wb') as writer:
                        writer.setparams(stream.getparams())
                        writer.writeframes(stream.readframes(last - first))
                    partial.replace(target)
        except (OSError, EOFError, wave.Error, KeyError, TypeError, ValueError):
            return narrator
    return [s for s in narrator if not readable_voice(output / f"narration-{s['id']}.wav")]


def raw_paragraphs(folder, lines, key):
    raw = clone_folder(folder) / ("raw-" + key)
    return [(raw / f"paragraph-{i:03}.wav", group) for i, group in enumerate(paragraph_groups(lines))]


def pending_rental_audio(folder, shots):
    missing = pending_clone(folder, shots)
    if not missing:
        return []
    lines = narration_lines(shots)
    key = request_key(lines, hashlib.sha256(clone_reference().read_bytes()).hexdigest())
    return missing if any(not readable_voice(p) for p, _ in raw_paragraphs(folder, lines, key)) else []


def align_recordings(folder, shots, check, progress):
    if not pending_clone(folder, shots):
        return  # Includes previously completed narration checkpoints.
    lines = narration_lines(shots)
    key = request_key(lines, hashlib.sha256(clone_reference().read_bytes()).hexdigest())
    recordings = raw_paragraphs(folder, lines, key)
    output = clone_folder(folder)
    model = None
    spans = []
    alignment_notes = []
    cursor = 0
    sample_rate = None
    master = output / "narration-master.partial.wav"
    with wave.open(str(master), "wb") as writer:
        # Set a valid header even if the first saved paragraph cannot be read.
        writer.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        for index, (path, group) in enumerate(recordings, 1):
            check()
            progress(f"Editing: aligning saved Hindi paragraph {index}/{len(recordings)}", 82)
            with wave.open(str(path), "rb") as source:
                rate, frames = source.getframerate(), source.getnframes()
                if source.getnchannels() != 1 or source.getsampwidth() != 2 or (sample_rate and rate != sample_rate):
                    raise RuntimeError(f"Unsupported saved narration format in {path.name}")
                if sample_rate is None:
                    sample_rate = rate
                    writer.setframerate(rate)
                pcm = source.readframes(frames)
                if not frames or len(pcm) != 2 * frames:
                    raise RuntimeError(f"Incomplete narration recording in {path.name}")

            def transcribe():
                nonlocal model
                try:
                    import numpy as np
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise RuntimeError("Install the updated requirements on the editing host, then retry the saved edit.") from exc
                if model is None:
                    model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=4,
                                         download_root=str(store.DATA / "_narration-alignment"))
                converted = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ar", "16000",
                                            "-ac", "1", "-f", "f32le", "pipe:1"], capture_output=True, check=True, timeout=60)
                segments, _ = model.transcribe(np.frombuffer(converted.stdout, dtype="<f4"), language="hi",
                                               initial_prompt=" ".join(line["text"] for line in group),
                                               word_timestamps=True, vad_filter=False, beam_size=5,
                                               condition_on_previous_text=False)
                return [dict(word=w.word, start=w.start, end=w.end) for seg in segments for w in seg.words or []]

            try:
                local = aligned_spans(path.with_suffix(".words.json"), group, frames / rate, transcribe)
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                # Preserve the full narration when recognition is unavailable or unreliable.
                check()
                local = pause_spans(group, pcm, rate)
                alignment_notes.append(dict(paragraph=index, mode="approximate_pause_timing",
                                            reason=type(exc).__name__))
                progress(f"Editing paragraph {index}: using approximate cuts near speech pauses", 82)
            edges = [0] + [round(span["end"] * rate) for span in local[:-1]] + [frames]
            for span, start, end in zip(local, edges, edges[1:]):
                target = output / f"narration-{span['id']}.wav"
                partial = target.with_suffix(".partial.wav")
                with wave.open(str(partial), "wb") as part:
                    part.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                    part.writeframes(pcm[2*start:2*end])
                partial.replace(target)
                spans.append(dict(id=span["id"], start=(cursor+start)/rate, end=(cursor+end)/rate))
            writer.writeframes(pcm)
            cursor += frames
    master.replace(output / "narration-master.wav")
    store.save(output / "narration-timing.json", dict(version=VERSION, key=key, lines=lines,
               sample_rate=sample_rate, duration=cursor/sample_rate, spans=spans, alignment_notes=alignment_notes))


def produce_clone(remote, folder, shots, check, progress, assets):
    missing = pending_rental_audio(folder, shots)
    if not missing:
        return
    if not re.fullmatch(r"[a-f0-9]{32}", folder.name) or any(
        not re.fullmatch(r"[0-9]{3}", shot["id"]) for shot in shots
    ):
        raise RuntimeError("The narration shot IDs are invalid.")
    check()
    output = clone_folder(folder)
    output.mkdir(exist_ok=True)
    reference = clone_reference()
    lines = narration_lines(shots)
    key = request_key(lines, hashlib.sha256(reference.read_bytes()).hexdigest())
    directory = f"voice-story-{folder.name}-{key[:12]}"
    manifest = folder / "narrator-input.json"
    store.save(manifest, {"reference": reference.name, "output": directory, "key": key, "lines": lines})
    with tempfile.TemporaryDirectory(prefix="raghu-voice-worker-") as tmp:
        worker = Path(tmp) / "worker.sh"
        worker.write_text((assets / "worker.sh").read_text(encoding="utf-8-sig"),
                          encoding="utf-8", newline="\n")
        remote.push([reference, manifest, worker, assets / "voice_clone.py",
                     Path(__file__).with_name("narration_layout.py")])
    progress("Generating continuous Hindi narration with natural sentence pauses", 52)
    # Keep the remote directory compatible with older interrupted narration runs.
    try:
        remote.phase("bash worker.sh voice narrator-input.json >results/narrator.log 2>&1",
                     600 + 30 * len(shots))
    except VastRemoteCommandFailed as exc:
        message = f"Hindi narration worker failed. {exc}"
        try:
            remote.pull(["narrator.log"], folder)
            with (folder / "narrator.log").open("rb") as log:
                log.seek(max(0, log.seek(0, 2) - 8192))
                tail = log.read().decode("utf-8", errors="replace").lower()
            # Only send known categories to Telegram. Raw provider logs can contain secrets.
            if "valueerror: narration" in tail and "timestamp" in tail:
                message = "Hindi narration timing could not be matched to the script. Saved paragraphs can be reused."
            elif "out of memory" in tail:
                message = "The narration worker ran out of memory."
            elif "no space left on device" in tail:
                message = "The Vast disk filled up during narration."
            message += " Details saved in narrator.log."
        except (RuntimeError, TimeoutError, OSError):
            pass
        raise RuntimeError(message) from exc
    for path, _ in raw_paragraphs(folder, lines, key):
        check()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not readable_voice(path):
            remote.pull([path.name], path.parent, subdir=directory)
        if not readable_voice(path):
            raise RuntimeError(f"Unreadable saved narration recording: {path.name}")
    progress("Hindi recordings saved for alignment during editing", 53)


def produce(folder, shots, check, progress):
    import edge_tts

    folder.mkdir(exist_ok=True)

    async def run():
        gate = asyncio.Semaphore(3)
        completed = 0

        async def render(shot):
            nonlocal completed
            async with gate:
                check()
                ident = shot["id"]
                output = folder / f"narration-{ident}.wav"
                if not readable_voice(output):
                    mp3 = folder / f"narration-{ident}.partial.mp3"
                    wav = folder / f"narration-{ident}.partial.wav"
                    try:
                        for attempt in range(3):
                            check()
                            try:
                                voice_name = VOICE
                                rate, pitch = "-8%", "-7Hz"
                                voice = edge_tts.Communicate(shot["narration"], voice_name, rate=rate, pitch=pitch)
                                await asyncio.wait_for(voice.save(str(mp3)), timeout=35)
                                break
                            except Exception as exc:
                                if attempt == 2:
                                    raise RuntimeError(f"Hindi narration failed for shot {ident}: "
                                                       f"{type(exc).__name__}.") from None
                                await asyncio.sleep(3 * (attempt + 1))
                        await asyncio.to_thread(media.ff, "-i", mp3, "-ar", "24000", "-ac", "1", wav)
                        wav.replace(output)
                    finally:
                        mp3.unlink(missing_ok=True)
                        wav.unlink(missing_ok=True)
                completed += 1
                if completed == len(shots) or completed == 1 or completed % 6 == 0:
                    progress(f"Hindi narration saved Â· {completed}/{len(shots)} lines", 81)

        results = await asyncio.gather(*(render(shot) for shot in shots), return_exceptions=True)
        failure = next((result for result in results if isinstance(result, BaseException)), None)
        if failure:
            raise failure

    asyncio.run(run())
