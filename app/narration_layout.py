"""Narrator paragraph grouping and speech-based shot boundaries (no audio retiming)."""
from difflib import SequenceMatcher
import hashlib
import json
import math
import unicodedata

VERSION = 'paragraph-narration-v1'


def request_key(lines, reference):
    raw = json.dumps([VERSION, reference, lines], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def paragraph_groups(lines, max_words=45, max_characters=300):
    """Bound Chatterbox's inference length without restarting at each scene."""
    groups, current = [], []
    for line in lines:
        if line.get('speaker', 'NARRATOR') != 'NARRATOR':
            if current:
                groups.append(current)
                current = []
            continue
        candidate = current + [line]
        text = ' '.join(item['text'] for item in candidate)
        if current and (len(text.split()) > max_words or len(text) > max_characters):
            groups.append(current)
            current = []
        if not line['text'].strip() or len(line['text']) > max_characters or len(line['text'].split()) > max_words:
            raise ValueError('Narration sentence exceeds the supported paragraph size; split at a sentence boundary.')
        current.append(line)
    if current:
        groups.append(current)
    return groups


def _letters(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold()
                   if unicodedata.category(c)[0] in {'L', 'M', 'N'})


def aligned_spans(path, lines, duration, transcribe):
    """Retry corrupt timestamp artifacts, retaining the expensive paragraph audio."""
    try:
        words = json.loads(path.read_text(encoding='utf-8'))
        return line_spans(lines, words, duration)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    words = transcribe()
    spans = line_spans(lines, words, duration)
    partial = path.with_suffix('.partial.json')
    partial.write_text(json.dumps(words, ensure_ascii=False), encoding='utf-8')
    partial.replace(path)
    return spans


def line_spans(lines, words, duration):
    """Match script characters to ASR words; cut only in gaps between words.

    Failure is an unreadable/alignment artifact, never a visual-quality rejection.
    No proportional guessed timings: a missing boundary needs transcription retry.
    """
    if not math.isfinite(duration) or duration <= 0 or not words:
        raise ValueError('Narration word timestamps are missing.')
    observed, char_words = '', []
    previous = 0.0
    for index, word in enumerate(words):
        start, end = float(word['start']), float(word['end'])
        if not all(math.isfinite(t) for t in (start, end)) or start < previous - .05 or end < start or end > duration + .1:
            raise ValueError('Narration word timestamps are invalid.')
        previous = end
        letters = _letters(word['word'])
        observed += letters
        char_words.extend([index] * len(letters))
    script_parts = [_letters(line['text']) for line in lines]
    script = ''.join(script_parts)
    if not script or not observed or any(not part for part in script_parts):
        raise ValueError('Narration word timestamps contain no matching speech.')
    match = SequenceMatcher(None, script, observed, autojunk=False)
    mapping = {a + n: char_words[b + n] for a, b, size in match.get_matching_blocks() for n in range(size)}
    if len(mapping) / len(script) < .65:
        raise ValueError('Narration word timestamps do not match the script; retry transcription of the saved audio.')
    offset = 0
    for line, part in zip(lines, script_parts, strict=True):
        if sum(p in mapping for p in range(offset, offset + len(part))) / len(part) < .65:
            raise ValueError('Narration line has incomplete word timestamps; retry transcription of saved audio.')
        tokens = [token for word in line['text'].split() if (token := _letters(word))]
        for anchor, first in ((tokens[0], offset), (tokens[-1], offset + len(part) - len(tokens[-1]))):
            if sum(p in mapping for p in range(first, first + len(anchor))) / len(anchor) < .65:
                raise ValueError('Narration ending or opening has incomplete word timestamps; retry transcription of saved audio.')
        offset += len(part)
    boundaries, offset = [0.0], 0
    for part in script_parts[:-1]:
        offset += len(part)
        left = next((mapping[p] for p in range(offset - 1, max(-1, offset - 13), -1) if p in mapping), None)
        right = next((mapping[p] for p in range(offset, min(len(script), offset + 12)) if p in mapping), None)
        if left is None or right is None or left >= right:
            raise ValueError('Narration scene boundary has no usable word timestamps; retry transcription of saved audio.')
        boundary = (float(words[left]['end']) + float(words[right]['start'])) / 2
        if not boundaries[-1] < boundary < duration:
            raise ValueError('Narration scene timestamps are not in order.')
        boundaries.append(boundary)
    boundaries.append(duration)
    return [dict(id=line['id'], start=boundaries[i], end=boundaries[i + 1]) for i, line in enumerate(lines)]


def pause_spans(lines, pcm, rate):
    """Approximate uncertain scene boundaries at nearby quiet audio windows."""
    from array import array
    import sys
    samples = array('h')
    samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    duration = len(samples) / rate
    if not samples or duration <= 0 or not lines:
        raise ValueError("No readable narration samples for editing")
    window = max(1, rate // 50)
    energy = [sum(abs(x) for x in samples[i:i+window]) / len(samples[i:i+window])
              for i in range(0, len(samples), window)]
    quiet = max(30, max(energy) * .025)
    weights = [max(1, len(line['text'].split())) for line in lines]
    total, cumulative = sum(weights), 0
    boundaries = [0.0]
    for i, weight in enumerate(weights[:-1], 1):
        cumulative += weight
        target = duration * cumulative / total
        low = max(boundaries[-1] + 1/rate, target - .5)
        high = min(duration - (len(lines)-i)/rate, target + .5)
        candidates = [(j+.5)*window/rate for j,e in enumerate(energy)
                      if e <= quiet and low < (j+.5)*window/rate < high]
        boundary = min(candidates, key=lambda t: abs(t-target)) if candidates else target
        boundary = max(boundaries[-1]+1/rate, min(boundary, duration-(len(lines)-i)/rate))
        boundaries.append(boundary)
    boundaries.append(duration)
    return [dict(id=line['id'], start=boundaries[i], end=boundaries[i+1])
            for i,line in enumerate(lines)]
