"""Use the same Hindi voice for every shot in the approved narration path."""

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path

from . import media, store

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
    if not path.is_file() or not 6 <= media.duration(path) <= 30:
        raise RuntimeError("The narrator voice reference is missing or unusable. No GPU was rented.")
    return path


def narrator_line(shot):
    return shot.get("speaker", "NARRATOR") == "NARRATOR" and not shot.get("dialogue_voice")


def pending_clone(folder, shots):
    if not uses_clone():
        return []
    return [shot for shot in shots if narrator_line(shot)
            and not (clone_folder(folder) / f"narration-{shot['id']}.wav").is_file()]


def produce_clone(remote, folder, shots, check, progress, assets):
    missing = pending_clone(folder, shots)
    if not missing:
        return
    if not re.fullmatch(r"[a-f0-9]{32}", folder.name) or any(
        not re.fullmatch(r"[0-9]{3}", shot["id"]) for shot in missing
    ):
        raise RuntimeError("The narration shot IDs are invalid.")
    check()
    output = clone_folder(folder)
    output.mkdir(exist_ok=True)
    reference = clone_reference()
    digest = hashlib.sha256(reference.read_bytes()).hexdigest()[:12]
    directory = f"voice-{folder.name}-{digest}"
    manifest = folder / "narrator-input.json"
    store.save(manifest, {"reference": reference.name, "output": directory,
                          "lines": [{"id": s["id"], "text": s["narration"]} for s in missing]})
    with tempfile.TemporaryDirectory(prefix="raghu-voice-worker-") as tmp:
        worker = Path(tmp) / "worker.sh"
        worker.write_text((assets / "worker.sh").read_text(encoding="utf-8-sig"),
                          encoding="utf-8", newline="\n")
        remote.push([reference, manifest, worker, assets / "voice_clone.py"])
    progress(f"Cloning the Hindi narrator · {len(missing)} lines pending", 52)
    remote.phase("bash worker.sh voice narrator-input.json >results/narrator.log 2>&1",
                 360 + 20 * len(missing))
    files = [f"narration-{s['id']}.wav" for s in missing]
    remote.pull(files, output, subdir=directory)
    if any(media.duration(output / name) < .25 for name in files):
        raise RuntimeError("A cloned narrator line was empty.")
    progress("Cloned Hindi narration saved", 53)


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
                if not output.is_file():
                    mp3 = folder / f"narration-{ident}.partial.mp3"
                    wav = folder / f"narration-{ident}.partial.wav"
                    try:
                        for attempt in range(3):
                            check()
                            try:
                                who = shot.get("dialogue_voice")
                                voice_name = "hi-IN-SwaraNeural" if who == "female" else VOICE
                                rate, pitch = ("-4%", "+1Hz") if who else ("-8%", "-7Hz")
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
                    progress(f"Hindi narration saved · {completed}/{len(shots)} lines", 81)

        results = await asyncio.gather(*(render(shot) for shot in shots), return_exceptions=True)
        failure = next((result for result in results if isinstance(result, BaseException)), None)
        if failure:
            raise failure

    asyncio.run(run())
