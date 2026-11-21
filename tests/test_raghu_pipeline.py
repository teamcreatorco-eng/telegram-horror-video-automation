from contextlib import contextmanager
from pathlib import Path

import pytest

from app import raghu_pipeline, store


@pytest.fixture(autouse=True)
def legacy_visual_review_cases(monkeypatch):
    # Retain coverage of the old review/recovery helpers without enabling them in production.
    monkeypatch.setattr(raghu_pipeline, "AI_VISUAL_REVIEW", True)


@pytest.mark.parametrize(("repeat_flow_issue", "temporary_failure"), [(False, False), (True, False), (False, True)])
def test_writer_repairs_flagged_beats_and_reuses_completed_plan(
        monkeypatch, tmp_path, repeat_flow_issue, temporary_failure):
    from app import raghu_services as services

    world = services.World(synopsis="A locked door conceals a secret beneath the old house.",
                           entity_rule="The entity answers three knocks but cannot cross an open door.",
                           setup_and_payoff="The door is wedged open early; the wedge keeps the entity trapped at the end.",
                           act_beats=[services.ActPlan(
                               turning_point="Meera notices the reflection at the shop mirror.",
                               location="ROOM",
                               continuity="The fixed shop mirror is the clue, never a pocket mirror.",
                           )] * 6,
                           cast=[{"tag": "MEERA", "prompt": "One adult in a yellow shirt."}],
                           locations=[{"tag": "ROOM", "prompt": "An empty room at the old house."}],
                           connections=[])
    state = services.SceneState(location="ROOM", cast=["MEERA"], staging="Meera faces the door.",
                                props={"bangles": "on Meera's wrist"})
    beats = [services.Beat(line=f"मीरा ने कदम {i} बढ़ाया।", framing="Medium shot by the door",
                            action="Meera takes a step toward the door.", end=state, visible=["MEERA"],
                            opening=state if i == 0 else None) for i in range(30)]
    beats[5] = beats[5].model_copy(update={
        "line": "मीरा को पीछे से धीमी फुसफुसाहट सुनाई दी।",
        "framing": "Close shot of Meera's face, cropped above the wrist.",
        "action": "Meera freezes and glances over her shoulder.",
    })
    repaired = beats[3].model_copy(update={"action": "Meera stops short of the doorway."})
    dependent = beats[4].model_copy(update={"action": "Meera looks up at the fixed shop mirror."})
    rebuilt = repaired.model_copy(update={"action": "Meera stops beside the fixed shop mirror."})
    prompts = []
    batch_index = 0
    reviews = 0
    failed_request = False

    def write(schema, prompt, *, max_tokens, usage, attempts):
        nonlocal batch_index, reviews, failed_request
        assert "{{" not in prompt
        assert attempts == 1
        prompts.append((schema, prompt))
        if temporary_failure and not failed_request and batch_index == 1 and issubclass(schema, services.Board):
            assert (tmp_path / "raghu-board-v10-1-approved.json").is_file()
            failed_request = True
            raise services.DeepSeekUnavailable("A temporary fixture failure.")
        usage("deepseek-flash", {"prompt_tokens": 200, "completion_tokens": 100})
        if schema is services.World:
            assert "Build the world before writing any shots" in prompt
            assert "six causal acts" in prompt
            return world
        if schema is services.OutlineReview:
            assert "Shot budgets by act: [5, 5, 5, 5, 5, 5]" in prompt
            return services.OutlineReview(findings=[], resolution=world.act_beats[-1],
                                          replacements={2: world.act_beats[1]})
        if issubclass(schema, services.Board):
            assert "Write 10 shots" in prompt
            assert "Allowed cast tags: ['MEERA']" in prompt
            assert '"shots": [1, 5]' in prompt and '"shots": [6, 10]' in prompt
            assert world.act_beats[0].continuity in prompt
            if "Rejected batch:" in prompt:
                assert repeat_flow_issue
                assert "coordinating the setup, turning point and consequence across shots" in prompt
                return schema(beats=[*beats[:3], rebuilt, dependent, *beats[5:10]])
            start = batch_index * 10
            batch_index += 1
            if start == 0:
                from pydantic import ValidationError
                with pytest.raises(ValidationError):
                    schema(beats=beats[:9])
                stray = beats[0].model_copy(update={"visible": ["SERVANT"]})
                bad_cut = beats[2].model_copy(update={"transition": "time_cut", "opening": state})
                return schema(beats=[stray, beats[1], bad_cut, *beats[3:10]])
            assert world.entity_rule in prompt and beats[0].line in prompt
            return schema(beats=beats[start:start + 10])
        if schema is services.BeatPatch:
            if "Repair only shots [1, 3]" in prompt:
                assert "SERVANT" in prompt and "visible time-cut cue" in prompt
                return services.BeatPatch(replacements={1: beats[0], 3: beats[2]})
            assert "Repair only shots [4]" in prompt
            assert "Allowed replacement keys: [1, 2, 3, 4, 5, 6]" in prompt
            return services.BeatPatch(replacements={4: repaired, 5: dependent})
        assert schema is services.BoardReview
        assert world.setup_and_payoff in prompt
        reviews += 1
        if reviews == 1:
            import json
            resolved = json.loads(prompt.split("\nResolved shots: ", 1)[1])
            assert any("the narrator can establish sounds" in draft for _, draft in prompts)
            assert "A supported reaction is sufficient" in prompt
            assert resolved[5]["narration"] == beats[5].line
            assert resolved[5]["start"]["props"] == {"bangles": "on Meera's wrist"}
            assert "sound_cues" not in resolved[5]
        return services.BoardReview(issues=[
            services.Issue(shot=4, reason="The board substitutes a pocket mirror for the fixed shop mirror.")
        ] if reviews == 1 or (repeat_flow_issue and reviews == 2) else [])

    monkeypatch.setattr(services, "write", write)
    monkeypatch.setattr(services.time, "sleep", lambda seconds: None)
    milestones = []
    result = services.plan(tmp_path, "Old House", 150, lambda: None,
                           lambda stage, percent: milestones.append((stage, percent)))
    assert any("30/30 shots drafted" in stage for stage, _ in milestones)
    assert any("Repairing script and motion for shots 4" in stage for stage, _ in milestones)
    assert milestones[-1][0].startswith("Script and storyboard ready")
    assert len(result["shots"]) == 30
    assert result["entity_rule"] == world.entity_rule
    assert result["shots"][2]["transition"] == "continuous"
    assert result["shots"][3]["motion"] == (rebuilt if repeat_flow_issue else repaired).action
    assert result["shots"][4]["motion"] == dependent.action
    assert all(a["end"] == b["start"] for a, b in zip(result["shots"], result["shots"][1:]))
    expected_calls = 11 + 2 * repeat_flow_issue + temporary_failure
    assert failed_request == temporary_failure
    assert len(prompts) == expected_calls
    assert store.read(tmp_path / "deepseek-planning-usage.json")["attempts"] == expected_calls
    assert services.plan(tmp_path, "Old House", 150, lambda: None) == result
    assert len(prompts) == expected_calls


def test_time_cut_cue_reaches_starting_image_without_a_character_count_rule():
    from app import raghu_services as services

    state = services.SceneState(location="ROOM", cast=["MEERA"], staging="Meera faces the window.", props={})
    beat = services.Beat(line="भोर में मीरा ने खिड़की खोली।", framing="Wide shot of the shuttered window.",
                         action="Meera quietly opens the window.", opening=state, end=state,
                         visible=["MEERA"], transition="time_cut", cue="Dawn light")
    shots, errors = services.materialize(services.Board(beats=[beat]), None)
    assert not errors
    assert "Dawn light" in shots[0].still
    assert not services.sequence_errors(shots, [beat.line], None, {"MEERA"}, {"ROOM"}, set())


def test_turning_point_later_in_act_is_accepted_without_clearing_other_issues():
    from app import raghu_services as services

    false_missing = services.Issue(shot=1, reason="The warning is delivered in shot two, not shot one.", missing_act=1)
    actual_missing = services.Issue(shot=6, reason="The diary reveal never happens in this act.", missing_act=2)
    contradiction = services.Issue(shot=2, reason="The neighbor plays the wrong character.")
    review = services.BoardReview(turning_points={1: 2, 2: 5},
                                  issues=[false_missing, actual_missing, contradiction])
    assert services.blocking_flow_issues(review, [{"shots": [1, 5]}, {"shots": [6, 10]}]) == [
        actual_missing, contradiction]


def test_sequence_rejects_meera_door_jump_and_unknown_character():
    from app import raghu_services as services

    def shot(location, end_location, cast=("MEERA",)):
        start = services.SceneState(location=location, cast=list(cast), staging="Meera faces the door.",
                                    props={"door": "closed"})
        return services.Shot(narration="दरवाज़ा बंद हो गया।", still="Meera stands by the wooden door.",
                             motion="The wooden door closes in front of her.", cast=list(cast),
                             location=location, start=start, end=start.model_copy(update={"location": end_location}),
                             transition="continuous")

    previous = shot("HALL", "HALL")
    wrong = shot("EXTERIOR", "STAIRS", ("PINK-GIRL",))
    errors = services.sequence_errors([wrong], [wrong.narration], previous, {"MEERA"},
                                      {"HALL", "EXTERIOR", "STAIRS"}, {frozenset(("HALL", "EXTERIOR"))})
    assert any("unknown character" in error for error in errors)
    assert any("mapped doorway" in error for error in errors)
    assert any("previous shot end" in error for error in errors)


def test_route_repair_updates_the_shots_on_both_sides_of_a_passage(monkeypatch, tmp_path):
    from app import raghu_services as services

    bedroom, hall, kitchen = "HAVELI-BEDROOM", "HAVELI-HALL", "HAVELI-KITCHEN"
    connections = [(bedroom, hall), (hall, kitchen)]
    world = services.World(
        synopsis="Meera follows a knocking noise from her bedroom to the kitchen.",
        entity_rule="The spirit can knock on a door but cannot open it on its own.",
        setup_and_payoff="Meera tests the bolt early and later uses it to hold the spirit back.",
        cast=[{"tag": "MEERA", "prompt": "One adult in a yellow shirt."}],
        locations=[{"tag": name, "prompt": f"An empty room with a wooden door: {name}."}
                   for name in (bedroom, hall, kitchen)],
        connections=connections,
        act_beats=[services.ActPlan(turning_point="Meera listens for the knock at the door.",
                                   location=bedroom if i < 2 else kitchen,
                                   continuity="The knock stays on the other side of the bolted door.")
                   for i in range(6)],
    )
    state = services.SceneState(location=bedroom, cast=["MEERA"], staging="Meera listens by the door.", props={})
    beats = [services.Beat(line=f"मीरा ने दस्तक {i} सुनी।", framing="Medium shot by the door.",
                           action="Meera listens for another knock.", visible=["MEERA"],
                           opening=state if i == 0 else None,
                           end=state.model_copy(update={"location": bedroom if i < 10 else kitchen}))
             for i in range(30)]
    first = beats[10].model_copy(update={"end": state.model_copy(update={"location": hall}),
                                        "framing": "Already inside the hall, looking out from the far wall.",
                                        "action": "Meera crosses the bedroom threshold into the hall."})
    second = beats[11].model_copy(update={"action": "Meera crosses from the hall into the kitchen."})
    first.line = beats[0].line
    batches = iter([beats[:10], beats[10:20], beats[20:]])
    repairs = []
    line_repairs = []

    def write(schema, prompt, **kwargs):
        if schema is services.World:
            return world
        if schema is services.OutlineReview:
            ending = world.act_beats[-1].model_copy(update={
                "turning_point": "Meera bars the spirit at the kitchen threshold.",
                "continuity": "Meera keeps the key on her belt after the spirit retreats.",
            })
            assert "a prop cannot both be left in a well and kept" in prompt
            return services.OutlineReview(findings=["Give the key one final owner."], resolution=ending)
        if issubclass(schema, services.Board):
            batch = next(batches)
            if batch[0] is beats[10]:
                assert '"arrival": "HAVELI-BEDROOM → HAVELI-HALL → HAVELI-KITCHEN"' in prompt
            if batch[0] is beats[20]:
                assert "Meera bars the spirit at the kitchen threshold." in prompt
            return schema(beats=batch)
        if schema is services.BeatPatch:
            repairs.append(prompt)
            assert "Repair only shots [1, 2]" in prompt
            assert '"1": {"start": "HAVELI-BEDROOM", "end": "HAVELI-HALL"}' in prompt
            assert '"2": {"start": "HAVELI-HALL", "end": "HAVELI-KITCHEN"}' in prompt
            assert "Use consecutive shots along this route: HAVELI-BEDROOM → HAVELI-HALL → HAVELI-KITCHEN" in prompt
            assert "Allowed replacement keys: [1, 2, 3, 4, 5, 6]" in prompt
            return services.BeatPatch(replacements={1: first, 2: second})
        if schema is services.NarrationPatch:
            line_repairs.append(prompt)
            assert "Repeats film shot 1" in prompt
            assert first.action in prompt
            return services.NarrationPatch(replacements={
                1: beats[0].line if len(line_repairs) == 1 else "मीरा ने चौखट पार की और गलियारे में पहुँची।"})
        assert schema is services.BoardReview
        return services.BoardReview(issues=[])

    monkeypatch.setattr(services, "write", write)
    usage_path = tmp_path / "deepseek-planning-usage.json"
    store.save(usage_path, {"attempts": 14, "responses": []})
    with pytest.raises(services.PlanningValidationError, match="request budget"):
        services.plan(tmp_path, "The Knock", 150, lambda: None)
    result = services.plan(tmp_path, "The Knock", 150, lambda: None, extra_runs=2)
    assert store.read(usage_path)["attempts"] > 14
    assert len(result["shots"]) == 30
    assert result["act_beats"][-1] == "Meera bars the spirit at the kitchen threshold."
    assert len(repairs) == 1
    assert len(line_repairs) == 2
    assert "previous reply" in line_repairs[1]
    assert result["shots"][10]["narration"] != result["shots"][0]["narration"]
    assert [(shot["start"]["location"], shot["end"]["location"]) for shot in result["shots"][10:12]] == [
        (bedroom, hall), (hall, kitchen)]
    crossing = result["shots"][10]
    assert "Already inside the hall" not in crossing["still"]
    assert f"pre-action view from {bedroom}" in crossing["still"]
    assert f"show the crossing into {hall}" in crossing["motion"]
    assert crossing["location"] == bedroom


def test_continuity_review_retries_an_unclear_pair(monkeypatch, tmp_path):
    from app import raghu_services

    replies = iter([
        {"decision": "unclear", "reason": "The subject in shot two is cropped."},
        {"decision": "pass", "reason": "The cut shows the scripted second character."},
    ])
    prompts = []

    def review(files, prompt, schema, *, validate):
        prompts.append(prompt)
        return validate(next(replies))

    monkeypatch.setattr(raghu_services, "vision_json", review)
    assert raghu_services.review_continuity([tmp_path / "001.png", tmp_path / "002.png"], "Same scene.")["pass"]
    assert len(prompts) == 2
    assert "The subject in shot two is cropped." in prompts[1]


def test_failed_continuity_still_never_starts_motion(monkeypatch, tmp_path):
    pauses = []

    class Remote:
        def push(self, files):
            pass

        def phase(self, command, seconds):
            pass

        def start_motion_setup(self):
            raise AssertionError("Motion must wait for visual continuity")

        def pause_and_restart(self, progress, percent):
            pauses.append(percent)
            if len(pauses) == 2:
                raise RuntimeError("Meera is suddenly outside; rental is stopped.")

    @contextmanager
    def lease(*args, **kwargs):
        yield Remote()

    import pytest
    shots = [{"id": ident, "transition": "continuous", "motion": "Meera steps toward the table."}
             for ident in ("001", "002")]
    monkeypatch.setattr(raghu_pipeline, "still_batches", lambda plan: ([], []))
    monkeypatch.setattr(raghu_pipeline, "rent", lease)
    monkeypatch.setattr(raghu_pipeline, "review_stills", lambda *args: {})
    monkeypatch.setattr(raghu_pipeline, "still_pair_checks", lambda *args: {"002": "Meera is suddenly outside"})
    with pytest.raises(RuntimeError, match="Meera is suddenly outside"):
        raghu_pipeline.generate(tmp_path, "job", {"shots": shots}, lambda: None, lambda *args: None)
    assert len(pauses) == 2


def test_saved_still_selection_goes_directly_to_motion(monkeypatch, tmp_path):
    import hashlib

    name = "still-001.png"
    (tmp_path / name).write_bytes(b"current")
    (tmp_path / ("rejected-" + name)).write_bytes(b"selected")
    store.save(tmp_path / "saved-still-selection.json", {
        "frames": {name: {"file": "rejected-" + name, "sha256": hashlib.sha256(b"selected").hexdigest()}},
        "resume_seconds": 2400,
    })
    calls = []

    @contextmanager
    def lease(folder, ident, check, **options):
        assert options == {"pending_shots": 1, "resume_seconds": 2400}
        yield object()

    def forbidden(*args):
        raise AssertionError("Still generation must remain disabled")

    monkeypatch.setattr(raghu_pipeline, "rent", lease)
    monkeypatch.setattr(raghu_pipeline, "still_batches", lambda *args: ([], []))
    monkeypatch.setattr(raghu_pipeline, "generate_on_rental", forbidden)
    monkeypatch.setattr(raghu_pipeline, "animate_stills", lambda remote, folder, shots, *args: calls.extend(shots))
    shot = {"id": "001", "transition": "continuous", "motion": "Meera steps toward the table."}
    raghu_pipeline.generate(tmp_path, "job", {"shots": [shot]}, lambda: None, lambda *args: None)
    assert calls == [shot]
    assert (tmp_path / name).read_bytes() == b"selected"
    assert (tmp_path / ("before-selection-" + name)).read_bytes() == b"current"


def test_generation_and_gpu_cleanup_precede_narration_and_edit(monkeypatch, tmp_path):
    stages = []
    monkeypatch.setenv("RAGHU_TARGET_SECONDS", "30")
    monkeypatch.setattr(raghu_pipeline, "production_credentials", lambda: None)
    monkeypatch.setattr(raghu_pipeline, "required", lambda name: "configured")
    intro = tmp_path / "intro.mp4"
    intro.touch()
    monkeypatch.setattr(raghu_pipeline, "opening_path", lambda: intro)
    monkeypatch.setattr(raghu_pipeline.media, "duration", lambda path: 7)
    monkeypatch.setattr(raghu_pipeline, "plan",
                        lambda *args: {"shots": [{"id": "001"}], "seconds": 30})

    @contextmanager
    def lease(*args):
        stages.append("rented")
        yield None
        stages.append("destroyed")

    def generation(folder, ident, plan, check, progress):
        with lease():
            stages.extend(["stills", "stills approved", "motion", "source clips saved"])

    monkeypatch.setattr(raghu_pipeline, "generate", generation)
    monkeypatch.setattr(raghu_pipeline, "narration", lambda *args: stages.append("narration"))
    monkeypatch.setattr(raghu_pipeline, "edit", lambda *args, **kwargs: stages.append("edit"))
    raghu_pipeline.work("fixture", tmp_path, {"title": "Fixture"}, lambda: None,
                        lambda *args: None)
    assert stages == ["rented", "stills", "stills approved", "motion",
                      "source clips saved", "destroyed", "narration", "edit"]


def test_production_uses_saved_plan_without_calling_deepseek(monkeypatch, tmp_path):
    monkeypatch.setenv("RAGHU_PLAN_ONLY", "true")
    monkeypatch.setenv("RAGHU_TARGET_SECONDS", "150")
    saved = {"title": "Saved Story", "seconds": 30, "script": "Saved narration",
             "shots": [{"id": f"{number:03d}"} for number in range(1, 7)]}
    store.save(tmp_path / "raghu-plan-v2.json", saved)
    stages = []

    def forbidden(*args):
        raise AssertionError("Saved production should reuse the approved script and prompts")

    monkeypatch.setattr(raghu_pipeline, "choose_title", forbidden)
    monkeypatch.setattr(raghu_pipeline, "plan", forbidden)
    monkeypatch.setattr(raghu_pipeline, "production_credentials", lambda: None)
    intro = tmp_path / "intro.mp4"
    intro.touch()
    monkeypatch.setattr(raghu_pipeline, "opening_path", lambda: intro)
    monkeypatch.setattr(raghu_pipeline.media, "duration", lambda path: 7)
    monkeypatch.setattr(raghu_pipeline, "generate", lambda remote, ident, plan, *args: stages.append(plan))
    monkeypatch.setattr(raghu_pipeline, "narration", lambda folder, shots, *args: stages.append(shots))
    monkeypatch.setattr(raghu_pipeline, "edit", lambda folder, plan, *args, **kwargs: stages.append(plan))
    assert raghu_pipeline.work("fixture", tmp_path, {"raghu_produce_saved": True, "choose_topic": True},
                               lambda: None, lambda *args: None) is None
    assert stages == [saved, saved["shots"], saved]
    assert store.read(tmp_path / "story.json") == saved


def test_saved_character_is_reviewed_against_text_with_style_only_reference(monkeypatch, tmp_path):
    item = raghu_pipeline.task("character-MEERA.png", "Meera wears a red sari.", ["style-wide.jpg"], 1)
    report = tmp_path / "character-MEERA.png.review.json"
    store.save(report, {"pass": False, "style": "illustrated_2d", "minor_findings": [],
                        "reason": "Person differs from the style image."})

    def assess(files, prompt):
        assert [path.name for path in files] == ["character-MEERA.png", "style-wide.jpg"]
        assert "Judge the character against its text description" in prompt
        assert "Image 2 is style only" in prompt
        return {"pass": True}

    monkeypatch.setattr(raghu_pipeline, "review_still", assess)
    assert raghu_pipeline.assess_still(tmp_path, item, lambda: None)["pass"]
    assert store.read(report)["review_version"] == raghu_pipeline.STILL_REVIEW_VERSION


def test_still_retake_prompt_uses_locked_props_and_named_reference_roles():
    plan = {
        "cast": [{"tag": "KAMLA", "prompt": "Adult with a long braid"},
                 {"tag": "CHAMPA", "prompt": "Adult dressed in red"}],
        "locations": [{"tag": "ROOM", "prompt": "A room with a wooden chest"}],
        "shots": [{"id": "003", "location": "ROOM", "cast": ["KAMLA", "CHAMPA"],
                   "still": "Overhead view of the chest.",
                   "start": {"cast": ["KAMLA"], "staging": "Kamla kneels next to the chest.",
                             "props": {"bangles": "seven on cloth, one cracked"}}}],
    }
    _, starts = raghu_pipeline.still_batches(plan)
    retake = starts[0]["retake_prompt"]
    assert "Reference 2 is KAMLA" in retake
    assert "CHAMPA" not in retake
    assert "seven on cloth, one cracked" in retake
    assert "Overhead view" in retake


def test_still_gate_resubmits_only_rejected_stills(monkeypatch, tmp_path):
    batches = []
    style = Path("style-wide.jpg")
    tasks = [
        {"file": "good.png", "refs": [style.name], "prompt": "Good", "seed": 1},
        {"file": "bad.png", "refs": [style.name], "prompt": "Bad", "seed": 2},
    ]

    class Remote:
        def push(self, files):
            batches.append(store.read(files[0]))
        def stream_stills(self, manifest_name, names, destination):
            for name in names:
                (destination / name).write_text(str(len(batches)))
                yield name

    def assess(files, prompt):
        assert "Correct this issue" not in prompt
        return {"pass": files[0].name == "good.png" or files[0].read_text() == "2",
                "reason": "Wrong hand"}

    monkeypatch.setattr(raghu_pipeline, "review_still", assess)
    raghu_pipeline.review_stills(Remote(), tmp_path, tasks, lambda: None)
    assert [[item["file"] for item in batch] for batch in batches] == [
        ["good.png", "bad.png"], ["bad.png"]
    ]
    assert batches[1][0]["seed"] == 1002
    assert "Wrong hand" in batches[1][0]["prompt"]
    assert (tmp_path / "rejected-bad.png").exists()
    assert store.read(tmp_path / "bad.png.review.json")["pass"]
def test_image_smoke_reviews_one_real_artifact_then_releases_rental(monkeypatch, tmp_path):
    import json
    sequence = []

    class Remote:
        def push(self, files):
            assert len(store.read(files[0])) == 1

        def phase(self, command, seconds):
            assert "stills" in command
            sequence.append("generated")

        def pull(self, names, destination):
            assert names == ["smoke-image.png", "events.jsonl"]
            (destination / names[0]).write_bytes(b"fake generated image")
            (destination / names[1]).write_text(json.dumps({"stage":"still_done", "seconds":4.2}))

    @contextmanager
    def rental(folder, ident, check, *, image_only):
        assert image_only
        sequence.append("rented")
        yield Remote()
        sequence.append("destroyed")

    def review(files, prompt):
        assert files[0].is_file() and files[1].is_file()
        sequence.append("reviewed")
        return {"pass": False, "reason": "Door missing"}

    monkeypatch.setattr(raghu_pipeline, "required", lambda name: "configured")
    monkeypatch.setattr(raghu_pipeline, "rent", rental)
    monkeypatch.setattr(raghu_pipeline, "review_still", review)
    raghu_pipeline.smoke("fixture", tmp_path, "image", lambda: None, lambda *args: None)
    assert sequence == ["rented", "generated", "reviewed", "destroyed"]
    assert store.read(tmp_path / "raghu-smoke.json")["visual_review"] == {"pass":False, "reason":"Door missing"}


def test_still_style_blocks_3d_even_when_subject_is_correct():
    from app.raghu_services import StillAssessment, still_verdict
    good_subject = {"content_matches": True, "visual_defects": [],
                    "observations": "One man stands with a torch beside the door."}
    for style in ("3d_like", "mixed"):
        assert still_verdict(StillAssessment(style=style, **good_subject))["pass"] is False
    assert still_verdict(StillAssessment(style="illustrated_2d", **good_subject))["pass"] is True


def test_still_review_runs_while_next_image_is_being_generated(monkeypatch, tmp_path):
    from threading import Event
    first_review_started = Event()
    second_generation_started = Event()
    first_review_finished = Event()
    tasks = [
        {"file": "first.png", "refs": [], "prompt": "First", "seed": 1},
        {"file": "second.png", "refs": [], "prompt": "Second", "seed": 2},
    ]

    class Remote:
        def push(self, files):
            pass

        def stream_stills(self, manifest_name, names, destination):
            first, second = names
            (destination / first).touch()
            yield first
            second_generation_started.set()
            assert first_review_started.wait(2)
            assert not first_review_finished.is_set()
            (destination / second).touch()
            yield second

    def assess(files, prompt):
        if files[0].name == "first.png":
            first_review_started.set()
            assert second_generation_started.wait(2)
        else:
            first_review_finished.set()
        return {"pass": True, "reason": "Matches", "style": "illustrated_2d"}

    monkeypatch.setattr(raghu_pipeline, "review_still", assess)
    raghu_pipeline.review_stills(Remote(), tmp_path, tasks, lambda: None)
    assert first_review_finished.is_set()


def test_review_check_uses_identical_generated_image_for_both_prompts(monkeypatch, tmp_path):
    seen = []

    def assess(files, prompt):
        seen.append(files[0].read_bytes())
        matching = "railway carriage" not in prompt
        return {"pass": False, "style": "illustrated_2d", "content_matches": matching,
                "reason": "The flashlight floats below his hand." if matching else "No railway carriage"}

    monkeypatch.setattr(raghu_pipeline, "review_still", assess)
    raghu_pipeline.review_check(tmp_path, lambda: None, lambda *args: None)
    assert len(seen) == 2 and seen[0] == seen[1] == (tmp_path / "review-check.png").read_bytes()
    assert store.read(tmp_path / "raghu-review-check.json")["passed"]


def test_vision_requests_a_schema_and_reads_only_final_text(monkeypatch):
    from app import raghu_services

    def gemini(model, endpoint, payload):
        config = payload["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        assert set(config["responseJsonSchema"]["required"]) == {"decision", "reason"}
        return {"candidates": [{"content": {"parts": [
            {"thought": True, "text": "ignored"},
            {"text": '{"decision":"retake",'},
            {"text": '"reason":"The torch floats."}'},
        ]}}]}

    monkeypatch.setenv("GEMINI_VISION_MODEL", "fixture")
    monkeypatch.setattr(raghu_services, "gemini", gemini)
    assert raghu_services.review_continuity([], "Check the cut") == {
        "pass": False, "reason": "The torch floats."}


def test_minor_framing_does_not_block_a_usable_image():
    from app.raghu_services import StillAssessment, still_verdict
    findings = {"style": "illustrated_2d", "content_matches": True,
                "visual_defects": [], "minor_findings": ["Slightly too close to the gate."],
                "observations": "The shot remains usable and the character can approach the gate."}
    accepted = still_verdict(StillAssessment(**findings))
    assert accepted["pass"]
    assert accepted["minor_findings"] == findings["minor_findings"]
    findings["visual_defects"] = ["The flashlight floats below his hand."]
    assert not still_verdict(StillAssessment(**findings))["pass"]


def test_minor_still_difference_gets_one_retake_then_passes(monkeypatch, tmp_path):
    from app import raghu_services

    item = {"file": "still-001.png", "seed": 1, "prompt": "Kamla near the chest.", "refs": []}
    (tmp_path / item["file"]).write_bytes(b"original")
    difference = raghu_services.StillAssessment(
        style="illustrated_2d", content_matches=True, visual_defects=[],
        retake_findings=["One fewer bangle on her wrist."], observations="The shot and action are clear.")
    verdict = raghu_services.still_verdict(difference)
    assert not verdict["pass"] and verdict["minor_retake"]
    assert not raghu_services.still_verdict(
        difference.model_copy(update={"visual_defects": ["The wrong character appears."]}))["minor_retake"]

    monkeypatch.setattr(raghu_pipeline, "review_still", lambda *args: verdict.copy())
    monkeypatch.setattr(raghu_pipeline, "manifest", lambda *args: None)
    retakes = []

    class Remote:
        def stream_stills(self, manifest, names, folder):
            for name in names:
                retakes.append(name)
                (folder / name).write_bytes(b"retake")
                yield name

    assert raghu_pipeline.review_stills(Remote(), tmp_path, [item], lambda: None) == {}
    assert retakes == [item["file"]]
    report = store.read(tmp_path / (item["file"] + ".review.json"))
    assert report["pass"] and report["accepted_after_retake"]


def test_motion_3d_blocks_otherwise_correct_action(monkeypatch):
    from app import raghu_services

    monkeypatch.setattr(raghu_services, "vision_json", lambda *args, validate: validate({
        "style": "mixed", "action_matches": True, "continuity_matches": True,
        "blocking_defects": [], "observations": "The face becomes photoreal in the last frame.",
    }))
    verdict = raghu_services.review_motion([], "Check the frames")
    assert verdict["pass"] is False
    assert "mixed" in verdict["reason"]


def test_vision_retries_invalid_json_and_invalid_schema(monkeypatch):
    from app import raghu_services

    replies = iter(("not JSON", '{"decision":true}', '{"decision":"pass","reason":"Both frames match."}'))
    monkeypatch.setattr(raghu_services, "required", lambda _: "fixture")
    monkeypatch.setattr(raghu_services.time, "sleep", lambda _: None)
    monkeypatch.setattr(raghu_services, "gemini", lambda *args: {
        "candidates": [{"content": {"parts": [{"text": next(replies)}]}}],
    })
    assert raghu_services.review_continuity([], "Check the cut") == {"pass": True, "reason": "Both frames match."}


def test_flagged_still_gets_retake_despite_another_pending_review(monkeypatch, tmp_path):
    batches = []
    tasks = [{"file": name, "prompt": name, "seed": 1} for name in ("flagged.png", "pending.png")]
    for item in tasks:
        (tmp_path / item["file"]).write_text("original")

    class Remote:
        def push(self, files):
            batches.append(store.read(files[0]))

        def stream_stills(self, manifest_name, names, destination):
            for name in names:
                (destination / name).write_text("retake")
                yield name

    def assess(folder, item, check):
        name = item["file"]
        if name == "pending.png":
            raise raghu_pipeline.GeminiUnavailable("Invalid provider response")
        result = {"pass": (folder / name).read_text() == "retake", "reason": "Missing rope"}
        store.save(folder / f"{name}.review.json", result)
        return result

    monkeypatch.setattr(raghu_pipeline, "assess_still", assess)
    with pytest.raises(raghu_pipeline.GeminiUnavailable, match="pending.png"):
        raghu_pipeline.review_stills(Remote(), tmp_path, tasks, lambda: None)
    assert [item["file"] for item in batches[0]] == ["flagged.png"]
    assert "Missing rope" in batches[0][0]["prompt"]
    assert (tmp_path / "pending.png").read_text() == "original"
    assert (tmp_path / "flagged.png").read_text() == "retake"


def test_motion_queue_prefetches_next_batch_and_preserves_failed_review(monkeypatch, tmp_path):
    from threading import Event

    queued_next = Event()
    submissions = []

    class Remote:
        def push(self, files):
            batch = store.read(files[0])["shots"]
            submissions.append(batch)
            if batch[0]["id"] == "006":
                queued_next.set()

        def phase(self, command, seconds):
            pass

        def pull(self, names, folder):
            (folder / names[0]).write_bytes(b"motion clip")

    def review(folder, shot, successor):
        ident = shot["id"]
        if ident == "001":
            assert queued_next.wait(2)
        result = {"pass": ident != "001", "reason": "Face drift"}
        store.save(folder / f"motion-{ident}.review.json", result)
        return result

    monkeypatch.setattr(raghu_pipeline, "validate_motion", review)
    shots = [{"id": f"{i:03}", "motion": "Walk forward into the room."} for i in range(1, 7)]
    for shot in shots:
        (tmp_path / f"still-{shot['id']}.png").write_bytes(b'fixture opening image')
    blocked = raghu_pipeline.render_motion_queue(Remote(), tmp_path, shots, {}, lambda: None, lambda *args: None)
    assert [[item["id"] for item in batch] for batch in submissions] == [
        ["001", "002", "003", "004", "005"], ["006"], ["001"],
    ]
    assert "Face drift" in submissions[2][0]["video_prompt"]
    assert (tmp_path / "rejected-shot-001.mp4").exists()
    assert blocked == ["001: Face drift"]


def test_motion_queue_resumes_a_saved_rejection_as_a_retake(monkeypatch, tmp_path):
    submissions = []

    class Remote:
        def push(self, files):
            submissions.extend(store.read(files[0])["shots"])

        def phase(self, command, seconds):
            pass

        def pull(self, names, folder):
            (folder / names[0]).write_bytes(b"retake")

    def review(folder, shot, successor):
        clip = folder / "shot-001.mp4"
        result = {"pass": clip.read_bytes() == b"retake", "reason": "The camera pulls back."}
        store.save(folder / "motion-001.review.json", result)
        return result

    monkeypatch.setattr(raghu_pipeline, "validate_motion", review)
    (tmp_path / "shot-001.mp4").write_bytes(b"first attempt")
    shots = [{"id": "001", "motion": "She reaches toward the chest. The camera is locked."}]
    for shot in shots:
        (tmp_path / f"still-{shot['id']}.png").write_bytes(b'fixture opening image')
    assert raghu_pipeline.render_motion_queue(Remote(), tmp_path, shots, {}, lambda: None, lambda *args: None) == []
    assert len(submissions) == 1
    assert submissions[0]["seed"] == 56401
    assert "The camera pulls back." in submissions[0]["video_prompt"]
    assert (tmp_path / "rejected-shot-001.mp4").read_bytes() == b"first attempt"


def test_simple_motion_rewrites_only_the_failed_shot_twice(monkeypatch, tmp_path):
    submissions = []
    rewrites = []

    class Remote:
        def push(self, files):
            submissions.append(store.read(files[0])["shots"])

        def phase(self, command, seconds):
            pass

        def pull(self, names, folder):
            (folder / names[0]).write_bytes(b"clip")

    def review(folder, shot, successor):
        result = {"pass": shot["id"] == "002", "reason": "The lid remains closed."}
        store.save(folder / f"motion-{shot['id']}.review.json", result)
        return result

    def rewrite(sheet, brief, reason, *, motion):
        assert motion
        rewrites.append(reason)
        return f"Keep the chest lid open, correction {len(rewrites)}."

    monkeypatch.setattr(raghu_pipeline, "validate_motion", review)
    monkeypatch.setattr(raghu_pipeline.media, "ff", lambda *args: None)
    monkeypatch.setattr(raghu_pipeline, "rewrite_still_prompt", rewrite)
    shots = [{"id": ident, "motion": "Raise the lid.", "simple_prompt": True} for ident in ("001", "002")]
    for shot in shots:
        (tmp_path / f"still-{shot['id']}.png").write_bytes(b'fixture opening image')
    assert raghu_pipeline.render_motion_queue(Remote(), tmp_path, shots, {}, lambda: None, lambda *args: None) == [
        "001: The lid remains closed."
    ]
    assert [[item["id"] for item in batch] for batch in submissions] == [["001", "002"], ["001"], ["001"]]
    assert [batch[0]["video_prompt"] for batch in submissions[1:]] == [
        "Keep the chest lid open, correction 1.", "Keep the chest lid open, correction 2."
    ]
    assert (tmp_path / "shot-002.mp4").is_file()


@pytest.mark.parametrize("review_recovers", [True, False])
def test_review_outage_parks_gpu_while_retrying_saved_work(monkeypatch, tmp_path, review_recovers):
    events = []
    now = [0.0]
    pending = [False]
    checks = [0]
    shot = {"id": "001", "transition": "continuous", "motion": "Lift the lid."}

    class Remote:
        def stop_for_recovery(self):
            events.append("stop")

        def resume_after_review(self, seconds):
            events.append(("restart", seconds))

    @contextmanager
    def lease(*args, **kwargs):
        yield Remote()

    def run_on_rental(*args):
        events.append("render")
        if not pending[0]:
            pending[0] = True
            (tmp_path / "shot-001.mp4").touch()
            raise raghu_pipeline.GeminiUnavailable("view unavailable")

    def review(*args):
        events.append(("review", now[0]))
        checks[0] += 1
        if not review_recovers or checks[0] == 1:
            raise raghu_pipeline.GeminiUnavailable("view unavailable")

    monkeypatch.setattr(raghu_pipeline, "still_batches", lambda plan: ([], []))
    monkeypatch.setattr(raghu_pipeline, "motion_signature", lambda *args: "ready" if pending[0] else None)
    monkeypatch.setattr(raghu_pipeline, "rent", lease)
    monkeypatch.setattr(raghu_pipeline, "generate_on_rental", run_on_rental)
    monkeypatch.setattr(raghu_pipeline, "validate_motion", review)
    monkeypatch.setattr(raghu_pipeline.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(raghu_pipeline.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay))
    args = (tmp_path, "job", {"shots": [shot]}, lambda: None, lambda *args: None)
    if review_recovers:
        raghu_pipeline.generate(*args)
        assert events == ["render", "stop", ("review", 15.0), ("review", 30.0), ("restart", 30.0), "render"]
    else:
        with pytest.raises(raghu_pipeline.GeminiUnavailable):
            raghu_pipeline.generate(*args)
        assert events == ["render", "stop", ("review", 15.0), ("review", 30.0), ("review", 45.0)]


def test_vast_recovery_stops_and_restarts_same_rental(monkeypatch, tmp_path):
    from app import raghu_vast as vast
    monkeypatch.setattr(vast, "require_resume_credit", lambda: None)

    now = [0]
    status = ["running"]
    calls = []
    rental = {"id": 42, "label": "railway-raghu-fixture", "destroyed": False,
              "ssh_host": "host.example", "ssh_port": 2222}
    journal = tmp_path / "raghu-rental.json"
    store.save(journal, rental)
    monkeypatch.setattr(vast.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(vast.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(vast, "instances", lambda: [{**rental, "actual_status": status[0],
        "intended_status": "stopped" if status[0] == "exited" else "running"}])
    monkeypatch.setattr(vast.Remote, "phase", lambda *args: None)

    def operation(action, ident):
        assert ident == 42
        calls.append(action)
        status[0] = "exited" if action == "stop" else "running"

    monkeypatch.setattr(vast, "request_instance_state", operation)
    remote = vast.Remote(rental, tmp_path / "key", tmp_path, 300, lambda: None)
    remote.journal = journal
    remote.pause_and_restart(lambda *args: None, 42)
    assert calls == ["stop", "start"]
    assert now[0] == 60
    assert not store.read(journal).get("recovery_stop")

    import pytest
    with pytest.raises(RuntimeError, match="is stopped"):
        remote.pause_and_restart(lambda *args: None, 42)
    assert calls == ["stop", "start", "stop"]
    assert store.read(journal)["recovery_stop"]


def test_repair_uses_rejected_batch_and_keeps_only_validated_rewrite(monkeypatch, tmp_path):
    from app import raghu_services as services

    def make_shot(destination):
        hall = services.SceneState(location="HALL", cast=["MEERA"], staging="Meera faces the door.", props={})
        return services.Shot(narration="मीरा ने दरवाज़ा खोला।", still="Meera stands in the entrance hall.",
                             motion="Meera opens the door and visibly crosses the threshold.",
                             cast=["MEERA"], location="HALL", start=hall,
                             end=hall.model_copy(update={"location": destination}), transition="continuous")

    wrong, fixed = make_shot("STAIRS"), make_shot("ROOM")
    prompts = []

    def write(schema, prompt):
        assert not (tmp_path / "batch.json").exists()
        prompts.append(prompt)
        return services.Act(shots=[wrong if len(prompts) == 1 else fixed])

    monkeypatch.setattr(services, "write", write)
    def validate(batch):
        return services.sequence_errors(batch.shots, [fixed.narration], None, {"MEERA"},
                                        {"HALL", "STAIRS", "ROOM"}, {frozenset(("HALL", "ROOM"))})

    result = services.checkpoint(tmp_path, "batch.json", services.Act, "Locked map.", lambda: None, validate)
    assert result.shots == [fixed]
    assert "Rejected draft:" in prompts[1]
    assert 'Allowed destinations from HALL: [\'ROOM\']' in prompts[1]
    assert '"location":"STAIRS"' in prompts[1]
    assert store.read(tmp_path / "batch.json")["shots"][0]["end"]["location"] == "ROOM"


def test_continuous_cut_carries_cast_props_and_staging_from_previous_shot():
    from app import raghu_services as services
    hall = services.SceneState(location="HALL", cast=["MEERA"], staging="Meera faces the door.",
                               props={"diary": "closed in her hand"})
    first = services.Shot(narration="मीरा ने दरवाज़ा खोला।", still="Meera stands near the door.",
                          motion="Meera opens the wooden door.", cast=["MEERA"], location="HALL",
                          start=hall, end=hall, transition="continuous")
    generated = hall.model_copy(update={"staging": "Meera is facing the door.", "props": {}})
    following = first.model_copy(update={"start": generated})
    services.carry_continuous_state([following], first)
    assert following.start == first.end
    assert following.start is not first.end


def test_planning_only_saves_a_board_without_gpu_or_media_dependencies(monkeypatch, tmp_path):
    monkeypatch.setenv("RAGHU_TARGET_SECONDS", "150")
    monkeypatch.setenv("RAGHU_PLAN_ONLY", "true")
    monkeypatch.setattr(raghu_pipeline, "required", lambda name: "set")
    board = {"script": "मीरा ने दरवाज़ा खोला।", "shots": [{"id": "001"}]}
    monkeypatch.setattr(raghu_pipeline, "plan", lambda *args: board)

    def forbidden(*args):
        raise AssertionError("Planning-only jobs cannot access GPU or media stages")

    for name in ("generate", "narration", "edit", "production_credentials"):
        monkeypatch.setattr(raghu_pipeline, name, forbidden)
    assert raghu_pipeline.work("fixture", tmp_path, {"title": "Test"}, lambda: None,
                               lambda *args: None).endswith("planning only")
    assert store.read(tmp_path / "story.json") == board


def test_storyboard_cleans_prop_labels_but_does_not_hide_an_invented_person():
    from app import raghu_services as services

    state = services.SceneState(location="HALL", cast=["MEERA", "bangles", "silver-pendant"],
                               staging="Meera studies the mirror.", props={"silver_pendant": "on her neck"})
    beat = services.Beat(line="मीरा ने आईने में देखा।", framing="Side view of Meera by the mirror.",
                         action="Meera turns toward the mirror.", opening=state, end=state.model_copy(deep=True),
                         visible=["MEERA", "mirror", "SERVANT"])
    board = services.Board(beats=[beat])
    services.clean_prop_labels(board, {"MEERA"})
    shots, errors = services.materialize(board, None)
    assert not errors
    assert shots[0].start.cast == ["MEERA"]
    assert shots[0].end.props == {"silver_pendant": "on her neck"}
    assert shots[0].cast == ["MEERA", "SERVANT"]
    assert services.sequence_errors(shots, [beat.line], None, {"MEERA"}, {"HALL"}, set()) == [
        "Shot 1 uses unknown character tags ['SERVANT']. Use ['MEERA']."]


def test_motion_uses_edited_ending_at_the_actual_last_frame(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGHU_MOTION_MODEL", "ltx")
    monkeypatch.setenv("RAGHU_LTX_END_FRAME", "true")
    shot = {"id": "012", "motion": "Meera walks through the doorway.",
            "end": {"staging": "Meera stands on the far side of the doorway."}}
    ending, = raghu_pipeline.end_frame_tasks([shot])
    assert ending["refs"] == ["results/still-012.png"]
    assert "far side" in ending["prompt"]
    request, = raghu_pipeline.motion_plan([shot])["shots"]
    assert request["starting_image_file"] == "still-012.png"
    assert request["ending_image_file"] == ending["file"] == "end-012.png"
    assert request["ending_frame"] == request["frames"] - 1
    (tmp_path / "shot-012.mp4").write_bytes(b"video")
    (tmp_path / "still-012.png").write_bytes(b"first")
    assert raghu_pipeline.motion_signature(tmp_path, shot, None) is None
    last = tmp_path / ending["file"]
    last.write_bytes(b"end")
    signature = raghu_pipeline.motion_signature(tmp_path, shot, None)
    last.write_bytes(b"replacement")
    assert signature != raghu_pipeline.motion_signature(tmp_path, shot, None)


def test_approved_ltx_uses_only_opening_and_written_ending(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGHU_MOTION_MODEL", "ltx")
    monkeypatch.delenv("RAGHU_LTX_END_FRAME", raising=False)
    shot = {"id": "012", "motion": "Locked wide view. Meera takes two steps across the doorway.",
            "end": {"staging": "Both feet are inside the hall."}}
    request, = raghu_pipeline.motion_plan([shot])["shots"]
    assert request["starting_image_file"] == "still-012.png"
    assert "ending_image_file" not in request
    assert request["frames"] == 121 and request["fps"] == 24
    assert "Both feet are inside the hall." in request["video_prompt"]
    (tmp_path / "shot-012.mp4").write_bytes(b"video")
    (tmp_path / "still-012.png").write_bytes(b"opening")
    assert raghu_pipeline.motion_signature(tmp_path, shot, None)


def test_crossing_ending_uses_destination_room_and_preserves_opening_identity(tmp_path):
    shot = {"id": "001", "motion": "Kamla steps from the porch into the hall.",
            "start": {"location": "PORCH"},
            "end": {"location": "HALL", "staging": "Kamla stands inside beside the closed door."}}
    (tmp_path / "still-001.png").write_bytes(b"opening")
    destination = tmp_path / "background-HALL.png"
    destination.write_bytes(b"hall")

    ending, = raghu_pipeline.end_frame_tasks([shot], tmp_path)

    assert ending["refs"] == ["results/background-HALL.png", "results/still-001.png"]
    assert "destination room" in ending["prompt"]
    assert "closed door" in ending["prompt"]
    assert shot["motion"] not in ending["prompt"]
    assert "closed door" in ending["review_prompt"]
    signature = raghu_pipeline.end_frame_source_signature(tmp_path, ending)
    destination.write_bytes(b"revised hall")
    assert raghu_pipeline.end_frame_source_signature(tmp_path, ending) != signature
