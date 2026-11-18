import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from app import raghu_pipeline as pipeline, raghu_vast


def test_hunyuan_graph_uses_single_opening_image_and_actual_15_weights(monkeypatch):
    assets = Path(pipeline.ASSETS)
    monkeypatch.setitem(sys.modules, "wan_worker", SimpleNamespace(node=lambda kind, **kw: {"class_type": kind, "inputs": kw}))
    monkeypatch.setitem(sys.modules, "qwen_stills", SimpleNamespace(COMFY=Path("test"), download_models=lambda *a: None))
    spec = importlib.util.spec_from_file_location("hunyuan_test", assets / "hunyuan_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    graph = worker.graph({"video_prompt": "Walk toward the door", "frames": 121, "fps": 24, "seed": 42}, "start.png", "test")
    assert graph["8"]["inputs"]["length"] == 121
    assert graph["8"]["inputs"]["start_image"] == ["1", 0]
    assert graph["8"]["inputs"]["clip_vision_output"] == ["7", 0]
    assert graph["9"]["inputs"]["unet_name"] == "hunyuanvideo1.5_720p_i2v_fp16.safetensors"
    assert graph["11"]["inputs"]["steps"] == 20
    assert graph["11"]["inputs"]["cfg"] == 6
    assert graph["14"]["inputs"]["codec"] == {"codec": "h264"}
    assert sum(n["class_type"] == "LoadImage" for n in graph.values()) == 1
    for n in graph.values():
        for value in n["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph


def test_hunyuan_uses_opening_only_even_with_legacy_end_frame_setting(monkeypatch):
    monkeypatch.setenv("RAGHU_MOTION_MODEL", "hunyuan15")
    monkeypatch.setenv("RAGHU_LTX_END_FRAME", "true")
    assert raghu_vast.motion_model() == "hunyuan15"
    assert not raghu_vast.uses_ending_image()
    shot = {"id": "001", "motion": "She takes two steps toward the door", "narration": "वह दरवाज़े की तरफ चली।"}
    output, = pipeline.motion_plan([shot])["shots"]
    assert output["frames"] == 121
    assert output["fps"] == 24
    assert "ending_image_file" not in output


def test_hunyuan_manifest_shares_encoder_without_duplicate_download():
    assets = Path(pipeline.ASSETS)
    qwen = json.loads((assets / "qwen-model-manifest.json").read_text())
    hunyuan = json.loads((assets / "hunyuan-model-manifest.json").read_text())
    assert len(hunyuan) == 5
    assert len({v["file"] for v in hunyuan}) == 5
    shared = {v["sha256"] for v in qwen} & {v["sha256"] for v in hunyuan}
    assert len(shared) == 1
    distinct = {v["sha256"]: v["bytes"] for v in qwen + hunyuan}
    assert 50e9 < sum(distinct.values()) < 51e9
    assert all(len(v["revision"]) == 40 and len(v["sha256"]) == 64 for v in hunyuan)
