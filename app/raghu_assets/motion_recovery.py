"""Recover completed motion without importing or changing the generation model."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def target_file(results, shot):
    if not re.fullmatch(r'\d{3}', shot['id']):
        raise ValueError('Invalid motion ID')
    return results / f"shot-{shot['id']}.mp4"


def marker_file(results, shot):
    return target_file(results, shot).with_suffix('.complete.json')


def video_readable(path):
    try:
        result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)],
                              capture_output=True,text=True,timeout=30)
        return result.returncode==0 and float(json.loads(result.stdout)['format']['duration'])>0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False


def mark_complete(results, shot):
    if not shot.get('recovery_key'): return
    target=target_file(results,shot)
    marker=marker_file(results,shot);partial=marker.with_suffix('.partial.json')
    partial.write_text(json.dumps(dict(key=shot['recovery_key'],sha256=hashlib.sha256(target.read_bytes()).hexdigest())),encoding='utf-8')
    partial.replace(marker)


def reusable(results, shot):
    try:
        marker=json.loads(marker_file(results,shot).read_text(encoding='utf-8'))
        target=target_file(results,shot)
        return (marker['key']==shot['recovery_key'] and marker['sha256']==hashlib.sha256(target.read_bytes()).hexdigest()
                and video_readable(target))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def legacy_result(root, shot):
    """Old workers logged input and completed-output hashes; both must match."""
    results=root/'results'; target=target_file(results,shot)
    if not target.is_file() or not shot.get('legacy_recovery'): return False
    expected=shot['legacy_recovery'];started=None
    try:
        for line in (results/'events.jsonl').read_text(encoding='utf-8').splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if row.get('id')!=shot['id']:continue
            if row.get('stage')=='video_start': started=row
            if row.get('stage')=='video_done' and started:
                matching=all(started.get(k)==v for k,v in expected.items())
                if matching and row.get('sha256')==hashlib.sha256(target.read_bytes()).hexdigest() and video_readable(target):
                    return True
    except OSError:pass
    return False


def prepare(root, request):
    results=root/'results'
    for shot in json.loads(request.read_text(encoding='utf-8'))['shots']:
        if reusable(results,shot):continue
        if legacy_result(root,shot):
            mark_complete(results,shot)
            continue
        target_file(results,shot).unlink(missing_ok=True)
        marker_file(results,shot).unlink(missing_ok=True)


def park_requests(root):
    archive=root/'completed-motion-requests';archive.mkdir(exist_ok=True)
    for path in root.glob('motion-request-*.json'):
        path.replace(archive/path.name)


if __name__=='__main__':
    if sys.argv[1:] == ['--park']:
        park_requests(Path.cwd())
    else:
        prepare(Path.cwd(),Path(sys.argv[1]))
