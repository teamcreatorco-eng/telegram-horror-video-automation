from app import raghu_pipeline, raghu_services
from app import store


def test_missing_required_face_cannot_pass_as_minor():
    assessment = raghu_services.OpeningAssessment(
        style="illustrated_2d", content_matches=True, visual_defects=[],
        minor_findings=["Only hands are visible"],
        observations="The required expression is outside the frame.",
        framing_matches=False, starting_pose_matches=True, required_props_match=True,
    )
    verdict = raghu_services.still_verdict(assessment)
    assert not verdict["pass"]
    assert not verdict["minor_retake"]
    assert "expression is outside the frame" in verdict["reason"]


def test_opening_review_with_contradictory_positive_flags_is_rejected():
    assessment = raghu_services.OpeningAssessment(
        style="illustrated_2d", content_matches=True, visual_defects=[],
        observations="The primary mismatch is that the rope rests on the table rather than floating.",
        framing_matches=True, starting_pose_matches=True, required_props_match=True,
    )
    assert not raghu_services.still_verdict(assessment)["pass"]


def test_legacy_clone_still_promotes_opening_to_primary_instruction():
    plan = {
        "cast": [{"tag": "ASHA", "prompt": "Asha with a braid"}],
        "locations": [{"tag": "ROOM", "prompt": "Empty room"}],
        "shots": [{
            "id": "005", "cast": ["ASHA"], "location": "ROOM", "clone_motion": "Rope rises",
            "still": "2D. Asha recoils. Camera: Medium shot.",
            "start": {"cast": ["ASHA"], "staging": "Asha's face recoils and her hand holds the rope.", "props": {}},
        }],
    }
    _, [opening] = raghu_pipeline.still_batches(plan)
    assert opening["prompt"].startswith("Opening composition: Asha's face recoils")
    assert "Reference image 2: identity and clothing for ASHA only" in opening["prompt"]
    assert opening["strict_opening"] and opening["max_retakes"] == 2


def test_visual_retake_updates_only_the_rejected_character(monkeypatch, tmp_path):
    monkeypatch.setattr(raghu_pipeline,'AI_VISUAL_REVIEW',True)
    plan = {"cast": [
        {"tag": "BUA", "prompt": "Reference sheet. Adult Indian woman, age 55, in a green sari. "
         "Neutral full-body three-quarter pose and front-facing expression inset."},
        {"tag": "KAVITA", "prompt": "Adult Indian woman, age 26."},
    ], "locations": [], "shots": []}
    tasks, _ = raghu_pipeline.still_batches(plan)
    first, approved = (item["file"] for item in tasks)
    (tmp_path / first).write_text("original")
    (tmp_path / approved).write_text("approved")
    calls = []

    class Remote:
        def push(self, files):
            calls.extend(store.read(files[0]))

        def stream_stills(self, manifest_name, names, folder):
            for name in names:
                (folder / name).write_text("retake")
                yield name

    def assess(folder, item, check):
        name = item["file"]
        result = {"pass": (folder / name).read_text() != "original",
                  "reason": "An inset repeats the character"}
        store.save(folder / (name + ".review.json"), result)
        return result

    def rewrite(image, locked, reason):
        assert image.name == first and image.read_text() == "original"
        assert "age 55" in locked and "green sari" in locked
        assert "inset" in reason
        return "Single full-body portrait of Bua in a green sari."

    monkeypatch.setattr(raghu_pipeline, "assess_still", assess)
    monkeypatch.setattr(raghu_pipeline, "rewrite_still_prompt", rewrite)
    assert raghu_pipeline.review_stills(Remote(), tmp_path, tasks, lambda: None) == {}
    assert len(calls) == 1 and calls[0]["file"] == first
    assert calls[0]["refs"] == []
    assert calls[0]["prompt"].startswith("Single full-body portrait of Bua")
    assert (tmp_path / approved).read_text() == "approved"
