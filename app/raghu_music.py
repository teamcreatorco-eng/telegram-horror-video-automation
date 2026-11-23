"""Render only music explicitly supplied under the raw-audio policy."""
import math
import os
from pathlib import Path
from . import media, store, bundled_media

CATALOG = {
    'ghostly-piano': 'Ghostly Piano Horror',
    'midnight-graveyard': 'Midnight Graveyard Ambient',
    'shadows-halloween': 'Shadows of Halloween Night',
    'haunted-manor': 'The Haunted Manor',
}


def choices():
    result = []
    for ident, title in CATALOG.items():
        for root in (store.DATA / '_music' / 'catalog', bundled_media.ROOT / 'music'):
            track, preview = root / f'{ident}.mp3', root / 'previews' / f'{ident}.mp4'
            if track.is_file() and preview.is_file():
                result.append(dict(id=ident, title=title, file=track, preview=preview))
                break
    return result


def source(selection=None):
    if selection is not None:
        if selection == 'none':
            return None
        if selection not in CATALOG:
            raise RuntimeError('Unknown background music selection')
        entry = next((entry for entry in choices() if entry['id'] == selection), None)
        if entry is None:
            raise RuntimeError('The selected background music is not installed on this host')
        return entry['file']
    configured = os.getenv("RAGHU_USER_BGM_FILE", "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        raise RuntimeError('The supplied background music file is missing or unreadable')
    return path


def validate_source(selection=None):
    track = source(selection) if selection is not None else source()
    if track is None:
        return
    try:
        info = media.probe(track)
        duration = float(info['format']['duration'])
        if not math.isfinite(duration) or duration < .25:
            raise ValueError('Empty music')
        if not any(s.get('codec_type') == 'audio' for s in info['streams']):
            raise ValueError('Not audio')
        media.run([os.getenv('FFMPEG', 'ffmpeg'), '-v', 'error', '-xerror',
                   '-i', str(track), '-map', '0:a:0', '-f', 'null', '-'])
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        raise RuntimeError('The supplied background music is unreadable or contains no audio; no GPU was rented') from None


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
    filters.append(f"[{label}]atrim=duration={seconds},loudnorm=I=-24:TP=-3:LRA=9,"
                   f"afade=t=in:d=0.5,afade=t=out:st={max(0, seconds-1)}:d=1[out]")
    media.ff(*args, "-filter_complex", ";".join(filters), "-map", "[out]", "-t", str(seconds), "-ar", "48000", "-ac", "2", path)


def mix(source, target, bed):
    """Moderate music level with gentle ducking; preserve picture and speech pace."""
    filters = ('[0:a]asplit[voice][key];'
               '[1:a][key]sidechaincompress=threshold=0.08:ratio=2:attack=20:release=300[music];'
               '[voice][music]amix=inputs=2:duration=first:normalize=0,'
               'alimiter=limit=0.95:level=false[a]')
    media.ff('-i', source, '-i', bed, '-filter_complex', filters,
             '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac',
             '-ar', '48000', '-ac', '2', '-b:a', '192k', '-movflags', '+faststart', target)
