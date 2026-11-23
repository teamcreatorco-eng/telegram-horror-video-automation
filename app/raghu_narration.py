"""Use the same Hindi voice for every shot in the approved narration path."""

import asyncio
import hashlib
import math
import os
import re
import tempfile
import wave
from pathlib import Path

from . import media, store, bundled_media
from .narration_layout import VERSION, request_key

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


def produce_clone(remote, folder, shots, check, progress, assets):
    missing = pending_clone(folder, shots)
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
    # Paragraph checkpoints are retained remotely; retry alignment without resynthesizing speech.
    remote.phase("bash worker.sh voice narrator-input.json >results/narrator.log 2>&1",
                 600 + 30 * len(shots))
    files = [f"narration-{s['id']}.wav" for s in shots if narrator_line(s)]
    remote.pull([*files, "narration-master.wav"], output, subdir=directory)
    if any(not readable_voice(output / name) for name in [*files, "narration-master.wav"]):
        raise RuntimeError("A cloned narrator artifact is unreadable; saved paragraphs remain available for retry.")
    # Completion is pulled last so an interrupted transfer cannot mark the job complete.
    remote.pull(["narration-timing.json"], output, subdir=directory)
    if pending_clone(folder, shots):
        raise RuntimeError("The continuous narrator checkpoint does not match this script.")
    progress("Continuous Hindi narration and speech timestamps saved", 53)


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
