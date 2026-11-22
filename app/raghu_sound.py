"""Synthesize scene score changes and action cues alongside the continuous bed."""

import re
import math
import random
import wave
import json
import hashlib
from functools import lru_cache
from pathlib import Path
from array import array

RATE = 24000
TAU = math.tau
SCORE = {"unease": (146, 156), "suspense": (192, 207), "dread": (117, 123)}
# Length, fundamental, pitch change, noise share, gain.
EFFECTS = {
    "fire": (5.0, 65, 0, .95, .07),
    "footstep": (.16, 105, -230, .30, .16),
    "knock": (.24, 205, -260, .23, .25),
    "creak": (.82, 275, -145, .12, .18),
    "latch": (.14, 930, -1600, .30, .18),
    "rustle": (.46, 420, -100, .78, .11),
    "thud": (.42, 93, -140, .19, .28),
    "sting": (.82, 445, 150, .07, .20),
    "wind": (1.7, 76, 12, .35, .10),
    "rain": (1.7, 102, 0, .87, .08),
    "bell": (1.15, 880, -34, .03, .22),
    "rattle": (.38, 670, -550, .58, .11),
    "radio": (4.0, 350, 0, .98, .18),
    "break_glass": (1.1, 2100, -1200, .90, .28),
    "break": (.65, 160, -160, .85, .26),
    "sizzle": (3.0, 850, 0, .97, .15),
    "drip": (.38, 1400, -800, .08, .18),
    "slam": (.65, 70, -90, .45, .27),
    "whisper": (1.5, 260, 40, .97, .10),
    "heartbeat": (.85, 62, -20, .05, .20),
    "breath": (1.1, 180, 0, .96, .10),
    "whoosh": (.8, 180, -100, .9, .15),
    "thunder": (3.0, 48, -12, .85, .23),
    "laugh": (2.0, 250, 0, .25, .17),
}
AMBIENT = {"fire", "rain", "wind", "radio", "sizzle"}
ALIASES = {"radio_static": "radio", "static": "radio", "static_noise": "radio",
           "glass_break": "break_glass", "glass": "break_glass", "shatter": "break_glass",
           "breaking": "break", "crack": "break", "footsteps": "footstep",
           "door_creak": "creak", "door_slam": "slam", "paper": "rustle", "page_turn": "rustle",
           "dripping": "drip", "water_drip": "drip", "heating": "sizzle", "boiling": "sizzle",
           "sizzling": "sizzle", "crackle": "fire", "breathing": "breath",
           "click": "latch", "thread": "rustle", "slide": "creak"}
ASSET_DIR = Path(__file__).resolve().parent / 'sound_assets'


def effect_name(name):
    key = str(name).lower().replace('-', '_')
    key = ALIASES.get(key, key)
    return key if key in EFFECTS else None


def parse_sound_design(text):
    """Parse explicit planner cues; never guess sound from incidental story words."""
    scores = re.findall(r"\[score:(unease|suspense|dread)\]", text, re.I)
    cues = []
    for name, at in re.findall(r"\[effect:([a-z][a-z_-]*)@([0-9.]+)\]", text, re.I):
        try:
            fraction = float(at)
        except ValueError:
            continue
        canonical = effect_name(name)
        if canonical and 0 <= fraction <= 1:
            cues.append({"effect": canonical, "at": fraction})
    return (scores[0].lower() if scores else "suspense"), cues


def resolve_shot_sound(shot):
    """Recover saved explicit cues; use audible directions, never narration keywords.

    Unrecognized tags are reported rather than silently discarded. No AI calls or
    generation gates are added by this editing-stage compatibility repair.
    """
    text = shot.get('sound_design', '') or ''
    score, parsed = parse_sound_design(text)
    cues, unknown = [], []
    for cue in list(shot.get('sound_cues') or []) + parsed:
        name = effect_name(cue.get('effect', ''))
        try:
            at = float(cue.get('at', 0))
        except (ValueError, TypeError):
            continue
        if name and math.isfinite(at) and 0 <= at <= 1:
            candidate = dict(effect=name, at=at)
            if candidate not in cues:
                cues.append(candidate)
        elif not name and str(cue.get('effect', '')).lower() not in {'silence', 'none'}:
            unknown.append(str(cue.get('effect', '')))
    tagged = re.findall(r'\[effect:([a-z][a-z_-]*)@', text, re.I)
    unknown += [name for name in tagged if effect_name(name) is None and name.lower() not in {'silence', 'none'}]
    # Legacy plans sometimes contain readable directions instead of machine tags.
    if not cues and not tagged:
        patterns = {
            'rain': r'\brain(?:fall|ing)?\b|बारिश|वर्षा',
            'fire': r'\bfire\b|\bflames?\b|आग|लपट',
            'radio': r'\bstatic\b|\bradio\b.{0,40}\b(?:hiss|crackl|buzz|play|tun)|रेडियो.{0,30}(?:आवाज़|शोर|खरखर)',
            'break_glass': r'\bglass\b.{0,35}\b(?:shatter|break)|काँच.{0,20}टूट',
            'sizzle': r'\bsizzl|\bboiling\b|छन्न|खौल',
            'drip': r'\bdrip|टपक|बूँद',
            'creak': r'\bcreak|चरमर',
            'slam': r'\bslam|धड़ाम',
            'footstep': r'\bfootsteps?\b|कदमों',
            'knock': r'\bknock|दस्तक',
            'rustle': r'\brustl|\bpage.{0,15}(?:turn|flip)|सरसर',
            'wind': r'\bwind\b|हवा',
            'bell': r'\bbell\b|घंटी',
            'thunder': r'\bthunder|गरज',
            'whisper': r'\bwhisper|फुसफुस',
            'breath': r'\bbreath|साँस',
            'heartbeat': r'\bheart.?beat|धड़कन',
        }
        readable = re.sub(r'\[[^]]*\]', '', text)
        readable = re.sub(r'\b(?:no|without)\s+(?:music|dialogue|speech|narration)\b', '', readable, flags=re.I)
        for clause in re.split(r'[.!;\n]+', readable):
            if re.search(r'\b(?:no|not|without|silently|silent|silence|stops?|stopped|stopping|absent|extinguished)\b'
                         r'|\b(?:switched|turned|powered)\s+off\b|\bnever\s+(?:heard|audible)\b'
                         r'|नहीं|खामोश|बंद|बुझ', clause, re.I):
                continue
            for name, pattern in patterns.items():
                if re.search(pattern, clause, re.I) and not any(c['effect'] == name for c in cues):
                    cues.append(dict(effect=name, at=0 if name in AMBIENT else .5))
    return dict(score=shot.get('score') or score, sound_cues=cues,
                unresolved_sound_cues=list(dict.fromkeys(unknown)))


@lru_cache(maxsize=24)
def recorded_samples(name):
    """Small CC0 recordings ship with the code; no API/network dependency at edit time."""
    try:
        manifest = json.loads((ASSET_DIR / 'manifest.json').read_text(encoding='utf-8'))
        item = manifest[name]
        path = ASSET_DIR / item['file']
        if path.parent != ASSET_DIR or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            return None
        with wave.open(str(path), 'rb') as stream:
            if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, RATE):
                return None
            data = array('h', stream.readframes(stream.getnframes()))
        peak = max((abs(x) for x in data), default=0)
        rms = math.sqrt(sum(x*x for x in data) / len(data)) if data else 0
        return (data, peak, rms, item) if peak else None
    except (OSError, ValueError, KeyError, wave.Error):
        return None


def render_cues(path, timeline, opening_seconds, seconds):
    samples = array("h", [0]) * round(seconds * RATE)
    rng = random.Random(40)
    report = []

    def add(index, value):
        samples[index] = max(-32768, min(32767, samples[index] + round(value * 32767)))

    # Merge adjacent shots with the same score so the music carries across cuts.
    runs = []
    for shot in timeline:
        start = max(0, round((shot["start"] - opening_seconds) * RATE))
        end = min(len(samples), start + round(shot["seconds"] * RATE))
        score = shot["score"]
        if runs and runs[-1][0] == score:
            runs[-1] = (score, runs[-1][1], end)
        else:
            runs.append((score, start, end))
        for cue in shot["sound_cues"]:
            name = effect_name(cue["effect"])
            effect = EFFECTS.get(name)
            if effect is None:
                continue
            duration, pitch, sweep, noise, gain = effect
            ambient = name in AMBIENT
            if ambient:
                duration = max(.01, (end - start) / RATE * (1 - cue["at"]))
            onset = min(max(start, end - round(.08 * RATE)),
                        start + round(cue["at"] * (end - start)))
            recording = recorded_samples(name)
            entry = dict(shot=shot.get('id'), effect=name, at_seconds=onset / RATE,
                         final_at_seconds=onset / RATE + opening_seconds,
                         kind='recorded' if recording else 'synthesized')
            if recording:
                data, peak, rms, metadata = recording
                entry.update(asset=metadata['file'], license=metadata['license'], source=metadata['source'])
                # Peak-only normalization hides quiet Foley beneath narration when
                # a recording has a single large transient. Match useful RMS and
                # soften isolated peaks on the effects bus, never on speech files.
                target_rms = max(.025, gain * .20) if ambient else gain * .33
                scale = target_rms / max(1, rms)
                count = (end - onset) if ambient else min(end - onset, len(data))
                overlap = min(round(.03 * RATE), len(data) // 4)
                stride = max(1, len(data) - overlap)
                for relative in range(max(0, count)):
                    index = onset + relative
                    if ambient:
                        j = relative % stride
                        value = (data[stride+j] * (1-j/overlap) + data[j] * j/overlap
                                 if overlap and j < overlap else data[j])
                    else:
                        value = data[relative]
                    envelope = min(1, relative / (RATE * .012), (count-relative) / (RATE * .025))
                    add(index, envelope * .35 * math.tanh(value * scale / .35))
                entry['end_seconds'] = (onset + max(0, count)) / RATE
                report.append(entry)
                continue
            for index in range(onset, min(end, onset + round(duration * RATE))):
                t = (index - onset) / RATE
                envelope = min(1, t / .012, (end - index) / (RATE * .02)) * (1 - t / duration) ** 2
                if ambient:
                    envelope = min(1, t / .08, (end - index) / (RATE * .08))
                tone = math.sin(TAU * (pitch * t + sweep * t * t / 2))
                add(index, gain * envelope * ((1 - noise) * tone + noise * rng.uniform(-1, 1)))
            entry['end_seconds'] = min(end / RATE, onset / RATE + duration)
            report.append(entry)

    for score, start, end in runs:
        if score not in SCORE:
            continue
        low, high = SCORE[score]
        for index in range(start, end):
            t = (index - start) / RATE
            fade = min(1, t / .35, (end - index) / (RATE * .45))
            swell = .55 + .45 * math.sin(TAU * .24 * t) ** 2
            chord = math.sin(TAU * low * t) + .55 * math.sin(TAU * high * t + .35 * math.sin(t))
            add(index, .052 * fade * swell * chord)

    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())
    return report


def mix_soundtrack(source, target, cues, bed=None):
    """Keep picture packets and speech timing; duck music, not action transients."""
    from . import media
    args = ['-i', source, '-i', cues]
    if bed:
        args += ['-i', bed]
        filters = ('[0:a]asplit[voice][key];'
                   '[2:a][key]sidechaincompress=threshold=0.042:ratio=3:attack=15:release=200[music];'
                   '[1:a]aformat=sample_rates=48000:channel_layouts=stereo[fx];'
                   '[voice][music][fx]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.90:level=false[a]')
    else:
        filters = ('[1:a]aformat=sample_rates=48000:channel_layouts=stereo[fx];'
                   '[0:a][fx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=false[a]')
    media.ff(*args, '-filter_complex', filters, '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy',
             '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '192k', '-movflags', '+faststart', target)
