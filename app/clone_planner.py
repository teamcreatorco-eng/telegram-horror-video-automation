"""Adapt the Clone YouTube repo's staged writer to Railway's media inputs."""

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, create_model

from . import raghu_services as writing, store


class Hook(BaseModel):
    line: str = Field(min_length=10)


class Line(BaseModel):
    text: str = Field(min_length=5)
    delivery: Literal["narration", "dialogue"]
    speaker_name: str = ""


class Script(BaseModel):
    summary: str = Field(min_length=20)
    lines: list[Line]


class Character(BaseModel):
    tag: str = Field(pattern=r"^@CHAR-[0-9]{2}-[A-Z][A-Z0-9-]{0,18}$")
    name: str = Field(min_length=1)
    look_summary: str = Field(min_length=8)
    full_prompt: str = Field(min_length=20)
    voice: Literal["female", "male"] = "male"


class Characters(BaseModel):
    characters: list[Character] = Field(min_length=1, max_length=8)


class Background(BaseModel):
    tag: str = Field(pattern=r"^#BGD-[0-9]{2}-[A-Z][A-Z0-9-]{0,18}$")
    location_name: str = Field(min_length=1)
    look_summary: str = Field(min_length=8)
    full_prompt: str = Field(min_length=20)


class Backgrounds(BaseModel):
    backgrounds: list[Background] = Field(min_length=1, max_length=12)


class Scene(BaseModel):
    scene_number: int
    background_tag: str
    character_tags: list[str] = Field(max_length=3)
    opening: str = Field(min_length=12)
    visual_action: str = Field(min_length=10)
    camera: str = Field(min_length=5)
    ending: str = Field(min_length=8)
    end_background_tag: str = ""
    time_cut_cue: str = ""
    score: Literal["none", "unease", "suspense", "dread"] = "suspense"
    sound_cues: list[writing.SoundCue] = Field(default_factory=list, max_length=2)


class SceneBatch(BaseModel):
    scenes: list[Scene]


class SilentActions(BaseModel):
    actions: dict[int, str]


def key(tag):
    return tag[1:]


def direct_dialogue(line):
    text = line.text.strip()
    if line.delivery != "dialogue":
        return text
    quoted = re.search(r"[“\"]([^“”\"]+)[”\"]\s*[।.]?$", text)
    if quoted:
        return quoted[1].strip()
    return re.split(r"\b(?:कहा|बोला|बोली|फुसफुसाया|फुसफुसाई|पूछा|चीखा|चीखी)\s*[,，:：]\s*",
                    text, maxsplit=1)[-1].strip()


def script_errors(script, seconds, count):
    errors = []
    if len(script.lines) != count:
        errors.append(f"Return exactly {count} spoken lines.")
    for n, line in enumerate(script.lines, 1):
        if not re.search(r"[\u0900-\u097f]", line.text) or re.search(r"[A-Za-z\n]", line.text):
            errors.append(f"Line {n} needs a single Hindi line.")
        if line.delivery == "dialogue" and not line.speaker_name.strip():
            errors.append(f"Name the character who speaks line {n}.")
    if sum(line.delivery == "dialogue" for line in script.lines) > (count + 3) // 4:
        errors.append("Use narration for most scenes and keep character dialogue occasional.")
    return errors


def asks_to_speak(action):
    return bool(re.search(r"\b(speaks|whispers|says|replies|shouts|yells|talks|begins to speak|opens .{0,12}mouth to speak)\b",
                          action, re.I))


def scene_errors(batch, start, lines, cast, backgrounds):
    errors = []
    for n, (scene, line) in enumerate(zip(batch.scenes, lines), start):
        visible = set(scene.character_tags)
        if scene.scene_number != n:
            errors.append(f"Scene number must be {n}.")
        if scene.background_tag not in backgrounds or (
            scene.end_background_tag and scene.end_background_tag not in backgrounds
        ):
            errors.append(f"Scene {n} uses an unknown background.")
        if visible - cast.keys() or len(visible) != len(scene.character_tags):
            errors.append(f"Scene {n} must use unique character tags from the supplied list.")
        if line.delivery == "dialogue" and len(visible) == 3 and not any(
            cast[tag].name.strip().casefold() == line.speaker_name.strip().casefold()
            for tag in visible & cast.keys()
        ):
            errors.append(f"Scene {n} needs room in the cast for the locked speaker {line.speaker_name}.")
    return errors


def fix_narrator_actions(batch, lines, stage, batch_number):
    flagged = {scene.scene_number: (scene, line.text) for scene, line in zip(batch.scenes, lines)
               if line.delivery == "narration" and asks_to_speak(scene.visual_action)}
    if not flagged:
        return
    brief = "\n".join(f"Scene {n}: Hindi narration {text}. Opening: {scene.opening}. "
                      f"Draft action: {scene.visual_action}. End: {scene.ending}."
                      for n, (scene, text) in flagged.items())

    def validate(value):
        if value.actions.keys() != flagged.keys() or any(
            len(action.strip()) < 10 or asks_to_speak(action) for action in value.actions.values()
        ):
            return ["Return one silent physical action for each listed scene, using its exact scene number."]
        return []

    corrected = stage(f"silent-actions-{batch_number:03d}", SilentActions,
                      "Keep the supplied Hindi lines, exact scene numbers, opening and ending. "
                      "For each listed narrator scene, replace only the visible speech with one short, silent "
                      "physical reaction or gesture that communicates the same beat. Offscreen voices stay offscreen. "
                      "Return an actions object with scene numbers as keys.\n" + brief,
                      1200, validate)
    for scene in batch.scenes:
        if scene.scene_number in corrected.actions:
            scene.visual_action = corrected.actions[scene.scene_number]


def as_shot(scene, line, cast, backgrounds):
    bg = backgrounds[scene.background_tag]
    actors = [key(tag) for tag in scene.character_tags]
    tag = key(scene.background_tag)
    end_tag = key(scene.end_background_tag) if scene.end_background_tag else tag
    speaker = next((actor for actor in cast.values()
                    if actor.name.strip().casefold() == line.speaker_name.strip().casefold()), None
                   ) if line.delivery == "dialogue" else None
    if speaker and key(speaker.tag) not in actors:
        actors.append(key(speaker.tag))
    still = f"{scene.camera}. {scene.opening} Location layout: {bg.look_summary}."
    if scene.time_cut_cue:
        still += f" Visible time cue: {scene.time_cut_cue}."
    return {
        "id": f"{scene.scene_number:03d}", "narration": line.text,
        "speaker": key(speaker.tag) if speaker else "NARRATOR",
        "speaker_description": speaker.look_summary if speaker else "",
        "dialogue_voice": speaker.voice if speaker else "",
        "still": still, "motion": f"{scene.visual_action} Camera: {scene.camera}.",
        "cast": actors, "location": tag,
        "start": dict(location=tag, cast=actors, staging=scene.opening, props={}),
        "end": dict(location=end_tag, cast=actors, staging=scene.ending, props={}),
        "transition": "time_cut" if scene.time_cut_cue else "continuous",
        "time_cut_cue": scene.time_cut_cue, "score": scene.score,
        "sound_cues": [cue.model_dump() for cue in scene.sound_cues],
    }


def plan(folder, title, seconds, check, report):
    final = folder / "raghu-plan-v2.json"
    if final.exists():
        return store.read(final)
    total = seconds // 5
    minutes = f"{seconds / 60:g}"
    word_limit = round(seconds * 2.5)
    usage_path = folder / "clone-planner-usage.json"
    usage = store.read(usage_path) if usage_path.exists() else {"attempts": 0, "responses": []}

    def record(model, tokens):
        usage["responses"].append({"model": model, **{k: tokens.get(k) for k in
                                   ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens")}})
        store.save(usage_path, usage)

    def stage(name, schema, prompt, max_tokens, validate=lambda _: []):
        def request(model, content):
            usage["attempts"] += 1
            store.save(usage_path, usage)
            return writing.write(model, content, max_tokens=max_tokens, usage=record, attempts=1)

        return writing.checkpoint(folder, f"clone-{name}.json", schema, prompt, check, validate,
                                  attempts=3 if name == "script" else 2, writer=request)

    report("Writing a simple Hindi horror story", 9)
    hook = stage("hook", Hook, f"Write one short original Hindi folk-horror opening hook for {title}. "
                 "Set it in a fictional North Indian place. Open with a visible event or object.", 300)
    story_prompt = writing.template("3", TITLE=title, HOOK=hook.line, LANGUAGE="Hindi",
                                    DURATION_MINUTES=minutes, WORD_COUNT=word_limit,
                                    MONSTER="An original folk-horror entity")
    script_schema = create_model("LockedScript", __base__=Script,
                                 lines=(list[Line], Field(min_length=total, max_length=total)))
    script = stage("script", script_schema, story_prompt +
                   f"\nWrite exactly {total} short Devanagari lines in story order, for five seconds each. "
                   f"Aim for {int(seconds * 2.1)} spoken words overall, with a ceiling of {word_limit}. "
                   "Aim for 8 to 12 words per line, and never exceed 16. Use fewer words on a character's line. "
                   "Most lines are narration. Keep occasional dialogue short and identify its speaker. "
                   "Use simple Hindi, one small cast, one main setting, one physical clue and a clear payoff. "
                   "Introduce the clue in the opening, show what changes, and avoid extra side plots or explanations. "
                   "Each line should correspond to one filmable beat. Do not include speaker names inside spoken text.",
                   min(11000, max(1900, total * 86)), lambda value: script_errors(value, seconds, total))
    for line in script.lines:
        line.text = direct_dialogue(line)
    locked = "\n".join(f"{n}. {line.speaker_name or 'Narrator'} — {line.text}"
                       for n, line in enumerate(script.lines, 1))
    context = f"\n{writing.STYLE_LOCK}\nLocked script:\n{locked}"

    report("Preparing character sheets and empty location references", 11)
    def character_errors(value):
        names = [item.name.strip().casefold() for item in value.characters]
        tags = {item.tag for item in value.characters}
        if len(tags) != len(names) or len(set(names)) != len(names):
            return ["Use unique character tags and names."]
        missing = {line.speaker_name.strip().casefold() for line in script.lines
                   if line.delivery == "dialogue"} - set(names)
        return [f"Add these locked speakers using their exact names: {', '.join(sorted(missing))}."] if missing else []

    characters = stage("characters", Characters, writing.template("4", SCRIPT=locked) + context +
                       "\nUse only named or visibly required adults in the script. For each reference use a single "
                       "neutral full-body pose. Copy each dialogue speaker name exactly from the locked script.",
                       2600, character_errors)
    locations = stage("backgrounds", Backgrounds, writing.template("5", SCRIPT=locked) + context +
                      "\nUse only reusable, empty locations in the script. Provide a separate close plate only when "
                      "the action needs one. Keep permanent props and orientations consistent between angles.",
                      2900, lambda value: ["Duplicate background tags."] if
                      len({item.tag for item in value.backgrounds}) != len(value.backgrounds) else [])
    cast = {item.tag: item for item in characters.characters}
    backgrounds = {item.tag: item for item in locations.backgrounds}
    cast_context = json.dumps({tag: item.name for tag, item in cast.items()}, ensure_ascii=False)
    location_context = json.dumps({tag: item.look_summary for tag, item in backgrounds.items()}, ensure_ascii=False)

    shots = []
    for offset in range(0, total, 12):
        lines = script.lines[offset:offset + 12]
        stop = offset + len(lines)
        report(f"Writing scene prompts · {offset}/{total} scenes ready", 12 + 10 * offset // total)
        text = "\n".join(locked.splitlines()[offset:stop])
        prompt = writing.template("6", DURATION_MINUTES=minutes, MINIMUM_SCENES=len(lines),
                                  SCENE_COUNT=len(lines), SCRIPT=text, CHARACTERS=cast_context,
                                  BACKGROUNDS=location_context)
        batch_schema = create_model("LockedScenes", __base__=SceneBatch,
                                    scenes=(list[Scene], Field(min_length=len(lines), max_length=len(lines))))
        batch = stage(f"scenes-{offset + 1:03d}", batch_schema, prompt +
                      f"\n{writing.STYLE_LOCK}\nFull story summary: {script.summary}"
                      f"\nLocked lines for this batch:\n{text}\nUse scene numbers {offset + 1}–{stop}. "
                      f"Allowed cast: {cast_context}. Allowed locations: {location_context}. "
                      f"Previous shot's visible ending: {shots[-1]['end']['staging'] if shots else 'first scene'}. "
                      "\nRailway renders five seconds of motion from the first image. Use one directly visible action "
                      "or reaction per scene, with a short camera instruction. Split unlocking, crossing, and revealing "
                      "into separate shots if these actions occur. Frame the instant before movement, specify only "
                      "the physical result of the current beat, and keep the pertinent hand and prop visible. "
                      "Keep any off-screen sound off camera. Narrator scenes have no speaking mouths. "
                      "Dialogue ownership is locked from the script already. Do not repeat a speaker field. For dialogue, "
                      "stage the identified speaker in frame, with no second action competing for time. "
                      "Use exact asset tags, a visible cue only for a real time jump, and leave end_background_tag empty "
                      "unless someone visibly crosses into another location in this shot. The editor copies the locked Hindi lines.",
                      5200, lambda value: scene_errors(value, offset + 1, lines, cast, backgrounds))
        fix_narrator_actions(batch, lines, stage, offset + 1)
        shots.extend(as_shot(scene, line, cast, backgrounds) for scene, line in zip(batch.scenes, lines))

    result = {
        "version": 4, "planner": "clone", "title": title, "seconds": seconds,
        "script": "\n".join(line.text for line in script.lines), "synopsis": script.summary,
        "cast": [{"tag": key(item.tag), "prompt": item.full_prompt} for item in cast.values()],
        "locations": [{"tag": key(item.tag), "prompt": item.full_prompt} for item in backgrounds.values()],
        "shots": shots,
    }
    store.save(final, result)
    report(f"Script and prompts ready · {total}/{total} scenes", 24)
    return result
