import importlib.util
import json
from pathlib import Path
import sys
import pytest
from types import SimpleNamespace

from app import raghu_pipeline as pipeline, raghu_vast


def test_low_balance_requires_explicit_override_and_positive_credit(monkeypatch):
    monkeypatch.delenv("RAGHU_ALLOW_LOW_BALANCE", raising=False)
    with pytest.raises(RuntimeError, match="lowest eligible rental"):
        raghu_vast.check_available_credit(.66, 1.06)
    monkeypatch.setenv("RAGHU_ALLOW_LOW_BALANCE", "true")
    raghu_vast.check_available_credit(.66, 1.06)
    with pytest.raises(RuntimeError, match="credit is empty"):
        raghu_vast.check_available_credit(0, 1.06)


def test_full_clone_production_accepts_hunyuan_and_reaches_generation(monkeypatch, tmp_path):
    from app import clone_chat_planner, youtube_clone_writer
    monkeypatch.setenv("RAGHU_MOTION_MODEL", "hunyuan15")
    monkeypatch.setenv("RAGHU_NARRATION_PROVIDER", "none")
    monkeypatch.setenv("RAGHU_PLAN_ONLY", "false")
    monkeypatch.setattr(pipeline, "production_credentials", lambda: None)
    monkeypatch.setattr(youtube_clone_writer, "enabled", lambda seconds: True)
    board = {"seconds": 30, "script": "एक पूरी छोटी कहानी।", "shots": [{"id": f"{i:03}"} for i in range(1, 7)]}
    stages = []
    def plan(*args, **kwargs):
        stages.append("clone planner")
        return board
    monkeypatch.setattr(clone_chat_planner, "plan", plan)
    monkeypatch.setattr(pipeline, "generate", lambda *args: stages.append("hunyuan generation"))
    monkeypatch.setattr(pipeline, "narration", lambda *args: None)
    monkeypatch.setattr(pipeline, "edit", lambda *args, **kwargs: stages.append("edit"))
    pipeline.work("fixture", tmp_path, {"title": "नई कहानी", "duration_seconds": 30}, lambda: None, lambda *args: None)
    assert stages == ["clone planner", "hunyuan generation", "edit"]


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
