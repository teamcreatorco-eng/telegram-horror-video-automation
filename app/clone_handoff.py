"""Verify and retain a Clone planning export, without contacting any provider."""

import argparse
import hashlib
import json
import re
from pathlib import Path


FORMAT = "railway.one-minute-planning.v1"
NARRATOR = "animation_prompt_NARRATOR_ONLY"
DIALOGUE = "animation_prompt_WITH_lipsync"
ASPECT_SUFFIX = ", --ar 16:9."


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError(f"Invalid JSON number: {value}")


def _parse(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                      parse_constant=_invalid_number)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _assets(entries, pattern):
    result = {}
    for entry in entries:
        tag = entry["tag"]
        if not re.fullmatch(pattern, tag) or tag in result:
            raise ValueError(f"Invalid or duplicate asset tag: {tag}")
        result[tag] = entry
    return result


def project(plan, source_hash, *, seconds=60):
    """Build separately inspectable FLUX/LTX requests from the locked source."""
    scenes = plan["scenes"]
    if (not 30 <= seconds <= 600 or seconds % 5 or plan["schema_version"] != "1.0.0"
            or plan["script"]["duration_minutes"] != seconds / 60
            or not isinstance(scenes, list) or not scenes):
        raise ValueError(f"Expected a {seconds}-second pipeline v1.0.0 plan with a non-empty scene list")
    characters = _assets(plan["characters"], r"@CHAR-\d+[-A-Z0-9]+")
    backgrounds = _assets(plan["backgrounds"], r"#BGD-\d+[-A-Z0-9]+")
    result = {"source_plan_sha256": source_hash, "production_ready": False,
              "target_seconds": seconds, "timing_status": "requires narration and clip timing",
              "characters": list(characters.values()), "backgrounds": list(backgrounds.values()),
              "shots": [], "adjustments": []}
    spoken = []
    for number, scene in enumerate(scenes, 1):
        path = f"/plan/scenes/{number - 1}"
        if type(scene["scene_number"]) is not int or scene["scene_number"] != number:
            raise ValueError(f"Unexpected scene order at {path}")
        bg, cast, speaker = (scene[key] for key in ("background_tag", "character_tags", "speaker"))
        if (scene["assets"] != {"background_tag": bg, "character_tags": cast}
                or bg not in backgrounds or any(tag not in characters for tag in cast)):
            raise ValueError(f"Invalid asset references at {path}")
        still, opening = scene["still_image_prompt"], scene["first_frame_description"]
        if not opening or opening not in still:
            raise ValueError(f"Opening pose is missing from still prompt at {path}")
        line, dialogue = scene["narration_text"], scene["devanagari_dialogue"]
        audio = {"speaker": speaker, "sound_design": scene["sound_design"]}
        motion_field = NARRATOR
        if speaker == "NARRATOR" and line and dialogue == "":
            audio.update(text=line, route="narration")
        elif speaker == "none" and line == dialogue == "":
            audio.update(text="", route="silent")
        elif speaker in cast and dialogue and line == "":
            motion_field = DIALOGUE
            audio.update(text=dialogue, route="character", lip_sync_status="unresolved",
                         voice_description=characters[speaker]["voice_description"])
        else:
            raise ValueError(f"Speaker and text disagree at {path}")
        if audio["text"]:
            spoken.append(audio["text"])
        source_motion = scene[motion_field]
        if motion_field == DIALOGUE and f'DIALOGUE (DEVANAGARI ONLY): "{dialogue}"' not in source_motion:
            raise ValueError(f"Character dialogue changed in the motion prompt at {path}")
        if motion_field == NARRATOR and "No voice synthesis" not in source_motion:
            raise ValueError(f"Narrator-only motion prompt lacks the audio lock at {path}")

        def trace(field, rule):
            result["adjustments"].append({"scene_number": number, "source_field": f"{path}/{field}",
                                          "rule": rule, "source_plan_sha256": source_hash})

        if still.endswith(ASPECT_SUFFIX):
            still = still[:-len(ASPECT_SUFFIX)] + "."
            trace("still_image_prompt", "move_terminal_flow_aspect_ratio_to_settings")
        motion = source_motion.splitlines(keepends=True)
        for directive, rule in (("CLIP LENGTH:", "defer_timing_to_renderer_settings"),
                                ("ACTIVE ENVIRONMENT:", "resolve_environment_from_opening_image"),
                                ("ACTIVE CHARACTERS:", "resolve_cast_from_opening_image")):
            retained = [row for row in motion if not row.startswith(directive)]
            if retained != motion:
                trace(motion_field, rule)
            motion = retained
        image_file = f"still-{number:02d}.png"
        opening_cast = scene.get("opening_character_tags")
        if opening_cast is not None and (not isinstance(opening_cast, list)
                                        or len(set(opening_cast)) != len(opening_cast)
                                        or not set(opening_cast) <= set(cast)):
            raise ValueError(f"Invalid opening cast at {path}")
        result["shots"].append({
            "scene_number": number, "assets": scene["assets"],
            "first_frame_description": opening, "visual_action": scene["visual_action"],
            "camera": scene["camera"], "effects": scene["effects"], "audio": audio,
            "flux": {"prompt": still, "aspect_ratio": "16:9", "output": image_file,
                     **({"character_tags": opening_cast} if opening_cast is not None else {})},
            "ltx": {"prompt": "".join(motion), "starting_image_file": image_file,
                    "source_field": f"{path}/{motion_field}"},
        })
    if "\n".join(spoken) != plan["script"]["raw_text"]:
        raise ValueError("Ordered speech does not match the locked script")
    return result


def import_for_review(reference, export, destination):
    raw, exported = reference.read_bytes(), export.read_bytes()
    plan, envelope = _parse(raw), _parse(exported)
    digest = _hash(_canonical(plan))
    expected = {"format": FORMAT, "source": {"filename": "pipeline.json", "sha256": _hash(raw),
                                           "plan_sha256": digest}, "plan": plan}
    if _canonical(expected) != _canonical(envelope):
        raise ValueError("Export differs from the original pipeline or its expected hashes")
    requests = project(plan, digest)

    destination.mkdir(parents=True, exist_ok=False)
    retained = destination / "railway-retained.json"
    retained.write_bytes(exported)
    (destination / "pipeline.json").write_bytes(raw)
    (destination / "renderer-preview.json").write_text(
        json.dumps(requests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if _canonical(_parse(retained.read_bytes())["plan"]) != _canonical(plan):
        raise ValueError("Persisted plan differs from the original")
    return len(requests["shots"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "export", "destination"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    try:
        count = import_for_review(args.reference, args.export, args.destination)
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.exit(1, f"Import rejected: {error}\n")
    print(f"Verified {count} locked shots after reading back the local import.")


if __name__ == "__main__":
    main()
