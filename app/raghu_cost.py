"""Conservative rental accounting. Never changes media generation settings."""
import hashlib
import json
import math

HOST_FIELDS = ('machine_id', 'gpu_name', 'cpu_name', 'cpu_cores_effective', 'cpu_ram', 'gpu_ram', 'pcie_bw')


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


def host_signature(offer):
    if any(offer.get(k) is None for k in HOST_FIELDS):
        return None
    return hashlib.sha256(json.dumps({k: offer[k] for k in HOST_FIELDS}, sort_keys=True).encode()).hexdigest()


def _observation(records, offer, key, now):
    signature = host_signature(offer)
    usable = []
    if signature is None:
        return None
    for row in records:
        try:
            if (row['host_signature'] != signature or row['workload_key'] != key
                    or not 0 <= now - number(row['measured_at']) <= 7 * 86400
                    or type(row['warm_samples']) is not int or row['warm_samples'] < 3
                    or number(row['warm_p90_seconds']) <= 0 or number(row['first_seconds']) <= 0):
                continue
            usable.append(row)
        except (TypeError, KeyError, ValueError):
            continue
    return max(usable, key=lambda r: r['measured_at']) if usable else None


def measured_seconds(records, offer, key, now):
    row = _observation(records, offer, key, now)
    return row['warm_p90_seconds'] if row else None


def offer_cost(offer, records, key, clips, overhead_seconds, download_gb, upload_gb, now):
    row = _observation(records, offer, key, now)
    seconds = row['warm_p90_seconds'] if row else 70
    first_extra = max(0, row['first_seconds'] - seconds) if row and clips else 0
    result = estimate_cost(float(offer['dph_total']), clips, seconds, overhead_seconds + first_extra,
                           download_gb, float(offer['inet_down_cost']),
                           upload_gb, float(offer['inet_up_cost']))
    return dict(result, seconds_per_clip=seconds, first_clip_extra_seconds=first_extra,
                speed_basis='measured_host_warm_p90' if row else 'unmeasured_estimate',
                exclusions=['writing/API fees', 'Railway editing/hosting allocation', 'taxes',
                            'unplanned retries', 'post-stop storage'])
