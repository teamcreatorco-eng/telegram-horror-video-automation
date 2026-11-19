"""Render the privately provisioned, approved music bed."""
import math
import os
from pathlib import Path
from . import media, store


def source():
    configured = os.getenv("BGM_FILE", "").strip()
    path = Path(configured) if configured else store.DATA / "_music" / "obsidian.mp3"
    return path if path.is_file() else None


def render(path, seconds, track):
    duration = media.duration(track)
    overlap = min(2.0, duration / 4)
    if duration < .25:
        raise RuntimeError("Background music is empty or unreadable")
    count = max(1, math.ceil((seconds - overlap) / (duration - overlap)))
    args = []
    filters = []
    for i in range(count):
        args += ["-i", track]
        filters.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]")
    label = "a0"
    for i in range(1, count):
        next_label = f"mix{i}"
        filters.append(f"[{label}][a{i}]acrossfade=d={overlap}:c1=tri:c2=tri[{next_label}]")
        label = next_label
    filters.append(f"[{label}]atrim=duration={seconds},loudnorm=I=-26:TP=-3:LRA=9,"
                   f"afade=t=in:d=0.5,afade=t=out:st={max(0, seconds-1)}:d=1[out]")
    media.ff(*args, "-filter_complex", ";".join(filters), "-map", "[out]", "-t", str(seconds), "-ar", "48000", "-ac", "2", path)
