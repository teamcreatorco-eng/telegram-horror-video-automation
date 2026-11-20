"""Conservative rental accounting. Never changes media generation settings."""
import hashlib
import json
import math
import pathlib
import uuid

HOST_FIELDS = ('machine_id', 'gpu_name', 'cpu_name', 'cpu_cores_effective', 'cpu_ram', 'gpu_ram', 'pcie_bw')


def number(value):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
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
    last_event_time = -1
    for row in events:
        stage = row.get('stage')
        if stage in ('video_model_ready', 'video_start', 'video_done'):
            timestamp = row.get('time')
            if timestamp is None:
                valid = False
            else:
                number(timestamp)
                if timestamp < last_event_time:
                    valid = False
                last_event_time = timestamp
        if stage == 'video_model_ready':
            sessions += 1
        elif stage == 'video_start':
            ident = row['id']
            if ident in starts or ident in done or sessions != 1 or row.get('num_frames') != 121 or row.get('fps') != 24:
                valid = False
            starts[ident] = row
        elif stage == 'video_done':
            ident = row['id']
            if ident in done or ident not in starts or number(row['seconds']) <= 0:
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


def host_metadata(offer):
    return {key: offer.get(key) for key in HOST_FIELDS}


def _observation(records, offer, key, now):
    signature = host_signature(offer)
    usable = []
    if signature is None:
        return None
    for row in records:
        if not isinstance(row, dict):
            continue
        try:
            if (row.get('qualifiable') is not True or row['host_signature'] != signature or row['workload_key'] != key
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


def load_observations(directory):
    records = []
    for path in pathlib.Path(directory).glob('*.json'):
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(row, dict):
                records.append(row)
        except (OSError, ValueError):
            continue
    return records


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def record_rental_cost(folder, catalog_path, now):
    """Best-effort observations, never a provider invoice. Callers isolate failures."""
    folder = pathlib.Path(folder)
    state = json.loads((folder / 'raghu-rental.json').read_text(encoding='utf-8'))
    if not state.get('workload_key') or not state.get('host_signature') or state.get('motion_model') != 'ltx':
        return None
    name = hashlib.sha256(str(state.get('id')).encode()).hexdigest() + '.json'
    catalog_file = pathlib.Path(catalog_path) / name
    # Retract the old observation before parsing: corrupt or partial resumed logs
    # must not leave a previous fast qualification in circulation.
    catalog_file.unlink(missing_ok=True)
    rows = [json.loads(line) for line in (folder / 'events.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    missing_time = any(row.get('time') is None for row in rows if row.get('stage') in ('video_model_ready','video_start','video_done'))
    rows = [row for row in rows if row.get('time') is not None and number(row['time']) >= state['started']]
    summary = summarize_events(rows)
    ready = [row for row in rows if row.get('stage') == 'video_model_ready']
    compatible = all(row.get('resolution') == [1280,768] and row.get('steps') == [8,3]
                     and row.get('quantization') == state.get('quantization') for row in ready)
    summary['qualifiable'] = summary['qualifiable'] and compatible and not missing_time
    measured_at = max((row['time'] for row in rows if row.get('stage') == 'video_done'), default=0)
    elapsed = max(0, number(now) - number(state['started']))
    result = dict(summary, measured_at=measured_at, host_signature=state['host_signature'],
                  host_hardware=state.get('host_hardware', {}),
                  workload_key=state['workload_key'], rental_id=state.get('id'),
                  elapsed_wall_seconds=elapsed, elapsed_compute_estimate_usd=elapsed * number(state['rate']) / 3600,
                  invoice_verified=False, estimate_basis='wall elapsed times listed rental rate; may include stopped time',
                  exclusions=['transfer invoice', 'storage', 'taxes', 'writing/API fees', 'Railway allocation'])
    _save(folder / 'rental-cost-observation.json', result)
    if summary['qualifiable'] and 0 <= now - measured_at <= 7 * 86400:
        _save(catalog_file, result)
    return result
