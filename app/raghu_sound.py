"""Synthesize scene score changes and action cues alongside the continuous bed."""

import re
import math
import random
import wave
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
}


def parse_sound_design(text):
    """Parse explicit planner cues; never guess sound from incidental story words."""
    scores = re.findall(r"\[score:(unease|suspense|dread)\]", text, re.I)
    cues = []
    for name, at in re.findall(r"\[effect:([a-z]+)@([0-9.]+)\]", text, re.I):
        try:
            fraction = float(at)
        except ValueError:
            continue
        if name.lower() in EFFECTS and 0 <= fraction <= 1:
            cues.append({"effect": name.lower(), "at": fraction})
    return (scores[0].lower() if scores else "suspense"), cues


def render_cues(path, timeline, opening_seconds, seconds):
    samples = array("h", [0]) * round(seconds * RATE)
    rng = random.Random(40)

    def add(index, value):
        samples[index] = max(-32768, min(32767, samples[index] + round(value * 32767)))

    # Merge adjacent shots with the same score so the music carries across cuts.
    runs = []
    for shot in timeline:
        start = round((shot["start"] - opening_seconds) * RATE)
        end = min(len(samples), start + round(shot["seconds"] * RATE))
        score = shot["score"]
        if runs and runs[-1][0] == score:
            runs[-1] = (score, runs[-1][1], end)
        else:
            runs.append((score, start, end))
        for cue in shot["sound_cues"]:
            effect = EFFECTS.get(cue["effect"])
            if effect is None:
                continue
            duration, pitch, sweep, noise, gain = effect
            ambient = cue["effect"] in {"fire", "rain", "wind"}
            if ambient:
                duration = max(.01, (end - start) / RATE * (1 - cue["at"]))
            onset = min(max(start, end - round(.08 * RATE)),
                        start + round(cue["at"] * (end - start)))
            for index in range(onset, min(end, onset + round(duration * RATE))):
                t = (index - onset) / RATE
                envelope = min(1, t / .012, (end - index) / (RATE * .02)) * (1 - t / duration) ** 2
                if ambient:
                    envelope = min(1, t / .08, (end - index) / (RATE * .08))
                tone = math.sin(TAU * (pitch * t + sweep * t * t / 2))
                add(index, gain * envelope * ((1 - noise) * tone + noise * rng.uniform(-1, 1)))

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
