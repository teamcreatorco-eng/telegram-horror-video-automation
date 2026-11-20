"""Author a Hindi story, then use the original Clone prompt builder."""

import json
import hashlib
import os
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, create_model

from .raghu_sound import parse_sound_design
from . import clone_handoff, raghu_services as writing, store
from .youtube_clone.pipeline.models import new_project
from .youtube_clone.pipeline.one_minute import validate
from .youtube_clone.pipeline.prompts import ROOT, STYLE_LOCK, render


class Character(BaseModel):
    tag: str
    name: str
    look_summary: str
    voice_description: str
    voice: Literal["female", "male"]


class Background(BaseModel):
    tag: str
    location_name: str
    look_summary: str


class ShotDirection(BaseModel):
    scene_number: int
    title: str
    background_tag: str
    character_tags: list[str] = Field(max_length=3)
    first_frame_description: str
    visual_action: str
    camera: str
    effects: str
    sound_design: str
    end_pose: str


class Scene(ShotDirection):
    speaker: str
    speech: str


class Beat(BaseModel):
    scene_number: int
    speaker: str
    speech: str
    event: str = Field(min_length=8)


class LockedScript(BaseModel):
    title: str
    summary: str
    entity: str
    beats: list[Beat]


class Board(BaseModel):
    characters: list[Character] = Field(min_length=1, max_length=2)
    backgrounds: list[Background] = Field(min_length=1, max_length=4)
    scenes: list[ShotDirection]


class Issue(BaseModel):
    scene_number: int = Field(ge=1, le=120)
    reason: str = Field(min_length=4)


class Review(BaseModel):
    issues: list[Issue] = Field(max_length=120)
    replacements: list[ShotDirection] = Field(max_length=120)


class Draft(BaseModel):
    title: str
    summary: str
    entity: str
    characters: list[Character] = Field(min_length=1, max_length=2)
    backgrounds: list[Background] = Field(min_length=1, max_length=4)
    scenes: list[Scene]


def enabled(seconds):
    return 30 <= seconds <= 600 and seconds % 5 == 0 and os.getenv("RAGHU_PLANNER") in {"clone", "youtube_clone"}


STORY_SETTINGS = (
    "a closed railway signal cabin", "a hillside orchard packing shed", "an old cinema projection room",
    "a riverside ferry ticket office", "a village library after closing", "an overnight tile workshop",
    "a shuttered mountain post office", "a rooftop water-tank landing", "a dry stepwell courtyard",
    "a roadside radio repair shop", "a rain-soaked bus depot", "an old observatory control room",
)


def setting_direction(folder):
    path = folder / "clone-story-setting.json"
    if path.is_file():
        saved = store.read(path)
    else:
        job = store.get(folder.name) if re.fullmatch(r"[0-9a-f]{32}", folder.name) else None
        history = store.script_history(job["owner"], folder.name) if job else []
        recent = [json.loads(row["concept"]).get("clone_setting") for row in history[:8]]
        unused = [setting for setting in STORY_SETTINGS if setting not in recent]
        choice = int(hashlib.sha256(folder.name.encode()).hexdigest()[:8], 16) % len(unused)
        saved = {"setting": unused[choice], "recent": [setting for setting in recent if setting]}
        store.save(path, saved)
    seed = int(hashlib.sha256(folder.name.encode()).hexdigest()[:12], 16)
    objects = ("a radio dial", "a ticket punch", "a measuring tape", "a brass weighing scale", "a lantern", "a wall clock", "a spool of thread", "a handwritten parcel label")
    looks = ("an adult woman with a long angular face, short wavy hair and a rust work jacket", "a middle-aged man with a broad face, cropped hair and an indigo work shirt", "an older woman with a square face, silver braid and a maroon cotton sari", "a young adult man with a narrow face, curly hair and a cream kurta")
    lights = ("dry late-afternoon amber sunlight", "warm lamplight with cool neutral shadows", "overcast daylight with earthy browns", "clear moonlight with one warm practical lamp")
    variation = (f"For an unspecified topic use {objects[seed % len(objects)]} as the central clue. "
                 f"Design the lead around {looks[(seed // 11) % len(looks)]}. "
                 f"Use {lights[(seed // 37) % len(lights)]}. "
                 "Explicit user story requirements take priority. These are new-story choices; keep them consistent within the story. "
                 "Do not reuse the example's bangles plot, teal salwar woman, or haunted room by default. "
                 "Change face shape, hairstyle, age, outfit and occupation, not just the name. "
                 "Express the chosen location through its distinctive working equipment and architecture; do not turn every setting into the same ruined room. ")
    recent_text = ", ".join(saved["recent"]) or "none recorded"
    return (variation + f"Use this story location unless the supplied title explicitly requires a different one: {saved['setting']}. "
            f"Recent settings to avoid: {recent_text}. Establish the chosen physical space early and "
            "keep its layout consistent within this story. Camera reverses are allowed within that space.")


def assemble(draft, folder, title, seconds=60):
    project = new_project(folder.name, "B", title, 0)
    idea = {"index": 1, "title": title, "hook": draft.scenes[0].speech, "entity_archetype": draft.entity}
    project["idea"].update(status="done", items=[idea], selected_index=1, selected=idea)
    project.update(style_reference=STYLE_LOCK, writing_path="DeepSeek authoring using the Clone workflow")
    style = ("Landscape 16:9, semi-realistic flat 2D Indian cel animation, clean bold outlines, natural "
             "adult anatomy, story-specific lighting and weather, no lettering, logos, CGI or watermark.")
    for character in draft.characters:
        item = character.model_dump(exclude={"voice"})
        item.update(full_prompt=f"{character.look_summary}. Neutral full-body three-quarter pose and "
                    f"front-facing expression inset on a grey studio ground. {style}", status="pending", error=None)
        project["characters"].append(item)
    for background in draft.backgrounds:
        item = background.model_dump()
        item.update(full_prompt=f"Clean empty location plate. {background.look_summary} {style} "
                    "No figures or silhouettes.", status="pending", error=None)
        project["backgrounds"].append(item)
    lines = []
    for scene in draft.scenes:
        if (not re.search(r"[\u0900-\u097f]", scene.speech) or re.search(r"[A-Za-z\n]", scene.speech)
                or len(scene.speech.split()) > 16 or not scene.visual_action.strip()):
            raise ValueError(f"Scene {scene.scene_number} requires a short Hindi line and a visible action")
        if scene.speaker == "none" or len(set(scene.character_tags)) != len(scene.character_tags):
            raise ValueError(f"Scene {scene.scene_number} requires one speaker and unique cast tags")
        lines.append(scene.speech)
        item = scene.model_dump(exclude={"speech", "end_pose"})
        item.update(narration_text=scene.speech if scene.speaker == "NARRATOR" else "",
                    devanagari_dialogue=scene.speech if scene.speaker != "NARRATOR" else "")
        project["scenes"].append(item)
    script = "\n".join(lines)
    words = len(script.split())
    count = seconds // 5
    minutes = seconds / 60
    project["script"].update(status="done", language="Hindi", duration_minutes=minutes, word_count=words,
                             monster=draft.entity, raw_text=script, raw_output=script)
    project["raw_stages"] = {
        "story_prompt": render("story", dict(TITLE=title, HOOK=lines[0], LANGUAGE="Hindi",
                                            DURATION_MINUTES=f"{minutes:g}", WORD_COUNT=round(minutes * 150), MONSTER=draft.entity)),
        "characters_prompt": render("characters", {"SCRIPT": script}),
        "backgrounds_prompt": render("backgrounds", {"SCRIPT": script}),
        "scenes_prompt": render("scenes", dict(DURATION_MINUTES=f"{minutes:g}", MINIMUM_SCENES=min(7, count), SCENE_COUNT=count,
                                              SCRIPT=script, CHARACTERS=json.dumps(project["characters"], ensure_ascii=False),
                                              BACKGROUNDS=json.dumps(project["backgrounds"], ensure_ascii=False))),
    }
    validate(project, scene_count=count)
    return project


def renderer_plan(project, draft, seconds=60):
    digest = clone_handoff._hash(clone_handoff._canonical(project))
    preview = clone_handoff.project(project, digest, seconds=seconds)
    cast = {item.tag: item for item in draft.characters}
    shots = []
    for source, render_shot, row in zip(project["scenes"], preview["shots"], draft.scenes, strict=True):
        actor = cast.get(source["speaker"])
        tags = [tag[1:] for tag in source["character_tags"]]
        opening_tags = [tag[1:] for tag in source.get("opening_character_tags", source["character_tags"])]
        location = source["background_tag"][1:]
        motion = render_shot["ltx"]["prompt"]
        # The current LTX graph delivers picture. Existing narration stages supply the audio.
        before, _, rest = motion.partition("### LIP-SYNC CONTROL & AUDIO\n")
        if rest:
            motion = before + "### NEGATIVE RESTRICTIONS" + rest.split("### NEGATIVE RESTRICTIONS", 1)[1]
        score, cues = parse_sound_design(row.sound_design)
        shots.append(dict(
            id=f"{row.scene_number:03d}", narration=row.speech,
            speaker=actor.tag[1:] if actor else "NARRATOR",
            speaker_description=actor.look_summary if actor else "", dialogue_voice=actor.voice if actor else "",
            still=render_shot["flux"]["prompt"], camera=row.camera, visual_action=row.visual_action, motion=f"{row.visual_action} Camera: {row.camera}.",
            clone_motion=motion, simple_prompt=True, location=location, cast=tags,
            start=dict(location=location, cast=opening_tags, staging=row.first_frame_description, props={}),
            end=dict(location=location, cast=tags, staging=row.end_pose, props={}),
            transition="continuous", time_cut_cue="", score=score, sound_cues=cues,
            sound_design=row.sound_design,
        ))
    result = dict(version=4, planner="youtube_clone", title=project["idea"]["selected"]["title"], seconds=seconds,
                  script=project["script"]["raw_text"], synopsis=draft.summary, shots=shots,
                  cast=[dict(tag=a["tag"][1:], prompt=a["full_prompt"], identity_description=a["look_summary"])
                        for a in project["characters"]],
                  locations=[dict(tag=b["tag"][1:], prompt=b["full_prompt"]) for b in project["backgrounds"]])
    return result, preview


def check_script(script, seconds=60, *, editorial_checks=True):
    count = seconds // 5
    if len(script.beats) != count:
        raise ValueError(f"Write exactly {count} ordered beats")
    words = 0
    dialogue = set()
    for number, beat in enumerate(script.beats, 1):
        line_words = len(beat.speech.split())
        if (beat.scene_number != number or (editorial_checks and line_words > 16) or not re.search(r"[\u0900-\u097f]", beat.speech)
                or re.search(r"[A-Za-z\r\n]", beat.speech)):
            raise ValueError(f"Beat {number} must have its exact number and one short Hindi line")
        if beat.speaker != "NARRATOR":
            if not re.fullmatch(r"@CHAR-\d{2}-[A-Z][A-Z0-9-]*", beat.speaker):
                raise ValueError(f"Beat {number} must use NARRATOR or its exact character tag")
            dialogue.add(beat.speaker)
        words += line_words
    word_limit = int(seconds * 155 / 60)
    dialogue_limit = count // 4
    direct_lines = sum(b.speaker != "NARRATOR" for b in script.beats)
    if editorial_checks and words > word_limit:
        raise ValueError(f"The script has {words} words. Shorten to {word_limit} or fewer.")
    if editorial_checks and (len(dialogue) > 2 or direct_lines > dialogue_limit):
        raise ValueError(f"The script has {direct_lines} dialogue lines across {len(dialogue)} speakers. "
                         f"Use at most {dialogue_limit} dialogue lines and at most two speakers.")


def assemble_board(script, board, folder, title, seconds=60):
    if len(board.scenes) != len(script.beats):
        raise ValueError(f"Return exactly {len(script.beats)} shot directions")
    cast_tags = {character.tag for character in board.characters}
    missing = {beat.speaker for beat in script.beats if beat.speaker != "NARRATOR"} - cast_tags
    if missing:
        raise ValueError(f"Add cast entries with these exact locked speaker tags: {', '.join(sorted(missing))}. "
                         "Do not replace them with a new tag based on the character name.")
    for beat, shot in zip(script.beats, board.scenes, strict=True):
        if beat.speaker != "NARRATOR" and beat.speaker not in shot.character_tags:
            raise ValueError(f"Shot {beat.scene_number} must include its locked speaker {beat.speaker} in character_tags.")
    rows = [Scene(**shot.model_dump(), speaker=beat.speaker, speech=beat.speech)
            for beat, shot in zip(script.beats, board.scenes, strict=True)]
    draft = Draft(title=title, summary=script.summary, entity=script.entity,
                  characters=board.characters, backgrounds=board.backgrounds, scenes=rows)
    project = assemble(draft, folder, title, seconds)
    return project, renderer_plan(project, draft, seconds)


def repair_board(board, review):
    flagged = [issue.scene_number for issue in review.issues]
    replacements = {shot.scene_number: shot for shot in review.replacements}
    if len(set(flagged)) != len(flagged) or len(replacements) != len(review.replacements) or set(flagged) != replacements.keys():
        raise ValueError("Every flagged scene needs exactly one replacement and no other scene may change")
    if not replacements.keys() <= {shot.scene_number for shot in board.scenes}:
        raise ValueError("Review flagged a scene outside the storyboard")
    return board.model_copy(update={"scenes": [replacements.get(s.scene_number, s) for s in board.scenes]})


def plan(folder, title, choose_topic, check, progress, *, seconds=60):
    final = folder / "raghu-plan-v2.json"
    if final.is_file():
        return store.read(final)
    writing.required("DEEPSEEK_API_KEY")
    writing.required("DEEPSEEK_MODEL")
    count = seconds // 5
    minutes = f"{seconds / 60:g}"
    script_schema = create_model("CloneScript", __base__=LockedScript,
                                 beats=(list[Beat], Field(min_length=count, max_length=count)))
    board_schema = create_model("CloneBoard", __base__=Board,
                                scenes=(list[ShotDirection], Field(min_length=count, max_length=count)))
    attempts = folder / "youtube-clone-attempts.json"
    ledger = store.read(attempts) if attempts.is_file() else {"count": 0}
    responses = ledger.setdefault("deepseek_responses", [])
    stages = ledger.setdefault("stage_attempts", {})
    retry_marker = folder / "raghu-user-retry.json"
    if retry_marker.is_file():
        round_id = store.read(retry_marker)["round"]
        stages = ledger.setdefault("retry_stage_attempts", {}).setdefault(round_id, {})

    def stage(name, schema, prompt, max_tokens, check_value):
        path = folder / f"youtube-clone-{name}.json"
        if path.exists():
            saved = schema.model_validate(store.read(path))
            check_value(saved)
            return saved

        def record_usage(model, usage):
            fields = ("prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens")
            responses.append({"stage": name, "model": model, **{key: usage.get(key) for key in fields}})
            store.save(attempts, ledger)

        correction = ""
        while stages.get(name, 0) < 2:
            check()
            stages[name] = stages.get(name, 0) + 1
            ledger["count"] += 1
            store.save(attempts, ledger)
            try:
                value = writing.write(schema, prompt + correction, max_tokens=max_tokens,
                                      usage=record_usage, attempts=1)
                check_value(value)
            except (writing.PlanningValidationError, ValidationError, ValueError, KeyError) as error:
                correction = "\nFix this validation issue: " + str(error)[:900]
                continue
            check()
            store.save(path, value.model_dump())
            return value
        raise RuntimeError(f"Could not validate Clone {name}.{correction}"[:450])

    examples = json.loads((ROOT.parent / "examples.json").read_text(encoding="utf-8"))
    example_lines = set().union(*(set(example["script"].splitlines()) for example in examples))
    story_template = render("story", dict(TITLE=title, HOOK="Choose a concrete visible impossibility",
                                          LANGUAGE="Hindi", DURATION_MINUTES=minutes, WORD_COUNT=round(seconds * 2.5),
                                          MONSTER="An original folk-horror entity"))
    def build_round(prefix, feedback="", keep_title=None):
        existing_script = (folder / f"youtube-clone-{prefix}script.json").is_file()

        def validate_script(value):
            check_script(value, seconds, editorial_checks=False)
            if not existing_script and sum(beat.speech.strip() in example_lines for beat in value.beats) > 1:
                raise ValueError("Write original Hindi lines and events. The approved scripts are style references.")

        progress("Clone YouTube is simplifying the story after review" if prefix else
                 "Clone YouTube is writing the Hindi script", 21 if prefix else 9)
        script = stage(prefix + "script", script_schema,
                       f"Title brief: {title}. {'Choose a fresh Hindi title.' if choose_topic else 'Keep the exact title.'}\n"
                       f"Write {count} ordered Hindi beats for a {seconds}-second short, near "
                       f"{round(seconds * 140 / 60)} spoken words. Use around 8–12 words per beat, at most 16 "
                       f"and at most {int(seconds * 155 / 60)} words total. Use at most {count // 4} "
                       "direct dialogue lines. Start with an ordinary object visibly breaking "
                       "one physical rule. Use one main location, one recurring clue, at most two adult characters, "
                       "and a final physical payoff caused by the clue. Decide the ending before writing the "
                       "opening. Reserve the last two beats for the payoff and an observable consequence. "
                       "End the central story within the requested duration. A dark ending is fine if the "
                       "central event resolves. Avoid stopping at a discovery, question, or teaser. Use short, spoken Devanagari. "
                       "Use one clearly filmable event per beat and describe it in English. People and recurring "
                       "props keep their last position and condition until an action changes it. Dedicate a beat "
                       "to any important pickup, handover or crossing. Prefer a close-up of a physical clue over "
                       "a character appearing in a different room. After writing, read the beats in order and "
                       "correct lost objects, repeated actions, impossible positioning, and dialogue tagged as narration. "
                       "No story-critical written words, flashbacks or montage. Use NARRATOR for most beats and "
                       "assign stable speaker tags using actual character names, for example @CHAR-01-ASHA. "
                       "Use fresh names, events, and lines.\n" + setting_direction(folder) + "\n"
                       f"{story_template}\n{feedback}\n"
                       "Approved examples of brief Hindi delivery, for cadence only:\n" +
                       "\n".join(line for example in examples for line in example["script"].splitlines()[1:3]),
                       max(1800, count * 220), validate_script)
        selected_title = keep_title or (script.title if choose_topic else title)
        locked_text = "\n".join(f"{b.scene_number}. {b.speaker}: {b.speech}" for b in script.beats)
        dialogue_tags = sorted({b.speaker for b in script.beats if b.speaker != "NARRATOR"})
        templates = [
            render("characters", {"SCRIPT": locked_text}),
            render("backgrounds", {"SCRIPT": locked_text}),
            render("scenes", dict(DURATION_MINUTES=minutes, MINIMUM_SCENES=min(7, count), SCENE_COUNT=count,
                                  SCRIPT=locked_text, CHARACTERS="Define canonical cast for the locked script",
                                  BACKGROUNDS="Define reusable empty location plates")),
        ]
        progress(f"Clone YouTube is staging {count} shots from the locked script", 22 if prefix else 13)
        board = stage(prefix + "board", board_schema,
                  "Stage the locked spoken beats in order. Each opening image must show the instant BEFORE its "
                  "main event. Put that event in visual_action and its physical result in end_pose. If the core beat "
                  "is a sound, give it a matching sound cue and a visible response. Prefer practical, locked camera "
                  "angles. Keep rooms, positions, props and wardrobe stable across cuts. Each dialogue speaker must "
                  "appear under their exact locked tag. Narrator shots have no speaking mouths. Include up to four "
                  "empty plates using #BGD-01-PLACE tags. Follow original Clone templates, leaving the speech unchanged.\n"
                  f"Required cast tags for dialogue: {json.dumps(dialogue_tags)}. Copy each tag exactly into one "
                  "characters entry and each scene where it speaks. These are already assigned IDs, even when "
                  "a name or look suggests a different ID. NARRATOR is an off-screen role. "
                  "Track the recurring prop in the opening and ending of any shot where its state matters. "
                  "The next shot can crop it out, but it cannot change owner, location, or condition across a cut. "
                  "Apply the same rule to which side of a doorway a person occupies and whether the door is open.\n"
                  f"Locked beats: {script.model_dump_json()}\n"
                  f"Templates: {chr(10).join(templates)}\n"
                  f"Approved direction samples: {json.dumps([e['scenes'][:3] for e in examples], ensure_ascii=False)}",
                  max(3000, count * 500), lambda value: assemble_board(script, value, folder, selected_title, seconds))
        return script, selected_title, board

    script, selected_title, board = build_round("")
    # Production has no AI review, repair, or editorial rewrite stage.
    project, (result, preview) = assemble_board(script, board, folder, selected_title, seconds)
    check()
    store.save(folder / "clone-source-plan.json", project)
    store.save(folder / "clone-renderer-preview.json", preview)
    store.save(final, result)
    progress(f"Clone YouTube script and prompts ready · {count}/{count} scenes written", 24)
    return result
