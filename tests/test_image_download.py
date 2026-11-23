import hashlib
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest


ASSETS = Path(__file__).resolve().parents[1] / 'app' / 'raghu_assets'


def load_downloader():
    spec = importlib.util.spec_from_file_location('image_download_test', ASSETS / 'image_download.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}

    def read1(self, size=-1):
        return self.read(size)


def item(data=b'correct model'):
    return dict(file='transformer/model.safetensors', bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest())


def manager(tmp_path, monkeypatch, opener):
    m = load_downloader()
    monkeypatch.setattr(m, 'urlopen', opener)
    return m, m.Downloader(tmp_path, 'private-token', lambda *a, **k: None, retry_wait=0)


def test_authenticated_interrupted_download_resumes_verified_prefix(tmp_path, monkeypatch):
    data = b'correct model'
    calls = []

    class Interrupted(Response):
        def read1(self, size=-1):
            if self.tell():
                raise TimeoutError('upstream secret URL must not leak')
            return self.read(4)

    def open_(request, timeout):
        calls.append(dict(request.header_items()))
        assert timeout <= 20
        if len(calls) == 1:
            return Interrupted(data)
        assert request.get_header('Range') == 'bytes=4-'
        return Response(data[4:], 206, {'Content-Range': f'bytes 4-{len(data)-1}/{len(data)}'})

    m, d = manager(tmp_path, monkeypatch, open_)
    p = d.download(item(data))
    assert p.read_bytes() == data
    assert len(calls) == 2
    assert all(c['Authorization'] == 'Bearer private-token' for c in calls)
    status = json.loads((tmp_path / 'results/image-download-progress.json').read_text())
    assert 'private-token' not in json.dumps(status)
    assert status['files']['transformer/model.safetensors']['bytes'] == len(data)
    assert any(r['stage'] == 'image_download_retry' for r in d.events)


def test_range_ignored_restarts_without_appending_duplicate_bytes(tmp_path, monkeypatch):
    data = b'correct model'
    m, d = manager(tmp_path, monkeypatch, lambda r, timeout: Response(data))
    partial = tmp_path / 'models/image/transformer/model.safetensors.partial'
    partial.parent.mkdir(parents=True)
    partial.write_bytes(data[:4])
    assert d.download(item(data)).read_bytes() == data


def test_hash_mismatch_retried_and_never_accepted_as_cache(tmp_path, monkeypatch):
    data = b'correct model'
    replies = iter([b'x' * len(data), data])
    m, d = manager(tmp_path, monkeypatch, lambda r, timeout: Response(next(replies)))
    assert d.download(item(data)).read_bytes() == data
    assert len([e for e in d.events if e['stage'] == 'image_download_retry']) == 1


def test_exhausted_timeout_preserves_partial_and_redacts_error(tmp_path, monkeypatch):
    calls = []
    def open_(r, timeout):
        calls.append(r)
        raise TimeoutError('private-token https://signed-secret')
    m, d = manager(tmp_path, monkeypatch, open_)
    partial = tmp_path / 'models/image/transformer/model.safetensors.partial'
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b'corr')
    with pytest.raises(RuntimeError, match='saved.*Retry') as caught:
        d.download(item())
    assert len(calls) == 3
    assert partial.read_bytes() == b'corr'
    assert 'private-token' not in str(caught.value)
    assert 'signed-secret' not in str(caught.value)


def test_cached_model_is_not_redownloaded_but_modified_file_is_repaired(tmp_path, monkeypatch):
    data = b'correct model'
    calls = []
    def open_(r, timeout):
        calls.append(r)
        return Response(data)
    m, d = manager(tmp_path, monkeypatch, open_)
    p = d.download(item(data))
    d.download(item(data))
    assert len(calls) == 1
    p.write_bytes(b'x' * len(data))
    assert d.download(item(data)).read_bytes() == data
    assert len(calls) == 2


def test_invalid_credentials_do_not_retry_or_leak_token(tmp_path, monkeypatch):
    calls = []
    def open_(r, timeout):
        calls.append(r)
        raise HTTPError(r.full_url, 401, 'private-token', {}, None)
    m, d = manager(tmp_path, monkeypatch, open_)
    with pytest.raises(RuntimeError, match='Hugging Face.*access'):
        d.download(item())
    assert len(calls) == 1


def test_slow_dribbling_connection_is_retried(tmp_path, monkeypatch):
    data = b'correct model'
    calls = []
    def open_(r, timeout):
        calls.append(r)
        return Response(data)
    m, d = manager(tmp_path, monkeypatch, open_)
    ticks = iter([0, 61, 62, 62, 62, 62, 62, 62, 62, 62, 62])
    monkeypatch.setattr(m.time, 'monotonic', lambda: next(ticks, 62))
    assert d.download(item(data)).read_bytes() == data
    assert len(calls) >= 2


def test_manifest_keeps_pinned_revision_and_upstream_hashes(tmp_path, monkeypatch):
    m = load_downloader()
    data = b'correct model'
    sibling = dict(rfilename='transformer/model.safetensors', size=len(data),
                   lfs={'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
    def open_(r, timeout):
        assert m.REVISION in r.full_url
        assert r.get_header('Authorization') == 'Bearer private-token'
        return Response(json.dumps({'siblings': [sibling, {'rfilename': '../escape', 'size': 1}]}).encode())
    monkeypatch.setattr(m, 'urlopen', open_)
    d = m.Downloader(tmp_path, 'private-token', lambda *a, **k: None, retry_wait=0)
    assert d.manifest() == [item(data)]


def test_worker_uses_verified_downloader_and_ships_it_on_resume():
    assert 'prepare_flux' in (ASSETS / 'stills.py').read_text()
    assert 'token=False' not in (ASSETS / 'stills.py').read_text()
    vast = (ASSETS.parent / 'raghu_vast.py').read_text()
    assert vast.count('"image_download.py"') >= 2


def test_wrong_range_never_appends_to_saved_prefix(tmp_path, monkeypatch):
    data = b'correct model'
    calls = []
    def open_(r, timeout):
        calls.append(r)
        if len(calls) == 1:
            return Response(data, 206, {'Content-Range': f'bytes 0-{len(data)-1}/{len(data)}'})
        return Response(data[4:], 206, {'Content-Range': f'bytes 4-{len(data)-1}/{len(data)}'})
    m, d = manager(tmp_path, monkeypatch, open_)
    partial = tmp_path / 'models/image/transformer/model.safetensors.partial'
    partial.parent.mkdir(parents=True)
    partial.write_bytes(data[:4])
    assert d.download(item(data)).read_bytes() == data
    assert len(calls) == 2


def test_token_is_read_privately_from_worker_env(tmp_path, monkeypatch):
    m = load_downloader()
    monkeypatch.delenv('HF_TOKEN', raising=False)
    (tmp_path / '.env').write_text('HF_TOKEN="worker-token"\n')
    assert m.huggingface_token(tmp_path) == 'worker-token'
    monkeypatch.setenv('HF_TOKEN', 'environment-token')
    assert m.huggingface_token(tmp_path) == 'environment-token'


def test_redirect_does_not_forward_token_to_cdn():
    from urllib.request import Request
    m = load_downloader()
    request = Request('https://huggingface.co/model', headers={'Authorization': 'Bearer private-token'})
    redirected = m.SafeRedirect().redirect_request(request, None, 302, '', {}, 'https://cdn.example/model?signed=yes')
    assert redirected.get_header('Authorization') is None


def test_git_blob_integrity_for_small_config_files(tmp_path, monkeypatch):
    data = b'{"config": 1}'
    record = dict(file='scheduler/config.json', bytes=len(data),
                  git_sha1=hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest())
    m, d = manager(tmp_path, monkeypatch, lambda r, timeout: Response(data))
    assert d.download(record).read_bytes() == data


def test_telegram_download_progress_is_meaningful():
    from app.raghu_vast import image_download_message
    status = {'files': {'transformer/model': {'bytes': 2_000_000_000, 'total': 8_000_000_000,
                                             'phase': 'downloading', 'attempt': 2}}}
    message = image_download_message(status)
    assert '2.00/8.00 GB' in message
    assert 'retry 2/3' in message
    assert image_download_message({'files': {}}) is None


def test_real_http_truncation_recovers_without_changing_model_bytes(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from urllib.request import Request, urlopen
    data = b'approved-model-bytes' * 10000
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.headers.get('Range'), self.headers.get('Authorization')))
            start = 0 if not self.headers.get('Range') else int(self.headers['Range'][6:-1])
            self.send_response(206 if start else 200)
            self.send_header('Content-Length', str(len(data) - start))
            if start:
                self.send_header('Content-Range', f'bytes {start}-{len(data)-1}/{len(data)}')
            self.end_headers()
            self.wfile.write(data[start:] if start else data[:1000])
            self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def open_(request, timeout):
            return urlopen(Request(f'http://127.0.0.1:{server.server_port}/model',
                                   headers=dict(request.header_items())), timeout=timeout)
        m, d = manager(tmp_path, monkeypatch, open_)
        assert d.download(item(data)).read_bytes() == data
        # TCP can deliver the resumed body in several partial reads on Windows.
        # Verify range progress and auth, not an OS-dependent exact request count.
        assert requests[:2] == [(None, 'Bearer private-token'), ('bytes=1000-', 'Bearer private-token')]
        offsets = [int(header[6:-1]) for header, _ in requests[1:]]
        assert offsets == sorted(set(offsets))
        assert all(0 < offset < len(data) for offset in offsets)
        assert all(auth == 'Bearer private-token' for _, auth in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_render_failure_surfaces_download_reason(tmp_path):
    from app.raghu_vast import Remote
    class Failure:
        def result(self):
            raise RuntimeError('Vast ssh operation failed.')
    remote = Remote({}, None, tmp_path, float('inf'), lambda: None, connecting=False)
    def pull(names, folder):
        (folder / names[0]).write_text(json.dumps({'error': 'Image model download failed after 3 attempts. Partial files are saved; use Retry this step.'}))
    remote.pull = pull
    with pytest.raises(RuntimeError, match='Image model download failed after 3 attempts'):
        remote.still_worker_result(Failure(), tmp_path)


@pytest.mark.parametrize('model,image_only', [('ltx', True), ('wan22', False), ('hunyuan15', False), ('ltx', False)])
def test_worker_receives_configured_token_in_every_generation_mode(monkeypatch, model, image_only):
    from app import raghu_vast
    monkeypatch.setenv('HF_TOKEN', 'configured-worker-token')
    assert raghu_vast.worker_hf_token(model, image_only) == 'configured-worker-token'
