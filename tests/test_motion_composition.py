from app import raghu_pipeline
from app.youtube_clone.pipeline.visual_composition import motion_contract


def test_effective_hunyuan_prompt_preserves_action_camera_and_context(monkeypatch):
    monkeypatch.setenv("RAGHU_MOTION_MODEL", "hunyuan15")
    monkeypatch.setenv("RAGHU_LTX_END_FRAME", "false")
    shot = {"id": "001", "simple_prompt": True,
            "narration": "She pulls her wrist away from the hot tray.",
            "visual_action": "Guddi pulls her right wrist backward away from the tray.",
            "motion": "Legacy generic motion", "camera": "Fixed side close-up of wrist and tray",
            "start": {"staging": "Her wrist is above the tray"},
            "end": {"staging": "Her wrist is clear of the tray"}}
    prompt = raghu_pipeline.motion_plan([shot])["shots"][0]["video_prompt"]
    assert shot["narration"] in prompt
    assert shot["visual_action"] in prompt
    assert shot["camera"] in prompt
    assert shot["start"]["staging"] in prompt
    assert shot["end"]["staging"] in prompt
    assert "Legacy generic motion" not in prompt
    assert "Preserve established fire" in prompt
    assert "shape, size, material and ownership" in prompt
    assert "do not reveal an unseen face" in prompt
    assert "Keep all mouths closed" in prompt


def test_saved_shot_without_new_fields_still_uses_its_motion():
    shot = {"motion": "She steps backward", "simple_prompt": True}
    assert "She steps backward" in raghu_pipeline.opening_motion_prompt(shot)
    assert "no automatic zoom or orbit" in motion_contract(shot)


def test_sound_design_uses_explicit_timed_cues():
    from app.raghu_sound import parse_sound_design
    assert parse_sound_design("No fire sound. [score:dread] [effect:knock@0.6]") == (
        "dread", [{"effect": "knock", "at": .6}])
    assert parse_sound_design("[effect:fire@0] [effect:bad@0.2] [effect:rain@3]")[1] == [
        {"effect": "fire", "at": 0}]
