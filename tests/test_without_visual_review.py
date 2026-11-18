import subprocess

import pytest
from PIL import Image

from app import raghu_pipeline as pipeline, store


def unexpected_review(*args, **kwargs):
    pytest.fail("Production must not call an AI visual reviewer")


def test_old_rejected_image_continues_without_visual_review(monkeypatch, tmp_path):
    assert pipeline.AI_VISUAL_REVIEW is False
    monkeypatch.setattr(pipeline, "review_still", unexpected_review)
    monkeypatch.setattr(pipeline, "review_continuity", unexpected_review)
    image = tmp_path / "still-001.png"
    Image.new("RGB", (32, 32), "blue").save(image)
    store.save(tmp_path / "still-001.png.review.json", {"pass": False, "reason": "Wrong pose"})
    verdict = pipeline.assess_still(tmp_path, {"file": image.name}, lambda: None)
    assert verdict["pass"] and verdict["review_skipped"] and verdict["technical_checks_passed"]
    assert pipeline.still_pair_checks(tmp_path, [{}, {}], lambda: None) == {}
    image.write_bytes(b"unreadable")
    with pytest.raises(OSError):
        pipeline.assess_still(tmp_path, {"file": image.name}, lambda: None)


@pytest.mark.parametrize("decodes", [True, False])
def test_saved_motion_uses_decode_checks_and_ignores_old_ai_rejection(monkeypatch, tmp_path, decodes):
    monkeypatch.setattr(pipeline, "review_motion", unexpected_review)
    monkeypatch.setattr(pipeline, "motion_model", lambda: "ltx")
    monkeypatch.setattr(pipeline, "uses_ending_image", lambda: False)
    monkeypatch.setattr(pipeline, "motion_signature", lambda *args: "current-assets")
    monkeypatch.setattr(pipeline.media, "probe", lambda path: {
        "streams": [{"codec_type": "video", "nb_frames": "121", "width": 1280, "height": 768}]
    })
    calls = []

    def decode(command, **kwargs):
        calls.append(command)
        if not decodes:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(pipeline.subprocess, "run", decode)
    store.save(tmp_path / "motion-001.review.json", {
        "signature": "current-assets", "pass": False, "reason": "Not enough movement"
    })
    verdict = pipeline.validate_motion(tmp_path, {"id": "001"})
    assert verdict["pass"] is decodes
    assert verdict["review_skipped"]
    assert verdict["technical_checks_passed"] is decodes
    assert len(calls) == 1
    if decodes:
        assert pipeline.validate_motion(tmp_path, {"id": "001"}) == verdict
        assert len(calls) == 1


def test_chatterbox_setup_has_no_gemini_requirement(monkeypatch, tmp_path):
    requested = []
    monkeypatch.setenv("RAGHU_NARRATION_PROVIDER", "chatterbox")
    monkeypatch.setenv("VAST_SSH_KEY_PATH", str(tmp_path / "key"))
    (tmp_path / "key").write_text("test")
    monkeypatch.setattr(pipeline, "required", lambda name: requested.append(name) or "configured")
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: name)
    monkeypatch.setattr(pipeline.raghu_narration, "clone_reference", lambda: tmp_path / "narrator.wav")
    pipeline.production_credentials()
    assert not any(name.startswith("GEMINI") for name in requested)


def test_saved_smoke_image_drops_old_visual_rejection_without_renting(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "required", lambda name: "configured")
    monkeypatch.setattr(pipeline, "rent", unexpected_review)
    monkeypatch.setattr(pipeline, "review_still", unexpected_review)
    Image.new("RGB", (32, 32), "blue").save(tmp_path / "smoke-image.png")
    store.save(tmp_path / "raghu-smoke.json", {
        "visual_review": {"pass": False, "reason": "Wrong pose"},
        "review_seconds": 4,
        "image_render_seconds": 3,
    })
    store.save(tmp_path / "raghu-rental.json", {"destroyed": True})
    pipeline.smoke("saved", tmp_path, "image", lambda: None, lambda *args: None)
    result = store.read(tmp_path / "raghu-smoke.json")
    assert result["visual_review"]["review_skipped"]
    assert result["visual_review"]["pass"]
    assert result["review_seconds"] == 0
