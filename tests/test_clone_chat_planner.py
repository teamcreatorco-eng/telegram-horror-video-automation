import json

import pytest

from app import clone_chat_planner as adapter


@pytest.mark.parametrize("failure, expected_attempts", [
    ("Shot review still flagged continuity issues after the final repair pass", [False]),
    ("Previous response failed validation: A proposed repair must actually change a field.", [False]),
    ("Invalid script", [False]),
])
def test_clone_chat_has_no_continuity_rewrite(monkeypatch, tmp_path, failure, expected_attempts):
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


@pytest.mark.parametrize('record', [None, {}, {'visible': None}])
def test_noncompilable_scene_is_a_format_error_inside_response_retry(record):
    scene = dict(scene_number=1, title='कमरा', sound_design='Quiet', scene_record=record)
    with pytest.raises(ValueError, match='scene_record'):
        adapter.planning_writer.check_scene_envelopes({'scenes': [scene]}, 1)


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


@pytest.mark.parametrize('semantic_flag', [False, True])
def test_production_planner_accepts_83_words_without_any_review_call(tmp_path, semantic_flag):
    writer = adapter.planning_writer
    story = {"entity": "परछाईं", "characters": [{"tag": "@CHAR-01-BABLI", "name": "बबली"}],
             "speech": [{"speaker": "NARRATOR", "line": " ".join(["छाया"] * n)}
                        for n in [14, 14, 14, 14, 14, 13]]}
    assets = {"characters": [{**story["characters"][0], "look_summary": "Woman in blue", "voice_description": "Hindi woman"}],
              "backgrounds": [{"tag": "#BGD-01-ROOM", "location_name": "कमरा", "look_summary": "An empty room"}]}
    shots = {"scenes": [{"scene_number": i, "title": "कमरा", "sound_design": "Quiet room",
                         "scene_record": {"location": "#BGD-01-ROOM", "view": "interior", "camera": "locked medium",
                             "visible": [{"tag": "@CHAR-01-BABLI", "pose": "standing by the door",
                                          "end_pose": "standing by the door", "face_visible": True}],
                             "offscreen": [], "objects": [], "static_surfaces": [],
                             "action": {"kind": "establishing", "actor": None, "target": None,
                                        "movement": "holds the composition", "reveal_face": False}}}
                        for i in range(1, 7)]}
    if semantic_flag:
        # A semantic concern must not launch record reviews/repairs or reject production.
        shots['scenes'][0]['scene_record']['visible'][0]['face_visible'] = False
        shots['scenes'][0]['scene_record']['action']['reveal_face'] = True
    class Model:
        def __init__(self):
            self.calls = []
            self.answers = iter([story, assets, shots])
        def complete(self, prompt, **kwargs):
            self.calls.append(prompt)
            assert "You are the shot continuity editor" not in prompt
            return json.dumps(next(self.answers), ensure_ascii=False)
    model = Model()
    output = tmp_path / "no-review-test"
    writer.write_plan("बंद दरवाज़ा", 30, output, model=model)
    project = json.loads((output / "pipeline.json").read_text(encoding="utf-8"))
    assert len(model.calls) == 3
    from app.youtube_clone.pipeline.visual_composition import SHOT_RULES
    assert SHOT_RULES in model.calls[0]
    assert SHOT_RULES in model.calls[2]
    assert project["script"]["word_count"] == 83
    assert len(project["scenes"]) == 6
    assert project["planning_review"] == []


def test_original_image_brief_keeps_references_without_appended_sheet_text(monkeypatch):
    from app import raghu_pipeline
    monkeypatch.setenv("RAGHU_IMAGE_MODEL", "flux")
    for simple in (True, False):
        plan = {"cast": [{"tag": "A", "prompt": "Woman with braid, neutral grey studio background, full character reference sheet"},
                         {"tag": "B", "prompt": "Older woman in red sari"}],
                "locations": [{"tag": "ROOM", "prompt": "Empty room"}],
                "shots": [{"id": "001", "cast": ["A", "A"], "location": "ROOM",
                           "clone_motion": "Raises wrist", "simple_prompt": simple,
                           "still": "A at left, her wrist beside her ear",
                           "start": {"cast": ["A"], "staging": "A at left", "props": {}}}]}
        _, [task] = raghu_pipeline.still_batches(plan)
        assert len([r for r in task["refs"] if "character-" in r]) == 1
        for key in ("prompt", "retake_prompt"):
            assert "neutral grey studio background" not in task[key]
            assert "full character reference sheet" not in task[key]
            assert "Reference image 2" in task[key]
            assert task["refs"][-1] == "style-wide.jpg"
