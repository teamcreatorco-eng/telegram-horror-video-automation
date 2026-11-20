"""Pre-rental scene data and prompt compilation; no visual reviewer or model calls."""
from copy import deepcopy

VERSION = "scene-record-v1"
KINDS = {"object_interaction", "locomotion", "reaction", "environmental_effect", "establishing"}
SCHEMA = '''Every shot includes scene_record with exactly this shape:
{"location":"#BGD-01-NAME","view":"interior/exterior and which side of the threshold",
 "camera":"one framing and at most one camera movement",
 "visible":[{"tag":"@CHAR-01-NAME","pose":"static opening pose and screen position","end_pose":"finishing pose and screen position","face_visible":true}],
 "offscreen":[],
 "objects":[{"id":"door","description":"wooden bedroom door","before":"closed","after":"open inward","fixed":false}],
 "static_surfaces":[],
 "action":{"kind":"object_interaction","actor":"@CHAR-01-NAME","target":"door",
 "movement":"pushes the door inward with one hand","reveal_face":false}}
Use stable object IDs across shots. visible contains only people actually in the frame, including visible body parts;
offscreen contains mentioned but unseen cast. Never put offscreen people in pose/object descriptions.
action.kind is object_interaction, locomotion, reaction, environmental_effect, or establishing.
actor is a visible cast tag, or null for environmental_effect/establishing. target is an object ID or null.
Put action targets and stateful props in objects. Fixed objects use fixed=true.
static_surfaces lists stationary scenery using object IDs or short descriptions; background scenery need not be duplicated in objects.
For footprints, the stairs are fixed: marks appear on them; the stairs do not move. Use an empty cast for empty inserts.
before/pose describe ONLY the static opening, not a sequence or completed action. after is the visible consequence.
One simple physical action per shot, achievable in five seconds. Keep the same cast throughout each clip.
If a face is hidden, never reveal it in motion: use a separate reaction shot with a visible face.
Keep view, object states and character positions consistent across adjacent shots; motivate location changes in the story.
The record is authoritative: Python compiles the final opening, action, camera and ending from it.
Do not change the locked narration to accommodate a different event.
'''


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")


def validate_record(record, cast, locations):
    if not isinstance(record, dict):
        raise ValueError("scene_record must be an object")
    required = {"location", "view", "camera", "visible", "offscreen", "objects", "static_surfaces", "action"}
    if set(record) != required:
        raise ValueError("scene_record requires fields: " + ", ".join(sorted(required)))
    if not isinstance(record["location"], str) or record["location"] not in locations:
        raise ValueError("Unknown location")
    for field in ("view", "camera"):
        _text(record[field], field)
    for field in ("visible", "offscreen", "objects", "static_surfaces"):
        if not isinstance(record[field], list):
            raise ValueError(f"{field} must be a list")
    tags = []
    for person in record["visible"]:
        if not isinstance(person, dict) or set(person) != {"tag", "pose", "end_pose", "face_visible"}:
            raise ValueError("Visible people require tag, pose, end_pose and face_visible")
        tag = person["tag"]
        if not isinstance(tag, str) or tag not in cast:
            raise ValueError("Unknown visible character")
        if tag in tags:
            raise ValueError("duplicate visible character")
        tags.append(tag)
        _text(person["pose"], "pose")
        _text(person["end_pose"], "end_pose")
        if type(person["face_visible"]) is not bool:
            raise ValueError("face_visible must be boolean")
    offscreen = record["offscreen"]
    if any(not isinstance(t, str) or t not in cast for t in offscreen):
        raise ValueError("Unknown offscreen character")
    if len(set(offscreen)) != len(offscreen) or set(tags) & set(offscreen):
        raise ValueError("Visible and offscreen cast must be distinct")
    objects = {}
    for obj in record["objects"]:
        if not isinstance(obj, dict) or set(obj) != {"id", "description", "before", "after", "fixed"}:
            raise ValueError("Objects require id, description, before, after and fixed")
        for field in ("id", "description", "before", "after"):
            _text(obj[field], field)
        if obj["id"] in objects or obj["id"] in cast:
            raise ValueError("Object IDs must be unique and separate from cast")
        if type(obj["fixed"]) is not bool:
            raise ValueError("fixed must be boolean")
        objects[obj["id"]] = obj
    surfaces = record["static_surfaces"]
    for surface in surfaces:
        _text(surface, "static surface")
        if surface in cast:
            raise ValueError("A character cannot be a static surface")
        if surface in objects and not objects[surface]["fixed"]:
            raise ValueError("A static surface cannot reference a movable object")
    action = record["action"]
    if not isinstance(action, dict) or set(action) != {"kind", "actor", "target", "movement", "reveal_face"}:
        raise ValueError("action requires kind, actor, target, movement and reveal_face")
    if not isinstance(action["kind"], str) or action["kind"] not in KINDS:
        raise ValueError("Unknown action kind")
    _text(action["movement"], "movement")
    if type(action["reveal_face"]) is not bool:
        raise ValueError("reveal_face must be boolean")
    if action["actor"] is not None and action["actor"] not in tags:
        raise ValueError("Action actor must be visible")
    if action["kind"] in {"object_interaction", "locomotion", "reaction"} and action["actor"] is None:
        raise ValueError("This action requires a visible actor")
    if action["target"] is not None and (not isinstance(action["target"], str) or action["target"] not in objects):
        raise ValueError("Unknown action target")
    if action["kind"] in {"object_interaction", "environmental_effect"} and action["target"] is None:
        raise ValueError("This action requires a target")
    if action["reveal_face"] and any(not p["face_visible"] for p in record["visible"]):
        raise ValueError("Hidden face needs a separate visible-face reaction shot")


def compile_record(record):
    tags = [p["tag"] for p in record["visible"]]
    people = "; ".join(f"{p['tag']}: {p['pose']} (face {'visible' if p['face_visible'] else 'out of frame'})" for p in record["visible"])
    scope = f"Exactly {len(tags)} people: {people}." if tags else "Empty composition: zero people, faces or human limbs."
    opening = " ".join(f"{o['description']}: {o['before']}." for o in record["objects"])
    ending = " ".join(f"{o['description']}: {o['after']}." for o in record["objects"])
    ending_people = "; ".join(f"{p['tag']}: {p['end_pose']}" for p in record["visible"])
    action = record["action"]
    performer = action["actor"] or "Environmental action"
    target = f" Target: {action['target']}." if action["target"] else ""
    surfaces = list(dict.fromkeys(record["static_surfaces"] + [o["id"] for o in record["objects"] if o["fixed"]]))
    fixed = " ".join(f"{name} remain stationary." for name in surfaces)
    hidden = " Keep hidden faces outside the frame; no face reveal." if any(not p["face_visible"] for p in record["visible"]) else ""
    return dict(background_tag=record["location"], character_tags=tags, opening_character_tags=tags,
                first_frame_description=f"{record['view']}. {scope} {opening}".strip(),
                camera=record["camera"], visual_action=f"{performer}: {action['movement']}.{target} {fixed}{hidden}".strip(),
                ending_state=f"Same view and same {len(tags)} visible people. {ending_people}. {ending} {fixed}{hidden}".strip())


def repair_records(records, validate, repair):
    """Two local corrections per bad record; never mutate input or retry good records."""
    result, history = deepcopy(records), []
    for index, record in enumerate(result):
        for attempt in range(3):
            try:
                validate(record)
                break
            except (ValueError, TypeError, KeyError) as error:
                if attempt == 2:
                    raise ValueError(f"Scene {index + 1} could not be planned before rental: {error}") from None
                revised = repair(index, deepcopy(record), str(error), attempt == 1)
                history.append(dict(scene_number=index + 1, reason=str(error), attempt=attempt + 1,
                                    before=deepcopy(record), after=deepcopy(revised)))
                record = revised
        result[index] = record
    return result, history
