"""Restore the supported fast DiffVAE backend and record actual stage timings.

No model, sampler, precision, resolution, prompt or frame changes are made here.
"""
from collections.abc import Iterator
import importlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

BACKEND_STATUS = Path(__file__).resolve().parent / 'results' / 'video-decode-backend.json'


NATTEN_WHEEL = (
    'https://github.com/SHI-Labs/NATTEN/releases/download/v0.21.5/'
    'natten-0.21.5%2Btorch2100cu130-cp312-cp312-linux_x86_64.whl'
    '#sha256=9a1afd653e218f6bed3b0c8c39e14455af707952e71cd5df91b5ac71fc187cde'
)


def torch_module():
    return importlib.import_module('torch')


def check_natten():
    """Exercise a real neighborhood kernel, not just a successful Python import."""
    torch = torch_module()
    natten = importlib.import_module('natten')
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA unavailable for NATTEN validation')
    with torch.inference_mode():
        q = torch.zeros((1, 4, 4, 4, 1, 64), device='cuda', dtype=torch.bfloat16)
        result = natten.na3d(q, q, q, kernel_size=(3, 3, 3), scale=1.0, backend='cutlass-fna')
        torch.cuda.synchronize()
        if result.shape != q.shape or not torch.isfinite(result).all().item():
            raise RuntimeError('NATTEN kernel validation failed')


def prepare(notify):
    try:
        check_natten()
        notify('video_decode_backend', accelerated_decode=True, backend='natten', reused=True)
        return True
    except Exception:
        pass
    try:
        torch = torch_module()
        supported = (platform.system() == 'Linux' and platform.machine() == 'x86_64'
                     and sys.version_info[:2] == (3, 12)
                     and str(torch.__version__).split('+')[0] == '2.10.0'
                     and torch.version.cuda == '13.0')
        if not supported:
            notify('video_decode_backend', accelerated_decode=False,
                   reason='No pinned NATTEN wheel for this runtime; existing fallback retained')
            return False
        notify('video_decode_backend_install', backend='natten', version='0.21.5+torch2100cu130')
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--no-deps', '--only-binary=:all:',
             '--retries', '2', '--timeout', '20', NATTEN_WHEEL],
            check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        importlib.invalidate_caches()
        check_natten()
        notify('video_decode_backend', accelerated_decode=True, backend='natten', reused=False)
        return True
    except Exception as exc:
        # An optional acceleration dependency must not break an otherwise valid production.
        notify('video_decode_backend', accelerated_decode=False, backend='existing_fallback',
               reason=type(exc).__name__, message='NATTEN acceleration unavailable; render speed is unverified')
        return False


class TimedIterator:
    """Time only iterator work, excluding the consumer's work between chunks."""
    def __init__(self, iterator, emit, synchronize):
        self.iterator, self.emit, self.synchronize = iterator, emit, synchronize
        self.seconds, self.finished = 0., False

    def __iter__(self):
        return self

    def __next__(self):
        self.synchronize()
        started = time.perf_counter()
        success, finished = True, False
        try:
            return next(self.iterator)
        except StopIteration:
            finished = True
            raise
        except BaseException:
            finished, success = True, False
            raise
        finally:
            self.synchronize()
            self.seconds += time.perf_counter() - started
            if finished:
                self.finish(success)

    def finish(self, success):
        if not self.finished:
            self.finished = True
            self.emit(seconds=self.seconds, success=success, lazy=True)

    def close(self):
        try:
            if hasattr(self.iterator, 'close'):
                self.iterator.close()
        finally:
            self.finish(True)


def configure_backend(notify):
    """Use the isolated setup process's kernel check before any LTX import.

    LTX checks import success when choosing its backend. A smoke failure in the
    separate setup process alone would otherwise leave a broken backend enabled.
    Do not repeat a potentially context-poisoning GPU smoke test inside rendering.
    No installed package is removed.
    """
    try:
        enabled = json.loads(BACKEND_STATUS.read_text())['accelerated_decode'] is True
    except (OSError, ValueError, KeyError, TypeError):
        enabled = False
    if not enabled:
        sys.modules['natten'] = None
    notify('video_decode_backend', accelerated_decode=enabled,
           backend='natten' if enabled else 'existing_fallback', process='render')
    return enabled


class TimedBlock:
    def __init__(self, block, name, notify, synchronize, shot_id):
        self.block, self.name, self.notify = block, name, notify
        self.synchronize, self.shot_id = synchronize, shot_id
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self.block, name)

    def __call__(self, *args, **kwargs):
        self.calls += 1
        call = self.calls
        ident = self.shot_id()
        def emit(**data):
            self.notify('video_component_timing', id=ident, component=self.name, call=call, **data)
        self.synchronize()
        started = time.perf_counter()
        success = False
        try:
            result = self.block(*args, **kwargs)
            success = True
        finally:
            self.synchronize()
            self.notify('video_component_timing', id=ident, component=self.name + '_call', call=call,
                        seconds=time.perf_counter() - started, success=success, lazy=False)
        if isinstance(result, Iterator):
            return TimedIterator(result, emit, self.synchronize)
        return result


def instrument_pipeline(pipe, notify, shot_id):
    torch = torch_module()
    for attribute, name in (
        ('prompt_encoder', 'prompt_encode'), ('image_conditioner', 'image_condition'),
        ('stage', 'denoise'), ('upsampler', 'upsample'),
        ('video_decoder', 'video_decode'), ('audio_decoder', 'audio_decode'),
    ):
        block = getattr(pipe, attribute, None)
        if block is not None:
            setattr(pipe, attribute, TimedBlock(block, name, notify, torch.cuda.synchronize, shot_id))


if __name__ == '__main__':
    from common import event
    # A crash in optional CUDA validation must leave a disabled marker for the
    # separate render process, whose CUDA context remains clean.
    BACKEND_STATUS.parent.mkdir(parents=True, exist_ok=True)
    BACKEND_STATUS.write_text(json.dumps({'accelerated_decode': False}))
    enabled = prepare(event)
    BACKEND_STATUS.write_text(json.dumps({'accelerated_decode': enabled}))
