"""Bounded, resumable downloads for the approved pinned FLUX model.

Only transfer/recovery changes live here; inference settings stay in stills.py.
"""
import concurrent.futures
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

REPO = 'black-forest-labs/FLUX.2-klein-4B'
REVISION = 'e7b7dc27f91deacad38e78976d1f2b499d76a294'
READ_TIMEOUT = 20
STALL_SECONDS = 60
PROGRESS_BYTES = 1024 * 1024


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https':
            raise RuntimeError('Image model download refused an insecure redirect.')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
        return redirected


def urlopen(request, timeout):
    return build_opener(SafeRedirect()).open(request, timeout=timeout)


def huggingface_token(root):
    token = os.environ.get('HF_TOKEN', '').strip()
    if token:
        return token
    env = Path(root) / '.env'
    if env.is_file():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() == 'HF_TOKEN':
                return value.strip().strip('\"\'')
    return None


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    temp.replace(path)


class Downloader:
    def __init__(self, root, token, notify, *, retry_wait=2):
        self.root, self.token, self.notify = Path(root), token, notify
        self.directory = self.root / 'models/image'
        self.status = self.root / 'results/image-download-progress.json'
        self.retry_wait = retry_wait
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.files, self.events = {}, []

    def report(self, name, size, received, phase, attempt=1):
        with self.lock:
            self.files[name] = dict(total=size, bytes=received, phase=phase, attempt=attempt)
            atomic_json(self.status, dict(time=time.time(), revision=REVISION, files=self.files))

    def event(self, stage, **data):
        self.events.append(dict(stage=stage, **data))
        self.notify(stage, **data)

    def request(self, url, offset=0):
        headers = {'Accept-Encoding': 'identity', 'User-Agent': 'horror-image-downloader/1'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if offset:
            headers['Range'] = f'bytes={offset}-'
        return urlopen(Request(url, headers=headers), timeout=READ_TIMEOUT)

    @staticmethod
    def access_error(exc):
        if isinstance(exc, HTTPError) and exc.code in (401, 403):
            raise RuntimeError('Hugging Face image model access failed. Check HF_TOKEN and repository access; saved files are preserved.') from None

    def manifest(self):
        url = f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true'
        for attempt in range(1, 4):
            try:
                with self.request(url) as response:
                    chunks, received = [], 0
                    started = time.monotonic()
                    while True:
                        chunk = response.read1(64 * 1024)
                        if time.monotonic() - started > STALL_SECONDS:
                            raise TimeoutError('Image manifest response timed out')
                        if not chunk:
                            break
                        chunks.append(chunk)
                        received += len(chunk)
                        if received > 4 * 1024 * 1024:
                            raise ValueError('Oversized image manifest')
                    body = b''.join(chunks)
                records = []
                for row in json.loads(body)['siblings']:
                    name = row['rfilename']
                    parts = PurePosixPath(name).parts
                    if ('..' in parts or name.startswith('/') or '\\' in name or
                            not (name == 'model_index.json' or (len(parts) > 1 and parts[0] in
                                 ('scheduler', 'tokenizer', 'text_encoder', 'transformer', 'vae')))):
                        continue
                    lfs = row.get('lfs') or {}
                    size = row.get('size', lfs.get('size'))
                    sha256 = lfs.get('sha256')
                    sha1 = row.get('blobId') if not sha256 else None
                    if not isinstance(size, int) or size < 0 or not re.fullmatch(
                            r'[0-9a-f]{64}' if sha256 else r'[0-9a-f]{40}', sha256 or sha1 or ''):
                        raise ValueError('Missing upstream file integrity metadata')
                    record = dict(file=name, bytes=size)
                    record['sha256' if sha256 else 'git_sha1'] = sha256 or sha1
                    records.append(record)
                if not records:
                    raise ValueError('Empty FLUX manifest')
                return records
            except (OSError, URLError, HTTPException, ValueError, KeyError) as exc:
                self.access_error(exc)
                if attempt == 3:
                    raise RuntimeError('Image model metadata unavailable after 3 attempts. Files are saved; use Retry this step.') from None
                self.event('image_download_retry', file='manifest', attempt=attempt, reason=type(exc).__name__)
                time.sleep(self.retry_wait)

    @staticmethod
    def valid(path, record):
        if not path.is_file() or path.stat().st_size != record['bytes']:
            return False
        with path.open('rb') as stream:
            if 'sha256' in record:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                return digest == record['sha256']
            digest = hashlib.sha1(f"blob {record['bytes']}\0".encode())
            for chunk in iter(lambda: stream.read(PROGRESS_BYTES), b''):
                digest.update(chunk)
            return digest.hexdigest() == record['git_sha1']

    def download(self, record):
        name, size = record['file'], record['bytes']
        target = self.directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + '.partial')
        stamp = target.with_suffix(target.suffix + '.verified.json')
        fingerprint = dict(revision=REVISION, record=record)
        if target.is_file():
            stat = target.stat()
            fingerprint.update(size=stat.st_size, mtime=stat.st_mtime_ns, ctime=stat.st_ctime_ns)
            try:
                cached = json.loads(stamp.read_text()) == fingerprint
            except (OSError, ValueError):
                cached = False
            if cached or self.valid(target, record):
                atomic_json(stamp, fingerprint)
                self.report(name, size, size, 'verified')
                return target
            # Preserve a failed full file separately; never treat it as a resumable prefix.
            target.replace(target.with_suffix(target.suffix + '.invalid'))
        for attempt in range(1, 4):
            try:
                if self.cancelled.is_set():
                    raise RuntimeError('Image model transfer stopped because another file failed; partial files are saved.')
                offset = partial.stat().st_size if partial.is_file() else 0
                if offset >= size and partial.exists():
                    if self.valid(partial, record):
                        return self.finish(partial, target, stamp, record)
                    partial.unlink()
                    offset = 0
                self.report(name, size, offset, 'downloading', attempt)
                url = f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}'
                with self.request(url, offset) as response:
                    if response.status == 206:
                        content_range = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                        if not content_range or tuple(map(int, content_range.groups())) != (offset, size - 1, size):
                            raise ValueError('Unexpected download range')
                    elif response.status == 200:
                        offset = 0  # Server ignored Range: overwrite rather than append.
                    else:
                        raise ValueError('Unexpected download response')
                    last_progress = time.monotonic()
                    last_report = last_progress
                    milestone = offset
                    with partial.open('ab' if offset else 'wb') as stream:
                        while True:
                            if self.cancelled.is_set():
                                raise RuntimeError('Image model transfer stopped because another file failed; partial files are saved.')
                            chunk = response.read1(PROGRESS_BYTES)
                            now = time.monotonic()
                            if now - last_progress >= STALL_SECONDS:
                                raise TimeoutError('Image model transfer stopped making progress')
                            if not chunk:
                                break
                            stream.write(chunk)
                            offset += len(chunk)
                            if offset > size:
                                raise ValueError('Oversized model response')
                            if offset - milestone >= PROGRESS_BYTES:
                                last_progress, milestone = now, offset
                            if now - last_report >= 5:
                                self.report(name, size, offset, 'downloading', attempt)
                                last_report = now
                self.report(name, size, offset, 'verifying', attempt)
                if not self.valid(partial, record):
                    # A truncated prefix can be resumed. Full-length corruption cannot.
                    if partial.stat().st_size >= size:
                        partial.unlink()
                    raise ValueError('Model file integrity check failed')
                return self.finish(partial, target, stamp, record)
            except (OSError, URLError, HTTPException, ValueError) as exc:
                self.access_error(exc)
                self.event('image_download_retry', file=name, attempt=attempt, reason=type(exc).__name__)
                if attempt == 3:
                    self.report(name, size, partial.stat().st_size if partial.exists() else 0, 'failed', attempt)
                    raise RuntimeError(f'Image model download failed after 3 attempts: {name}. Partial files are saved; use Retry this step.') from None
                time.sleep(self.retry_wait)

    def finish(self, partial, target, stamp, record):
        partial.replace(target)
        stat = target.stat()
        atomic_json(stamp, dict(revision=REVISION, record=record, size=stat.st_size,
                               mtime=stat.st_mtime_ns, ctime=stat.st_ctime_ns))
        self.report(record['file'], record['bytes'], record['bytes'], 'verified')
        self.event('image_download_verified', file=record['file'], bytes=record['bytes'])
        return target


def prepare_flux(root, notify):
    downloader = Downloader(root, huggingface_token(root), notify)
    # Reset stale progress before querying the pinned upstream manifest.
    downloader.report('manifest', 0, 0, 'checking')
    try:
        records = downloader.manifest()
        for record in records:
            downloader.report(record['file'], record['bytes'], 0, 'pending')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(downloader.download, record) for record in records]
            try:
                for future in concurrent.futures.as_completed(futures):
                    future.result()
            except Exception:
                downloader.cancelled.set()
                for future in futures:
                    future.cancel()
                raise
    except Exception as exc:
        # Controlled downloader messages omit credentials and signed CDN URLs.
        message = str(exc) if isinstance(exc, RuntimeError) and str(exc).startswith(
            ('Image model ', 'Hugging Face image ')) else (
            'Image model setup failed. Partial files are saved; use Retry this step.')
        atomic_json(downloader.status, dict(time=time.time(), revision=REVISION,
                                           files=downloader.files, error=message))
        notify('image_download_failed', reason=message)
        raise RuntimeError(message) from None
    downloader.report('manifest', 0, 0, 'ready')
    notify('image_model_ready', revision=REVISION)
    return downloader.directory
