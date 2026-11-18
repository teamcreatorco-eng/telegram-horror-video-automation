import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from app import media, raghu_narration, raghu_pipeline, raghu_vast, store


@pytest.fixture
def qwen_worker(monkeypatch, tmp_path):
    assets = Path(raghu_pipeline.ASSETS)
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=1, flock=lambda *args: None))
    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(ROOT=tmp_path, RESULTS=tmp_path / "results", event=lambda *args, **kwargs: None))
    spec = importlib.util.spec_from_file_location("qwen_test_worker", assets / "qwen_stills.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qwen_graph_binds_two_characters_and_background_to_both_conditionings(qwen_worker):
    graph = qwen_worker.graph("One room with exactly two women", ["room.png", "a.png", "b.png"], 123, "test")
    for key in ("30", "31"):
        assert graph[key]["inputs"]["image1"] == ["20", 0]
        assert graph[key]["inputs"]["image2"] == ["11", 0]
        assert graph[key]["inputs"]["image3"] == ["12", 0]
    assert graph["34"]["inputs"]["steps"] == 40
    assert graph["1"]["inputs"]["unet_name"] == "qwen_image_edit_2511_fp8mixed.safetensors"
    for item in graph.values():
        for value in item["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph


def test_qwen_scene_references_exclude_unrelated_style_person(qwen_worker, tmp_path):
    root = tmp_path
    (root / "results").mkdir()
    names = ["background.png", "character-a.png", "character-b.png"]
    for name in names:
        Image.new("RGB", (32, 32), "blue").save(root / "results" / name)
    folder = root / "QwenComfyUI/input/test"
    folder.mkdir(parents=True)
    task = {"refs": ["results/" + name for name in names] + ["unrelated-style-person.jpg"]}
    result = qwen_worker.task_references(task, folder)
    assert len(result) == 3
    assert all((root / "QwenComfyUI/input" / name).is_file() for name in result)


def test_pinned_qwen_download_has_exact_sizes_and_complete_hashes():
    rows = store.read(Path(raghu_pipeline.ASSETS) / "qwen-model-manifest.json")
    assert sum(row["bytes"] for row in rows) == 30172239743
    for row in rows:
        assert len(row["sha256"]) == 64
        int(row["sha256"], 16)
        assert len(row["revision"]) == 40


def test_vast_instance_read_uses_current_endpoint(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, **kwargs):
            calls.append(url)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"instances": [{"id": 42}]})

    monkeypatch.setattr(raghu_vast.httpx, "Client", Client)
    monkeypatch.setattr(raghu_vast, "required", lambda name: "test")
    assert raghu_vast.instances() == [{"id": 42}]
    assert calls == ["https://console.vast.ai/api/v1/instances/"]


def test_silent_mode_never_requests_voice_generation(monkeypatch, tmp_path):
    monkeypatch.setenv("RAGHU_NARRATION_PROVIDER", "none")
    monkeypatch.setattr(raghu_pipeline, "narrate", lambda *args, **kwargs: pytest.fail("Unexpected narration"))
    assert raghu_narration.silent()
    assert raghu_narration.pending_clone(tmp_path, [{"id": "001"}]) == []
    raghu_pipeline.narration(tmp_path, [{"id": "001"}], lambda: None, lambda *args: None)
    assert not list(tmp_path.iterdir())


def test_silent_editor_keeps_five_seconds_and_ignores_old_voice_files(monkeypatch, tmp_path):
    if not media.available():
        pytest.skip("FFmpeg required")
    monkeypatch.setenv("RAGHU_NARRATION_PROVIDER", "none")
    monkeypatch.setenv("RAGHU_MOTION_MODEL", "ltx")
    monkeypatch.setenv("RAGHU_LTX_END_FRAME", "false")
    clip = tmp_path / "shot-001.mp4"
    media.ff("-f", "lavfi", "-i", "color=c=blue:s=1280x768:r=24", "-frames:v", "121", "-an",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", clip)
    (tmp_path / "narration-001.wav").write_bytes(b"stale voice file must be ignored")
    plan = {"shots": [{"id": "001", "narration": "एक छाया कमरे में खड़ी थी।"}]}
    raghu_pipeline.edit(tmp_path, plan, lambda: None, music=False, opening=False)
    assert abs(media.duration(tmp_path / "final.mp4") - 5) < .1
    assert store.read(tmp_path / "raghu-timeline.json")["shots"][0]["seconds"] == 5
