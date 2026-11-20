from copy import deepcopy
import pytest

from app.youtube_clone.pipeline.scene_records import validate_record, compile_record, repair_records


def record():
    return dict(location="#BGD-01-STAIRS", view="interior", camera="locked close shot",
                visible=[], offscreen=["@CHAR-01-MEERA"],
                objects=[dict(id="stairs", description="stone staircase", before="dry treads", after="wet footprint marks on successive treads", fixed=True)],
                action=dict(kind="environmental_effect", actor=None, target="stairs", movement="wet footprint marks appear one by one upwards", reveal_face=False),
                static_surfaces=["stairs"])


def validate(r):
    return validate_record(r, {"@CHAR-01-MEERA"}, {"#BGD-01-STAIRS"})


def test_effect_compiles_empty_frame_and_stationary_stairs():
    r = record()
    validate(r)
    output = compile_record(r)
    assert output["character_tags"] == []
    assert "zero people" in output["first_frame_description"]
    assert "dry treads" in output["first_frame_description"]
    assert "wet footprint marks appear" not in output["first_frame_description"]
    assert "stairs remain stationary" in output["visual_action"]
    assert "MEERA" not in output["first_frame_description"]


def test_action_cannot_invent_offscreen_actor():
    r = record()
    r["action"].update(kind="locomotion", actor="@CHAR-01-MEERA")
    with pytest.raises(ValueError, match="visible"):
        validate(r)


def test_hidden_face_cannot_be_revealed():
    r = record()
    r["visible"] = [dict(tag="@CHAR-01-MEERA", pose="hand on rail", end_pose="hand still on rail", face_visible=False)]
    r["offscreen"] = []
    r["action"].update(kind="reaction", actor="@CHAR-01-MEERA", reveal_face=True)
    with pytest.raises(ValueError, match="face"):
        validate(r)


def test_unknown_object_and_duplicate_cast_rejected():
    r = record()
    r["action"]["target"] = "door"
    with pytest.raises(ValueError, match="target"):
        validate(r)
    r = record()
    r["visible"] = [dict(tag="@CHAR-01-MEERA", pose="standing", end_pose="standing", face_visible=True)] * 2
    with pytest.raises(ValueError, match="duplicate"):
        validate(r)


def test_targeted_repair_preserves_valid_record_and_input():
    good = record()
    bad = deepcopy(good)
    bad["action"]["target"] = "missing"
    records = [good, bad]
    original = deepcopy(records)
    calls = []
    def repair(index, broken, reason, simplify):
        calls.append((index, reason, simplify))
        return record()
    fixed, history = repair_records(records, validate, repair)
    assert records == original
    assert fixed[0] == good and len(calls) == 1 and calls[0][0] == 1
    assert history[0]["scene_number"] == 2


def test_repair_is_bounded():
    r = record()
    r["action"]["target"] = "missing"
    calls = []
    def repair(index, broken, reason, simplify):
        calls.append(simplify)
        return broken
    with pytest.raises(ValueError, match="Scene 1"):
        repair_records([r], validate, repair)
    assert calls == [False, True]
