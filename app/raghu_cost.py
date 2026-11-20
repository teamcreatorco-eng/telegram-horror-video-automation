"""Conservative rental accounting. Never changes media generation settings."""
import hashlib
import json
import math


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Expected a finite non-negative number')
    return value


def estimate_cost(rate, clips, seconds_per_clip, overhead_seconds, download_gb,
                  download_rate, upload_gb, upload_rate, other_usd=0):
    for v in (rate, clips, seconds_per_clip, overhead_seconds, download_gb, download_rate,
              upload_gb, upload_rate, other_usd):
        number(v)
    if type(clips) is not int:
        raise ValueError('Clip count must be an integer')
    motion = clips * seconds_per_clip * rate / 3600
    setup = overhead_seconds * rate / 3600
    transfers = download_gb * download_rate + upload_gb * upload_rate
    total = motion + setup + transfers + other_usd
    return dict(motion_usd=motion, overhead_usd=setup, transfers_usd=transfers,
                other_usd=other_usd, total_usd=total, total_inr=total * 96)


def workload_key(manifest, worker_bytes, quantization, motion_model):
    payload = json.dumps(dict(models=manifest, quantization=quantization, motion=motion_model,
                              worker=hashlib.sha256(worker_bytes).hexdigest()), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def summarize_events(events):
    starts, done, intervals = {}, {}, []
    sessions = 0
    valid = True
    for row in events:
        stage = row.get('stage')
        if stage == 'video_model_ready':
            sessions += 1
        elif stage == 'video_start':
            ident = row['id']
            if ident in starts or row.get('num_frames') != 121 or row.get('fps') != 24:
                valid = False
            starts[ident] = row
        elif stage == 'video_done':
            ident = row['id']
            if ident in done or number(row['seconds']) <= 0:
                valid = False
            done[ident] = row['seconds']
        elif stage == 'model_download':
            end, duration = number(row['time']), number(row['seconds'])
            if end < duration:
                raise ValueError('Download interval starts before zero')
            intervals.append((end - duration, end))
    wall, last = 0., None
    for start, end in sorted(intervals):
        wall += end - max(start, last if last is not None else start) if last is None or end > last else 0
        last = max(last or 0, end)
    values = list(done.values())
    warm = sorted(values[1:])
    return dict(clips=len(values), warm_samples=len(warm), first_seconds=values[0] if values else None,
                warm_p90_seconds=warm[math.ceil(.9 * len(warm)) - 1] if warm else None,
                render_seconds=sum(values), download_wall_seconds=wall,
                qualifiable=bool(valid and sessions == 1 and set(starts) == set(done) and len(warm) >= 3))
