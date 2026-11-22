import importlib.util
from pathlib import Path
from types import SimpleNamespace


ASSETS = Path(__file__).resolve().parents[1] / 'app/raghu_assets'


def runtime():
    spec = importlib.util.spec_from_file_location('motion_runtime_test', ASSETS / 'motion_runtime.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installs_matching_binary_without_replacing_torch(monkeypatch):
    m = runtime()
    commands, checks, events = [], [], []
    monkeypatch.setattr(m.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(m.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(m.sys, 'version_info', (3, 12, 2))
    torch = SimpleNamespace(__version__='2.10.0+cu130', version=SimpleNamespace(cuda='13.0'))
    monkeypatch.setattr(m, 'torch_module', lambda: torch)
    def smoke():
        checks.append(True)
        if len(checks) == 1:
            raise ImportError('missing natten')
    monkeypatch.setattr(m, 'check_natten', smoke)
    monkeypatch.setattr(m.subprocess, 'run', lambda args, **kwargs: commands.append((args, kwargs)))
    assert m.prepare(lambda stage, **data: events.append((stage, data)))
    args, options = commands[0]
    assert '--no-deps' in args
    assert '--only-binary=:all:' in args
    assert 'torch2100cu130-cp312-cp312-linux_x86_64.whl' in args[-1]
    assert '#sha256=' in args[-1]
    assert options['timeout'] <= 180
    assert len(checks) == 2
    assert events[-1][1]['accelerated_decode'] is True


def test_existing_working_backend_is_reused_without_install(monkeypatch):
    m = runtime()
    monkeypatch.setattr(m, 'check_natten', lambda: None)
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unnecessary installation')))
    assert m.prepare(lambda *a, **k: None)


def test_failed_binary_install_reports_fallback_without_breaking_generation(monkeypatch):
    m = runtime()
    monkeypatch.setattr(m, 'check_natten', lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(m.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(m.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(m.sys, 'version_info', (3, 12, 2))
    monkeypatch.setattr(m, 'torch_module', lambda: SimpleNamespace(__version__='2.10.0+cu130', version=SimpleNamespace(cuda='13.0')))
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(m.subprocess.TimeoutExpired('pip', 180)))
    events = []
    assert not m.prepare(lambda stage, **data: events.append((stage, data)))
    assert events[-1][1]['accelerated_decode'] is False


def test_unsupported_torch_is_not_upgraded(monkeypatch):
    m = runtime()
    monkeypatch.setattr(m, 'check_natten', lambda: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(m, 'torch_module', lambda: SimpleNamespace(__version__='2.13.0+cu132', version=SimpleNamespace(cuda='13.2')))
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not install mismatched wheel')))
    assert not m.prepare(lambda *a, **k: None)


def test_profiler_preserves_lazy_decode_and_attributes():
    m = runtime()
    events, consumed = [], []
    class Block:
        marker = 'unchanged'
        def __call__(self):
            def chunks():
                for i in range(2):
                    consumed.append(i)
                    yield i
            return chunks()
    wrapped = m.TimedBlock(Block(), 'video_decode', lambda stage, **data: events.append((stage, data)), lambda: None, lambda: '001')
    output = wrapped()
    assert consumed == []
    assert wrapped.marker == 'unchanged'
    assert list(output) == [0, 1]
    assert any(data['component'] == 'video_decode' and data.get('lazy') for stage, data in events)
    assert all(data['id'] == '001' for stage, data in events)


def test_profiler_does_not_swallow_component_failures():
    import pytest
    m = runtime()
    def failed():
        raise RuntimeError('original failure')
    events = []
    wrapped = m.TimedBlock(failed, 'denoise', lambda stage, **data: events.append(data), lambda: None, lambda: '002')
    with pytest.raises(RuntimeError, match='original failure'):
        wrapped()
    assert events[-1]['success'] is False


def test_worker_prepares_backend_even_on_cached_video_environment():
    shell = (ASSETS / 'worker.sh').read_text()
    assert 'video-env/bin/python motion_runtime.py' in shell
    assert shell.index('video-env/bin/python motion_runtime.py') > shell.index('touch video-env/.ready')
    vast = (ASSETS.parent / 'raghu_vast.py').read_text()
    assert vast.count('"motion_runtime.py"') >= 2


def test_instrumentation_keeps_arguments_and_return_objects_unchanged(monkeypatch):
    m = runtime()
    synced, calls = [], []
    output = object()
    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return output
    pipe = SimpleNamespace(prompt_encoder=original)
    monkeypatch.setattr(m, 'torch_module', lambda: SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda: synced.append(True))))
    m.instrument_pipeline(pipe, lambda *a, **k: None, lambda: '003')
    assert pipe.prompt_encoder('same prompt', seed=55403, enhance_first_prompt=False) is output
    assert calls == [(('same prompt',), {'seed': 55403, 'enhance_first_prompt': False})]
    assert len(synced) == 2


def test_failed_kernel_is_disabled_in_actual_render_process(monkeypatch, tmp_path):
    m = runtime()
    monkeypatch.setitem(m.sys.modules, 'natten', SimpleNamespace())
    monkeypatch.setattr(m, 'BACKEND_STATUS', tmp_path / 'backend.json')
    m.BACKEND_STATUS.write_text('{"accelerated_decode": false}')
    monkeypatch.setattr(m, 'check_natten', lambda: (_ for _ in ()).throw(AssertionError('must not run GPU checks inside render process')))
    events = []
    assert not m.configure_backend(lambda stage, **data: events.append(data))
    assert m.sys.modules['natten'] is None
    assert events[-1]['accelerated_decode'] is False
    worker = (ASSETS / 'videos.py').read_text()
    assert worker.index('configure_backend(event)') < worker.index('from ltx_core')


def test_successful_isolated_backend_check_keeps_natten_enabled(monkeypatch, tmp_path):
    m = runtime()
    natten = SimpleNamespace()
    monkeypatch.setitem(m.sys.modules, 'natten', natten)
    monkeypatch.setattr(m, 'BACKEND_STATUS', tmp_path / 'backend.json')
    m.BACKEND_STATUS.write_text('{"accelerated_decode": true}')
    assert m.configure_backend(lambda *a, **k: None)
    assert m.sys.modules['natten'] is natten
