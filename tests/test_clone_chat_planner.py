import json

import pytest

from app import clone_chat_planner as adapter


@pytest.mark.parametrize("failure, expected_attempts", [
    ("Shot review still flagged continuity issues after the final repair pass", [False, True]),
    ("Previous response failed validation: A proposed repair must actually change a field.", [False, True]),
    ("Invalid script", [False]),
])
def test_clone_chat_rewrites_once_only_for_continuity(monkeypatch, tmp_path, failure, expected_attempts):
    attempts = []

    def write_plan(title, duration, output, *, model, short_editorial_margin):
        assert title == "मिट्टी का हाथ"
        assert duration == 30
        assert short_editorial_margin
        attempts.append(model.simpler_draft)
        raise ValueError(failure)

    monkeypatch.setattr(adapter.raghu_services, "required", lambda name: "fixture")
    monkeypatch.setattr(adapter.planning_writer, "write_plan", write_plan)
    with pytest.raises(ValueError, match=failure):
        adapter.plan(tmp_path, "मिट्टी का हाथ", False, lambda: None, lambda *_: None, seconds=30)
    assert attempts == expected_attempts


def test_asset_stage_uses_only_the_locked_cast():
    locked = [{"tag": "@CHAR-01-BABLI", "name": "बबली"}]
    response = {
        "characters": [
            {"tag": "@CHAR-02-CHUDAIL", "name": "चुड़ैल", "look_summary": "A spirit"},
            {"tag": "@CHAR-01-BABLI", "name": "Babli", "look_summary": "Woman in a blue kurta"},
        ],
        "backgrounds": [{"tag": "#BGD-01-ROOM", "location_name": "कमरा"}],
    }
    bound = json.loads(adapter.bind_locked_cast(json.dumps(response), locked))
    assert bound["characters"] == [{**locked[0], "look_summary": "Woman in a blue kurta"}]
    assert bound["backgrounds"] == response["backgrounds"]
    response["characters"].pop()
    missing = json.dumps(response)
    assert adapter.bind_locked_cast(missing, locked) == missing


def test_redundant_repair_does_not_discard_a_valid_repair(monkeypatch):
    writer = adapter.planning_writer
    monkeypatch.setattr(writer, "check_assets", lambda *_: None)
    monkeypatch.setattr(writer, "check_shots", lambda *_: None)
    draft = {"characters": [], "backgrounds": [], "scenes": [
        {"scene_number": 1, "first_frame_description": "Bangle on the table"},
        {"scene_number": 2, "first_frame_description": "Bangle on her wrist"},
    ]}
    review = {"asset_repairs": [], "shot_repairs": [
        {"scene_number": 1, "changes": {"first_frame_description": "Bangle on the table"}},
        {"scene_number": 2, "changes": {"first_frame_description": "Hand reaches toward the table"}},
    ]}
    fixed = writer.apply_repairs(review, draft, [])
    assert fixed["scenes"][0] == draft["scenes"][0]
    assert fixed["scenes"][1]["first_frame_description"] == "Hand reaches toward the table"
    review["shot_repairs"].pop()
    with pytest.raises(ValueError, match="actually change"):
        writer.apply_repairs(review, draft, [])


def test_repeated_validation_failure_asks_provider_for_a_distinct_retry():
    prompts = []

    class Model:
        def complete(self, prompt, **kwargs):
            prompts.append(prompt)
            return "{}"

    def invalid(_):
        raise ValueError("Same fault")

    with pytest.raises(ValueError, match="Same fault"):
        adapter.planning_writer.ask(Model(), "Original brief", invalid, 800)
    assert len(prompts) == len(set(prompts)) == 3


@pytest.mark.parametrize(("lengths", "expected_error"), [
    ([14] * 3 + [13] * 21, None),
    ([14] * 5 + [13] * 19, "Speech has 317 words"),
    ([17] + [12] * 2 + [13] * 21, "at or below 16 words"),
])
def test_two_minute_story_allows_a_small_overage_when_each_beat_fits(lengths, expected_error):
    writer = adapter.planning_writer
    story = {"entity": "परछाईं", "characters": [{"tag": "@CHAR-01-BABLI", "name": "बबली"}],
             "speech": [{"speaker": "NARRATOR", "line": " ".join(["छाया"] * length)} for length in lengths]}
    count, low, high = writer.duration_limits(120)
    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            writer.check_story(story, count, low, high, editorial_margin=True)
    else:
        writer.check_story(story, count, low, high, editorial_margin=True)
