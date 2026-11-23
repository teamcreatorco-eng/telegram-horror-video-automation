"""Railway production path for the approved Raghu image and motion settings."""
from app.youtube_clone.pipeline.visual_composition import motion_contract
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image

from . import media, raghu_narration, store
from . import raghu_music, bundled_media
from .raghu_services import ASSETS, STYLE, GeminiUnavailable, choose_title, narrate, plan, required, review_continuity, review_motion, review_still, rewrite_still_prompt
from .raghu_vast import VastConnectionLost, image_model, motion_model, rent, uses_ending_image

FRAMES = 121
FPS = 24
WAN_FRAMES = 81
WAN_FPS = 16
OPENING = ASSETS / "opening.mp4"
STILL_REVIEW_VERSION = 4
STRICT_OPENING_REVIEW_VERSION = 5
MOTION_REVIEW_VERSION = 4
MOTION_EVIDENCE_VERSION = 4
REVIEW_RETRY_SECONDS = 15
REVIEW_RECOVERY_ATTEMPTS = 3
AI_VISUAL_REVIEW = False


def check_image_file(path):
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        image.load()
    return {"pass": True, "review_skipped": True, "technical_checks_passed": True,
            "approval_basis": "readable_image", "reason": "Image saved successfully. AI visual review is disabled."}


def invalidate_scene_media(folder, ident, archive, *, include_still=True):
    """Archive one scene's visual outputs; canonical identity and narration remain valid."""
    names=[f'shot-{ident}.mp4',f'motion-{ident}.review.json',f'motion-{ident}.retakes.json',
           f'rejected-shot-{ident}.mp4',f'rejected-motion-{ident}.review.json',f'end-{ident}.png']
    if include_still:
        names += [f'still-{ident}.png',f'still-{ident}.png.review.json',f'still-{ident}.png.retakes.json']
    names += ['final.mp4','production-kit.zip','raghu-timeline.json','quality-report.json','saved-still-selection.json']
    archive.mkdir(parents=True,exist_ok=True)
    for name in names:
        source=folder/name
        if source.is_file():source.replace(archive/name)
    store.save(folder/f'motion-{ident}.invalidate.json',dict(nonce=uuid.uuid4().hex))
    return names


def recover_saved_images(folder, tasks, check):
    """Quarantine technically unreadable saved images and their dependent visuals."""
    broken=set()
    for item in tasks:
        check();path=folder/item['file']
        if path.is_file():
            try:check_image_file(path)
            except (OSError,ValueError,SyntaxError):broken.add(item['file'])
    if not broken:return
    changed=True
    while changed:
        previous=len(broken)
        broken.update(item['file'] for item in tasks if any(
            ref.removeprefix('results/') in broken for ref in item.get('refs',[])))
        changed=len(broken)!=previous
    archive=folder/'history'/('unreadable-'+uuid.uuid4().hex);archive.mkdir(parents=True)
    forgotten=[]
    for name in broken:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):raise ValueError('Invalid image artifact name')
        for suffix in ('','.review.json','.retakes.json'):
            path=folder/(name+suffix)
            if path.is_file():path.replace(archive/path.name)
            forgotten.append(path.name)
        match=re.fullmatch(r'still-(\d{3})\.png',name)
        if match:forgotten.extend(invalidate_scene_media(folder,match[1],archive,include_still=False))
    from . import storage
    storage.sync(folder.name)
    storage.forget(folder.name,forgotten)


def opening_path():
    configured = os.getenv("RAGHU_OPENING_PATH")
    if configured:
        return Path(configured)
    installed = store.DATA / "branding" / "opening.mp4"
    return installed if installed.is_file() else (bundled_media.available('opening.mp4') or OPENING)


class RenderReviewNeedsRetake(RuntimeError):
    """A failed image or motion review can be retried on the same rental."""


FLAT_STYLE = ("Every frame must be hand-drawn-looking 2D ink and matte flat cel shading. "
              "Preserve the same facial design, colors and scale throughout. No photoreal humans, 3D, CGI, "
              "glossy skin, rendered hair, style transitions, or cutaways.")


def validate_music_for_request(request):
    if os.getenv('RAGHU_BACKGROUND_AUDIO', 'true').lower() in {'1', 'true', 'yes'}:
        raghu_music.validate_source(request.get('music_track'))


def production_credentials():
    image_model()
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "VAST_API_KEY",
                 "RAGHU_MAX_HOURLY_USD", "RAGHU_MAX_JOB_USD"):
        required(name)
    if os.getenv("RAGHU_NARRATION_PROVIDER", "gemini").lower() == "gemini":
        required("GEMINI_API_KEY")
    if key_path := os.getenv("VAST_SSH_KEY_PATH"):
        if not Path(key_path).is_file():
            raise RuntimeError("VAST_SSH_KEY_PATH does not point to a readable file.")
    else:
        required("VAST_SSH_PRIVATE_KEY")
    tools = [name for name in ("vastai", "ssh", "scp", "ssh-keygen") if not shutil.which(name)]
    if tools:
        raise RuntimeError("Required local tools are missing: " + ", ".join(tools))
    if motion_model() == "ltx":
        required("HF_TOKEN")
    if raghu_narration.uses_clone():
        raghu_narration.clone_reference()

def task(name, prompt, refs, seed):
    return {"file": name, "prompt": f"{prompt} {STYLE}", "refs": refs, "seed": seed}

def cast_file(tag):
    return f"character-{tag}.png"

def location_file(tag):
    return f"background-{tag}.png"

def still_file(shot):
    return f"still-{shot['id']}.png"


def retry_round(folder):
    marker = folder / "raghu-user-retry.json"
    return store.read(marker)["round"] if marker.is_file() else ""


def retry_state(path, round_id):
    saved = store.read(path) if path.is_file() else {}
    return saved if saved.get("round", "") == round_id else {}

def still_batches(plan):
    canon = []
    for index, asset in enumerate(plan["cast"]):
        identity = asset.get("identity_description") or asset["prompt"]
        identity = identity.split(" Neutral full-body three-quarter pose and front-facing expression inset", 1)[0]
        if "Adult Indian " in identity:
            identity = "Adult Indian " + identity.split("Adult Indian ", 1)[1]
        prompt = (
            "One person only, one full-body three-quarter view, head to feet centered against a solid grey "
            "studio background. Clean 2D Indian cel animation, bold ink outlines and matte flat shading. "
            f"{identity} Use one view with an unobstructed face and readable outfit. No inset, layout panels, "
            "scenery, lettering or watermark."
        )
        canon.append({"file": cast_file(asset["tag"]), "prompt": prompt, "refs": [],
                      "seed": 55100 + index, "visual_retake": True, "retake_prompt": prompt})
    canon += [task(location_file(a["tag"]), a["prompt"] + " Empty room or exterior, no people.",
                   ["style-interior.jpg"], 55200+i) for i, a in enumerate(plan["locations"])]
    wardrobe = {asset["tag"]: asset["prompt"] for asset in plan["cast"]}
    starts = []
    for index, shot in enumerate(plan["shots"]):
        opening = shot.get("start")
        visible_at_opening = list(dict.fromkeys(tag for tag in shot["cast"] if not opening or tag in opening["cast"]))
        location = shot["location"]
        prompt = (shot["still"] + " Single pre-action pose. Style image is style only. Canonical character appearance, "
                  "including outfit and lower garments, overrides conflicting scene details. "
                  + " ".join(f"{tag}: {wardrobe[tag]}" for tag in visible_at_opening))
        if opening:
            prompt += (f" Locked opening state: {describe(opening)}. "
                       "Stage the action before it starts. Preserve threshold side, props, and camera geography. "
                       "The location reference defines architecture; its framing and prop state yield to this shot. "
                       "The character references define identity only. Show only the scripted opening composition; "
                       "off-camera people stay off camera. " + FLAT_STYLE)
        refs = [f"results/{location_file(location)}",
                *[f"results/{cast_file(tag)}" for tag in visible_at_opening], "style-wide.jpg"]
        item = task(still_file(shot), prompt, refs, 57300 + index)
        if "clone_motion" in shot:
            frame = opening["staging"]
            camera = f" Camera: {shot['camera'].rstrip('.')}." if shot.get("camera") else ""
            props = " ".join(f"{name}: {state}." for name, state in opening["props"].items())
            composition = (f"Opening frame: {frame.rstrip('.')}.{camera} {props} "
                           "Show this instant before the action. Keep the necessary hands, props and action space visible.")
            roles = " ".join(f"Reference image {i + 2}: identity and clothing for {tag} only."
                             for i, tag in enumerate(visible_at_opening))
            item.update(
                prompt=f"{composition} Reference image 1: architecture and lighting only. {roles} "
                       f"The last reference supplies linework and shading only. Show only the scripted cast. {STYLE}",
                review_prompt=f"{composition} Match the identity and clothes of the supplied character references. {STYLE}",
                strict_opening=True,
                max_retakes=2,
            )
            if not shot.get("simple_prompt"):
                composition = f"Opening composition: {frame}.{camera} Show this exact instant before the action."
                item.update(
                    prompt=f"{composition} {shot['still']} Reference image 1: location layout only. {roles} "
                           "The final reference is for linework and shading only. Use the requested camera and opening pose.",
                    review_prompt=f"{composition} {shot['still']}",
                )
            item["simple_prompt"] = bool(shot.get("simple_prompt"))
        item["is_opening"] = True
        item["detail_insert"] = bool(
            opening and opening.get("location") == shot.get("end", {}).get("location")
            and re.search(r"close.up|insert|overhead|desk detail", shot["still"], re.I)
        )
        if opening:
            props = " ".join(f"{name}: {state}." for name, state in opening["props"].items())
            reference_roles = " ".join(f"Reference {i + 2} is {tag}; copy that person's face, hair and outfit."
                                       for i, tag in enumerate(visible_at_opening))
            # Give retakes an affirmative composition brief without contradicting the original review prompt.
            item["retake_prompt"] = (
                f"{shot['still']} Show exactly the opening instant, before any movement. "
                f"Opening position: {opening['staging']}. Required prop placement: {props} "
                f"Reference 1 establishes {location}'s layout; adapt the angle to this shot. {reference_roles} "
                "The last reference supplies style only. Keep each prop physically attached to its stated holder "
                "or resting on its stated surface. Match the required framing and keep story-critical details readable. "
                "When an exact count is featured, arrange the items separately with visible space between them, "
                "keeping every item in frame and placing distinguishing marks on the specified item. "
                f"{FLAT_STYLE} {STYLE}")
            if (visible_at_opening and "overhead" in shot["still"].lower()
                    and "crack" in shot["still"].lower() and re.search(r"seven (?:glass )?bangles", props, re.I)):
                item["retake_prompt"] = (
                    "Top-down insert looking straight into the open chest. Arrange the seven glass bangles in "
                    "two clearly separated rows: four above and three below. Keep every bangle fully inside "
                    "the frame. The bottom-right bangle alone has a hairline crack through the glass. Keep "
                    f"{', '.join(visible_at_opening)} at the edge of the frame, with the contents as the focal point. "
                    + item["retake_prompt"])
        if item.get("strict_opening"):
            item["retake_prompt"] = item["prompt"]
        if "scene_record" in shot:
            # Persist the exact structured input beside the prompt and reference list.
            item["scene_record"] = shot["scene_record"]
            item["scene_record_version"] = shot["scene_record_version"]
            item["planning_input_sha256"] = hashlib.sha256(
                json.dumps(shot["scene_record"], sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        starts.append(item)
    return canon, starts


def describe(state):
    return json.dumps(state, ensure_ascii=False, sort_keys=True)


def review_signature(prompt, paths):
    fingerprint = hashlib.sha256(prompt.encode())
    for path in paths:
        fingerprint.update(hashlib.sha256(path.read_bytes()).digest())
    return fingerprint.hexdigest()


def still_pair_checks(folder, shots, check):
    """Review the two compositions and the planned action between them."""
    if not AI_VISUAL_REVIEW:
        return {}
    def assess(pair):
        previous, current = pair
        check()
        paths = [folder / still_file(shot) for shot in pair]
        prompt = (
            "Both images are stills before each shot's action. Image 1 is BEFORE the earlier action, "
            "and image 2 is AFTER the earlier action but BEFORE the next action. No motion footage is supplied. "
            "Assume the stated earlier action and ending happen between these two images. Do not reject the "
            "absence of intermediate movement or an expected change caused by that action. For example, "
            "holding bangles in image 1 and wearing them in image 2 passes when the action puts them on. "
            "Different rooms pass when the earlier action crosses into the next room through the mapped passage. "
            f"Earlier opening: {describe(previous['start'])}. Earlier action: {previous['motion']}. "
            f"Earlier ending: {describe(previous['end'])}. Next opening: {describe(current['start'])}. "
            f"Earlier visible cast: {previous['start']['cast']}; next visible cast: {current['start']['cast']}. "
            f"Transition: {current['transition']}. Visible cue: {current['time_cut_cue']}. "
            "Decide if image 2 can follow the earlier action. Reject a material change of threshold side, room "
            "or floor, actor identity, outfit, essential prop or ownership, or a reversal without a visible cause. "
            "Allow small differences in secondary item counts and incidental background props that do not change the action. "
            "A different angle is fine. An explicitly motivated time cut is fine if it reads as such. "
            "A cut from one person to another is expected when they are the scripted subjects of separate "
            "framings in the same ongoing scene. Compare their identities to the cast specified for each shot. "
            "Reject only if you can state how a visible detail in image 2 materially contradicts its own "
            "expected opening, or how a recurring character, setting or essential prop visibly changes beyond "
            "what the intervening action explains. A shot may crop or omit a person or prop outside the frame. "
            "If unclear whether a small detail differs, pass it. For a reflection, distinguish the physical and reflected figures."
        )
        signature = review_signature(prompt, paths)
        report = folder / f"continuity-{previous['id']}-{current['id']}.review.json"
        if report.is_file() and (saved := store.read(report)).get("signature") == signature:
            return current["id"], saved
        try:
            verdict = {**review_continuity(paths, prompt), "signature": signature}
        except GeminiUnavailable as exc:
            raise GeminiUnavailable(f"Continuity review pending for shots {previous['id']} → {current['id']}. {exc}") from exc
        store.save(report, verdict)
        return current["id"], verdict

    pairs = zip(shots, shots[1:])
    with ThreadPoolExecutor(max_workers=4) as pool:
        return {ident: result["reason"] for ident, result in pool.map(assess, pairs) if not result["pass"]}


def manifest(remote, folder, name, value):
    path = folder / name
    store.save(path, value)
    remote.push([path])

def assess_still(folder, item, check):
    name = item["file"]
    report = folder / (name + ".review.json")
    if not AI_VISUAL_REVIEW:
        check()
        try:result = check_image_file(folder / name)
        except (OSError,ValueError,SyntaxError):
            result={"pass":False,"technical_failure":True,"reason":"Image file is unreadable; regenerate this asset."}
        store.save(report, result)
        return result
    saved = {}
    strict = item.get("strict_opening", False)
    version = STRICT_OPENING_REVIEW_VERSION if strict else STILL_REVIEW_VERSION
    source_signature = end_frame_source_signature(folder, item) if name.startswith("end-") else None
    if report.exists():
        saved = store.read(report)
        current = saved.get("review_version") == version or (
            not strict and saved.get("review_version") == 3 and saved.get("pass"))
        if current and (source_signature is None or saved.get("source_signature") == source_signature):
            return saved
    check()
    refs, roles = [], []
    for index, filename in enumerate(item["refs"]):
        if filename.startswith("results/"):
            ref = folder / filename.removeprefix("results/")
            role = ("canonical character, compare identity and clothing" if ref.name.startswith("character-")
                    else "approved opening frame; preserve every detail except the scripted change"
                    if ref.name.startswith(("still-", "smoke-image")) else "canonical location, compare setting and layout")
        else:
            ref = ASSETS / filename
            role = "style only, compare linework, shading and rendering. Ignore its subject, identity, pose, and setting"
        if "review_roles" in item:
            role = item["review_roles"][index]
        refs.append(ref)
        roles.append(f"Image {len(refs) + 1} is {role}.")
    kind = ("This candidate is a canonical character asset. Judge the character against its text description."
            if name.startswith("character-") else
            "This candidate is an empty location plate. Judge the location against its text description."
            if name.startswith("background-") else
            "This is the ending image edited from the approved opening. Check the intended landing pose, position and prop state. "
            "Reject changes unrelated to the action. " if name.startswith("end-") else
            "This candidate is a single scene starting frame. Judge the scene against its text and canonical assets.")
    expected = item.get("review_prompt", item.get("base_prompt", item["prompt"]))
    result = review_still([folder / name, *refs],
        f"{kind} {' '.join(roles)} Expected: {expected} "
        "Separate small visible deviations from blocking issues such as malformed anatomy, wrong essential props, "
        "unusable pose, material identity or layout changes, incorrect style, panels, text or watermarks. "
        "Use the canonical image to identify visible hair and clothing. Establish which person each reference shows "
        "before comparing them. If hair or a background prop is cropped, obscured or too small, do not guess its "
        "appearance or count. An essential detail explicitly featured in the composition must be visible.",
        **({"strict_opening": True} if strict else {}))
    result["review_version"] = version
    if source_signature is not None:
        result["source_signature"] = source_signature
    result["candidate_seed"] = saved.get("candidate_seed", item["seed"])
    store.save(report, result)
    return result


def review_stills(remote, folder, tasks, check, report_failure=lambda message: None):
    pending_reviews = {}
    exhausted = {}
    round_id = retry_round(folder)
    tasks = [retry_state(folder / (t["file"] + ".retakes.json"), round_id).get("task", t)
             if not (folder / t["file"]).is_file() else t for t in tasks]
    for attempt in range(3):
        missing = [t for t in tasks if not (folder / t["file"]).exists()]
        with ThreadPoolExecutor(max_workers=4) as pool:
            waiting = {t["file"]: t for t in missing}
            reviews = {t["file"]: pool.submit(assess_still, folder, t, check)
                       for t in tasks if t["file"] not in waiting}
            if missing:
                manifest(remote, folder, "still-tasks.json", missing)
                for name in remote.stream_stills("still-tasks.json", list(waiting), folder):
                    reviews[name] = pool.submit(assess_still, folder, waiting[name], check)
            results = []
            for item in tasks:
                try:
                    result = reviews[item["file"]].result()
                except GeminiUnavailable as error:
                    pending_reviews[item["file"]] = error
                else:
                    pending_reviews.pop(item["file"], None)
                    results.append((item, result))
        failed = []
        for item, result in results:
            if result["pass"]:
                continue
            name = item["file"]
            if result.get("minor_retake") and (folder / ("rejected-" + name + ".review.json")).is_file():
                result = {**result, "pass": True, "accepted_after_retake": True}
                store.save(folder / (name + ".review.json"), result)
                continue
            if item.get("strict_opening") or item.get("visual_retake"):
                checkpoint = folder / (name + ".retakes.json")
                retakes = retry_state(checkpoint, round_id).get("count", 0)
                if retakes >= item.get("max_retakes", 2):
                    exhausted[name] = result["reason"]
                    continue
            failed.append((item, result))
        if not failed:
            if exhausted:
                return exhausted
            if pending_reviews:
                name, error = next(iter(pending_reviews.items()))
                raise GeminiUnavailable(f"Review is still pending for {name}. {error}") from error
            return {}
        name = failed[0][0]["file"]
        reason = failed[0][1]["reason"][:240]
        message = f"Image review flagged {name}. {reason}"
        report_failure(message)
        if attempt == 2:
            return {**exhausted, **{item["file"]: result["reason"] for item, result in failed}}
        tasks = []
        for item, result in failed:
            name = item["file"]
            fallback_seed = item["seed"] + (1000 if (folder / ("rejected-" + name)).exists() else 0)
            seed = int(result.get("candidate_seed", fallback_seed)) + 1000
            rewritten = ""
            if AI_VISUAL_REVIEW and (item.get("visual_retake") or item.get("strict_opening")):
                check()
                try:
                    rewritten = rewrite_still_prompt(folder / name, item.get("retake_prompt", item["prompt"]),
                                                     result["reason"])
                except GeminiUnavailable:
                    # The locked prompt is still usable if the rewrite request fails.
                    pass
            (folder / name).replace(folder / ("rejected-" + name))
            (folder / (name + ".review.json")).replace(folder / ("rejected-" + name + ".review.json"))
            prompt = item.get("retake_prompt")
            base = item.get("base_prompt", item["prompt"])
            if rewritten and item.get("simple_prompt"):
                prompt = f"{rewritten} {STYLE}"
            elif rewritten:
                prompt = f"{rewritten} Locked requirements: {prompt or base}"
            elif item.get("visual_retake"):
                prompt = prompt or base
            else:
                prompt = "Correct this visible issue: " + result["reason"][:350] + ". " + (prompt or base)
            retake = {**item, "seed": seed, "base_prompt": base}
            if item.get("is_opening"):
                prompt += (" Resolve the defect in the pre-action still itself. If a contact detail is too small, "
                           "frame the hands and the working object clearly. If the action crosses a boundary, "
                           "make the origin side, threshold and open walking path readable. A prop or person "
                           "that enters during motion must be in its specified frame-zero state here.")
                if item.get("detail_insert") and re.search(
                    r"framing|crop|close.up|too wide|too small|obscur|hard to see", result["reason"], re.I
                ):
                    # A wide reference can compete with an insert. Keep it for review.
                    retake["generation_refs"] = item["refs"][1:]
                    prompt = ("Close insert. The attached character references are for identity; "
                              "the last image is for 2D style. " + (rewritten or base))
            retake["prompt"] = prompt
            if item.get("strict_opening") or item.get("visual_retake"):
                checkpoint = folder / (name + ".retakes.json")
                count = retry_state(checkpoint, round_id).get("count", 0)
                store.save(checkpoint, {"count": count + 1, "round": round_id, "task": retake})
            tasks.append(retake)

def end_file(shot):
    return f"end-{shot['id']}.png"


def end_frame_tasks(shots, folder=None, successors=None):
    tasks = []
    for shot in shots:
        refs = [f"results/{still_file(shot)}"]
        opening = shot.get("start", {}).get("location")
        ending = shot.get("end", {}).get("location")
        guidance = ("Use the same faces, clothes, prop designs, geometry, lighting and flat 2D linework. "
                    "Change only what the scripted action changes. "
                    "Match the opening crop and angle unless the action explicitly moves the camera.")
        action = f"Action: {shot['motion']}. "
        review_roles = None
        final_state = describe(shot.get("end", {}))
        if folder is not None and opening and ending and opening != ending:
            next_shot = (successors or {}).get(shot["id"])
            target = location_file(ending)
            if not (folder / target).is_file() and next_shot and next_shot.get("location") == ending:
                target = still_file(next_shot)
            if (folder / target).is_file():
                refs.insert(0, f"results/{target}")
                guidance = ("Use the destination room in reference 1 as the base image. "
                            "Place the camera and subjects inside that space after the crossing. "
                            "Take only the character identity, costume, prop designs and 2D linework from "
                            "reference 2. Stage the character poses and required props from the final state.")
                action = ""
                review_roles = [
                    "destination layout only, compare the room and threshold side",
                    "opening frame, compare identity, clothing, prop design and style only",
                ]
        if review_roles:
            item = task(end_file(shot), f"Create the ending frame of one continuous shot. {guidance} "
                        f"Final composition and prop state: {final_state}. One finished frame. " + FLAT_STYLE,
                        refs, 58300 + int(shot["id"]))
            item["review_roles"] = review_roles
            item["review_prompt"] = (
                f"Judge the image against this final state only: {final_state}. "
                "The opening location and staging describe a previous moment. "
                "Props described as hidden or located in another room are continuity state only. "
                "They should remain off screen and their absence is not a defect. "
                "Treat hand preference as minor when it does not affect the action or a featured reveal."
            )
        else:
            item = task(end_file(shot),
                        "Edit the attached approved first frame into the final frame of the same shot. "
                        f"{guidance} For a doorway crossing, show the resulting side of the same doorway. "
                        "For motion returning to rest, return to the original pose and layout. "
                        f"{action}Final composition and prop state: {final_state}. "
                        "One finished frame, no labels or panels. " + FLAT_STYLE,
                        refs, 58300 + int(shot["id"]))
        tasks.append(item)
    return tasks


def end_frame_source_signature(folder, item):
    return review_signature(item.get("base_prompt", item["prompt"]),
                            [folder / ref.removeprefix("results/") for ref in item["refs"]])


def opening_motion_prompt(shot):
    physical_brief = motion_contract(shot)
    if motion_model() == "hunyuan15" and shot.get("narration"):
        physical_brief += f"Story context only (not spoken audio): {shot['narration']}. "
    if not shot.get("simple_prompt"):
        mouth = "Keep all mouths closed."
        if shot.get("speaker_description"):
            mouth = (f"Only this speaker moves their mouth during the brief line: {shot['speaker_description']}. "
                     "Keep other mouths closed. Hindi dialogue is added in editing.")
        return (physical_brief +
                "Preserve the opening image's clean hand-drawn 2D finish, character identity, clothing and "
                "fixed architecture. Show clear body weight and contact. One continuous shot, "
                f"no internal cuts, fades, duplicate objects or extra people. {mouth}")
    mouth = "Keep all mouths closed."
    if shot.get("speaker_description"):
        mouth = (f"Only the scripted speaker ({shot.get('speaker', '')}) moves their lips briefly, then closes their mouth. "
                 "Other mouths stay closed. Dialogue is added in editing.")
    camera = "Keep the supplied opening framing and subject scale fixed unless the shot explicitly calls for camera movement."
    return (f"One continuous shot from the supplied opening image. {camera} "
            f"{physical_brief} {mouth} "
            "Keep the visible character's face, clothes and the room unchanged. "
            "Preserve flat hand-drawn 2D linework. No internal cuts, fades or extra cast.")


def motion_plan(shots):
    if motion_model() == "wan22":
        return {"shots": [{
            "id": shot["id"],
            "starting_image_file": still_file(shot),
            "video_prompt": (
                "Animate the supplied opening as one continuous five-second shot. "
                f"Opening framing and camera: {shot.get('still', 'Use the supplied camera angle')}. "
                f"Opening blocking: {describe(shot.get('start', {}))}. "
                f"Perform this motion in order: {shot['motion']}. "
                f"Finish naturally in this visible story state: {describe(shot.get('end', {}))}. "
                "Start movement promptly, show grounded footfalls or clear hand contact throughout the action, "
                "and let the reaction register through gaze, facial expression and posture. Follow the specified "
                "camera move and keep screen direction, character design, prop shape and room geometry consistent. "
                "Show travel through the threshold when the script requires it. No fades, dissolves, cuts, "
                "teleports, static frame holds or dialogue. " + FLAT_STYLE
            ),
            "frames": WAN_FRAMES, "fps": WAN_FPS, "seed": 55400 + int(shot["id"]),
        } for shot in shots]}
    if not uses_ending_image():
        return {"shots": [{
            "id": shot["id"], "starting_image_file": still_file(shot),
            "video_prompt": opening_motion_prompt(shot),
            "frames": FRAMES, "fps": FPS, "seed": 55400 + int(shot["id"]),
        } for shot in shots]}
    return {"shots": [{"id": s["id"], "starting_image_file": still_file(s),
                      "ending_image_file": end_file(s), "ending_frame": FRAMES - 1,
                      "video_prompt": "Use the attached first image as the opening and the attached last image as "
                      "the landing of one continuous shot. Show the action between them in order. "
                      + s["motion"] + " Match the reference face, outfit, ink and room. "
                      "Keep unaffected people, props and architecture stable. Use only the specified camera motion. "
                      "No cuts, speech, or unscripted objects or people. " + FLAT_STYLE
                      + (f" Start: {describe(s['start'])}. End: {describe(s['end'])}. "
                         "Reach the ending pose before the cut and settle into the ending image." if "start" in s else ""),
                      "frames": FRAMES, "fps": FPS, "seed": 55400 + int(s["id"])} for s in shots]}


def motion_signature(folder, shot, next_shot):
    model = motion_model()
    paths = [folder / f"shot-{shot['id']}.mp4", folder / still_file(shot)]
    if uses_ending_image():
        paths.append(folder / end_file(shot))
    if next_shot is not None:
        paths.append(folder / still_file(next_shot))
    if not all(path.is_file() for path in paths):
        return None
    contract = [MOTION_REVIEW_VERSION, shot, next_shot, model, motion_plan([shot])]
    return review_signature(describe(contract), paths)


def motion_retake_prompt(folder, ident):
    path = folder / "focused-motion-retake-draft.json"
    if not path.is_file():
        return None
    return store.read(path)["motion_prompt_overrides"].get(ident)


def validate_motion(folder, shot, next_shot=None):
    wan = motion_model() == "wan22"
    paired = uses_ending_image()
    ident = shot["id"]
    retake_prompt = motion_retake_prompt(folder, ident)
    clip = folder / f"shot-{ident}.mp4"
    report = folder / f"motion-{ident}.review.json"
    signature = motion_signature(folder, shot, next_shot)
    if not AI_VISUAL_REVIEW:
        if signature and report.is_file():
            saved = store.read(report)
            if saved.get("signature") == signature and saved.get("review_skipped") and saved.get("technical_checks_passed"):
                return saved
        try:
            stream = next(s for s in media.probe(clip)["streams"] if s["codec_type"] == "video")
            expected = (WAN_FRAMES, 848, 480) if wan else (FRAMES, 1280, 720 if motion_model() == "hunyuan15" else 768)
            if (int(stream.get("nb_frames", "0")), stream["width"], stream["height"]) != expected:
                raise RuntimeError("Unexpected frame count or dimensions.")
            subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-i", str(clip),
                            "-an", "-f", "null", "-"], capture_output=True, timeout=45, check=True)
            result = {"pass": True, "review_skipped": True, "technical_checks_passed": True,
                      "approval_basis": "decodable_video", "reason": "Clip saved successfully. AI visual review is disabled."}
        except (RuntimeError, StopIteration, ValueError, OSError, subprocess.SubprocessError) as error:
            result = {"pass": False, "review_skipped": True, "technical_checks_passed": False,
                      "technical_failure": True, "reason": "Video file validation failed. " + str(error)[:240]}
        result["signature"] = signature
        result["evidence_version"] = MOTION_EVIDENCE_VERSION
        store.save(report, result)
        return result
    if signature and report.exists() and (saved := store.read(report)).get("signature") == signature:
        if saved.get("pass") or saved.get("evidence_version") == MOTION_EVIDENCE_VERSION:
            return saved
    try:
        with tempfile.TemporaryDirectory(dir=folder) as tmp:
            sheet = Path(tmp) / "frames.jpg"
            result = media.probe(clip)
            stream = next(s for s in result["streams"] if s["codec_type"] == "video")
            expected = (WAN_FRAMES, 848, 480) if wan else (FRAMES, 1280, 720 if motion_model() == "hunyuan15" else 768)
            if (int(stream.get("nb_frames", "0")), stream["width"], stream["height"]) != expected:
                raise RuntimeError("Unexpected frame count or dimensions.")
            scan = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-xerror",
                "-threads", "2", "-i", str(clip), "-vf",
                "blackframe=amount=98:threshold=24,freezedetect=n=-60dB:d=1.2",
                "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=45, check=True)
            if len(re.findall(r"pblack:", scan.stderr)) >= 3 or "freeze_start:" in scan.stderr:
                raise RuntimeError("Detected near-black or frozen frames.")
            sample = "eq(n,floor(selected_n*80/15))" if wan else "not(mod(n,8))"
            media.ff("-i", clip, "-vf", f"select='{sample}',scale=240:-2,tile=4x4",
                     "-frames:v", "1", sheet)
            files = [sheet, folder / still_file(shot)]
            reference = ("Image 2 is the approved opening. Judge the ending against the written staging. "
                         "Reject a dissolve, an inter-room morph, or an apparent jump with no visible action. " if not paired else
                         "Image 2 is the approved start and image 3 is the approved ending target. "
                         "Compare the first and last sampled panels to their respective images. ")
            if paired:
                files.append(folder / end_file(shot))
            transition = ""
            if next_shot is not None:
                files.append(folder / still_file(next_shot))
                transition = (f"Image {len(files)} is the approved next shot on a continuous cut. "
                              "It is a separate reference for the cut, never a panel from this clip. "
                              "A cut to a different scripted character in the same scene is allowed. "
                              "Check recurring characters, story-critical props and setting for true discontinuities. ")
            mouth_review = ("Check that only the assigned visible speaker moves their mouth; dialogue is dubbed separately. "
                            if shot.get("speaker_description") else "Flag visible characters speaking under narration. ")
            motion_rubric = (
                "Read the 16 frames left to right across each row, including the first and last frame. "
                + reference + "Reject a missed ending on any story-critical position, pose, or prop. " + transition
                + (f"Required motion and ending for this retake: {retake_prompt} "
                   if retake_prompt else
                   f"Required motion: {shot['motion']}. Expected ending: {describe(shot.get('end', {}))}. ") + mouth_review +
                "Reject any photoreal, live-action or 3D-looking frame, including brief style changes. "
                "Reject a new face, wrong wardrobe/scale, a reversed walk or threshold jump, or missing action. "
                "Reject a warped diary, hand fusion, or physically wrong reflection. "
                "Point to the first visibly bad frame and explain why. Minor distance or gesture differences can pass. "
                "Check whether motion happened early and the actor then held their final pose. Compare the actor's "
                "feet, hips and position to fixed landmarks across the whole sequence before calling them stationary. "
                "For locomotion, tiny differences in stride count or distance are acceptable when travel in the "
                "required direction and the story-critical destination are visible."
            )
            result = review_motion(files, motion_rubric)
            if result.get("action_matches") is False and result.get("style") == "illustrated_2d":
                evidence = [clip]
                clip_refs = "Watch the clip itself in temporal order. "
                if paired:
                    evidence.append(folder / end_file(shot))
                    clip_refs += "The first separate image is the planned final frame. "
                if next_shot is not None:
                    evidence.append(folder / still_file(next_shot))
                    clip_refs += "The last separate image belongs to the next shot and is outside the video. "
                action = retake_prompt or shot["motion"]
                finish = shot.get("end", {}).get("staging", "")
                walking_note = ""
                if re.search(r"\b(walks?|steps?|porch|threshold)\b", action, re.I):
                    walking_note = (
                        "For a walk, compare the person's initial, intermediate and final positions against "
                        "fixed features. Mark the action matched if the person travels in the intended direction "
                        "under their own power and reaches the intended surface or side. A person can stand on "
                        "the porch while still overlapping the doorway in the camera view. Exact stride count "
                        "and distance are minor when the destination is visible. "
                    )
                confirmation = review_motion(evidence,
                    clip_refs + f"The expected action is {action} The ending should show {finish} "
                    + walking_note + "An early action followed by a held final pose is valid. "
                    "Check preservation of character, key props, camera, layout and 2D style. "
                    "Include visible before and after positions with approximate timestamps. "
                    + mouth_review)
                if confirmation["pass"]:
                    result = confirmation
                else:
                    result = {**confirmation, "initial_reason": result["reason"]}
    except (RuntimeError, StopIteration, ValueError, subprocess.SubprocessError) as exc:
        if "Gemini" in str(exc):
            raise
        result = {"pass": False, "technical_failure": True,
                  "reason": "Frame validation failed. " + str(exc)[:240]}
    result["signature"] = signature
    result["evidence_version"] = MOTION_EVIDENCE_VERSION
    store.save(report, result)
    return result

def generate(folder, ident, shot_plan, check, progress):
    canonical, starts = still_batches(shot_plan)
    recover_saved_images(folder,canonical+starts,check)
    looks = {character["tag"]: character["prompt"] for character in shot_plan.get("cast", [])}
    shots = []
    for original in shot_plan["shots"]:
        shot = dict(original)
        entering = set(shot.get("end", {}).get("cast", [])) - set(shot.get("start", {}).get("cast", []))
        if entering:
            shot["motion"] += " Entering cast: " + " ".join(f"{tag}: {looks[tag]}" for tag in sorted(entering))
        shots.append(shot)
    successors = {a["id"]: b for a, b in zip(shots, shots[1:]) if b["transition"] == "continuous"}
    store.save(folder / "raghu-generation-plan.json", {
        "motion_model": motion_model(),
        "canonical_images": canonical,
        "opening_images": starts,
        **motion_plan(shots),
    })
    selected_path = folder / "saved-still-selection.json"
    selection = store.read(selected_path) if selected_path.is_file() else None
    skip_motion_review = bool(selection and selection.get("skip_motion_review"))
    if selection:
        for shot in shots:
            name = still_file(shot)
            chosen = selection["frames"].get(name)
            if chosen:
                source = chosen["file"]
                if source not in {name, "rejected-" + name}:
                    raise RuntimeError(f"Invalid saved image selection for {name}.")
                data = (folder / source).read_bytes()
                if hashlib.sha256(data).hexdigest() != chosen["sha256"]:
                    raise RuntimeError(f"Saved image selection changed for {name}.")
                if source != name:
                    target = folder / name
                    backup = folder / ("before-selection-" + name)
                    if not backup.exists():
                        shutil.copyfile(target, backup)
                    temporary = target.with_suffix(".selected.png")
                    temporary.write_bytes(data)
                    temporary.replace(target)
            if not (folder / name).is_file():
                raise RuntimeError(f"Saved starting image is missing: {name}.")

    def approved_motion(shot):
        if not AI_VISUAL_REVIEW and (folder / f"shot-{shot['id']}.mp4").is_file():
            return validate_motion(folder, shot, successors.get(shot["id"]))["pass"]
        report = folder / f"motion-{shot['id']}.review.json"
        if not report.is_file():
            return False
        verdict = store.read(report)
        signature = motion_signature(folder, shot, successors.get(shot["id"]))
        return bool(signature and verdict["pass"] and verdict.get("signature") == signature)

    def review_saved_assets():
        for item in ([] if selection else canonical + starts):
            if (folder / item["file"]).is_file():
                assess_still(folder, item, check)
        if starts and not selection and all(
            (folder / item["file"]).is_file()
            and (report := folder / (item["file"] + ".review.json")).is_file()
            and store.read(report).get("pass")
            for item in starts
        ):
            still_pair_checks(folder, shots, check)
        for shot in shots:
            successor = successors.get(shot["id"])
            report = folder / f"motion-{shot['id']}.review.json"
            if not report.is_file() and motion_signature(folder, shot, successor):
                check()
                validate_motion(folder, shot, successor)

    def recover_reviews(report, percent):
        for attempt in range(1, REVIEW_RECOVERY_ATTEMPTS + 1):
            report(f"Review unavailable · checking saved work in 15 seconds ({attempt}/{REVIEW_RECOVERY_ATTEMPTS})", percent)
            wake = time.monotonic() + REVIEW_RETRY_SECONDS
            while (remaining := wake - time.monotonic()) > 0:
                check()
                time.sleep(min(remaining, 1))
            check()
            try:
                review_saved_assets()
                return
            except GeminiUnavailable:
                if attempt == REVIEW_RECOVERY_ATTEMPTS:
                    raise

    try:
        review_saved_assets()
    except GeminiUnavailable:
        recover_reviews(progress, 42)
    missing = [shot for shot in shots if not approved_motion(shot)]
    if not missing and not raghu_narration.pending_clone(folder, shots):
        journal=folder/'raghu-rental.json'
        if journal.is_file() and not store.read(journal).get('destroyed'):
            from .raghu_vast import finish_rental
            check()
            finish_rental(journal,True)
        return
    recovery_options = {"resume_seconds": selection["resume_seconds"]} if selection else {"progress": progress}
    recovery = folder / "vast-preserve-and-replace.json"
    if not selection and any(shot.get("simple_prompt") for shot in shots) and not recovery.is_file():
        store.save(recovery, {"replace_instance": None, "user_recovery": True})
    if not selection and recovery.is_file():
        recovery_state = store.read(recovery)
        saved_window = recovery_state.get("continuity_resume_seconds")
        if saved_window:
            recovery_options.update(resume_seconds=saved_window,
                                    resume_window=recovery_state.get("resume_window", "continuity-review"))
    if skip_motion_review:
        recovery_options["resume_window"] = "unreviewed-edit"
    with rent(folder, ident, check, pending_shots=len(missing), **recovery_options) as remote:
        phase = 28
        recovered_review = False

        def progress_at(message, percent):
            nonlocal phase
            phase = max(phase, percent)
            progress(message, phase)

        while True:
            try:
                if selection:
                    progress_at("Using the selected saved frames · no still generation", 50)
                    raghu_narration.produce_clone(remote, folder, shots, check, progress_at, ASSETS)
                    if missing:
                        animate_stills(remote, folder, missing, successors, check, progress_at)
                else:
                    generate_on_rental(remote, folder, canonical, starts, shots, successors,
                                       approved_motion, check, progress_at)
                return
            except GeminiUnavailable:
                remote.stop_for_recovery()
                if recovered_review:
                    raise
                recovered_review = True
                parked_at = time.monotonic()
                progress_at("Vast GPU stopped · retrying review on saved work", phase)
                recover_reviews(progress_at, phase)
                missing = [shot for shot in shots if not approved_motion(shot)]
                if not missing and not raghu_narration.pending_clone(folder, shots):
                    return
                remote.resume_after_review(time.monotonic() - parked_at)
            except (VastConnectionLost, RenderReviewNeedsRetake) as exc:
                if isinstance(exc, VastConnectionLost):
                    from .raghu_vast import explain_connection_loss
                    explain_connection_loss(exc)
                progress_at(str(exc), phase)
                if (folder / "vast-preserve-and-replace.json").is_file():
                    raise
                remote.pause_and_restart(progress_at, phase)
            except Exception:
                remote.stop_for_recovery()
                raise


def generate_on_rental(remote, folder, canonical, starts, shots, successors, approved_motion, check, progress):
    remote.image_download_progress = lambda message: progress(message, 28)
    progress("Generating character and background images", 28)
    rejected = review_stills(remote, folder, canonical, check, lambda message: progress(message, 28))
    if rejected:
        raise RenderReviewNeedsRetake("Character or location references still need correction: " + ", ".join(rejected))
    approved_canon = [folder / t["file"] for t in canonical]
    remote.push(approved_canon)
    remote.phase("mv character-*.png background-*.png results/", 40)
    progress("Generating scene starting images", 42)
    remote.image_download_progress = lambda message: progress(message, 42)
    rejected = review_stills(remote, folder, starts, check, lambda message: progress(message, 42))
    if rejected:
        if any(item.get("strict_opening") and item["file"] in rejected for item in starts):
            remote.stop_for_recovery()
            raise RuntimeError("Opening images still failed after their retakes. Vast is stopped and the files are saved: "
                               + ", ".join(rejected))
        raise RenderReviewNeedsRetake("Starting images still need visual corrections: " + ", ".join(rejected))
    mismatches = still_pair_checks(folder, shots, check)
    round_id = retry_round(folder)
    for _ in range(2 if any(shot.get("simple_prompt") for shot in shots) else 1):
        if not mismatches:
            break
        corrections = []
        for shot, item in zip(shots, starts):
            reason = mismatches.get(shot["id"])
            if reason:
                name = item["file"]
                if item.get("strict_opening"):
                    checkpoint = folder / (name + ".retakes.json")
                    count = retry_state(checkpoint, round_id).get("count", 0)
                    if count >= item["max_retakes"]:
                        continue
                image = folder / name
                report = folder / (name + ".review.json")
                prior = store.read(report)
                try:
                    rewritten = rewrite_still_prompt(image, item["prompt"], reason)
                except GeminiUnavailable:
                    rewritten = "Fix this visible continuity problem: " + reason[:350]
                image.replace(folder / ("rejected-" + name))
                report.replace(folder / ("rejected-" + name + ".review.json"))
                correction = {**item, "seed": prior["candidate_seed"] + 1000,
                              "prompt": f"{rewritten} Locked requirements: {item['prompt']}"}
                if item.get("strict_opening"):
                    store.save(checkpoint, {"count": count + 1, "round": round_id, "task": correction})
                corrections.append(correction)
        if not corrections:
            break
        rejected = review_stills(remote, folder, corrections, check)
        mismatches = still_pair_checks(folder, shots, check) if not rejected else rejected
    if mismatches:
        raise RenderReviewNeedsRetake("Starting images need continuity corrections before animation: "
                           + "; ".join(f"{name}: {reason}" for name, reason in list(mismatches.items())[:6]))
    # A changed start or destination invalidates the old approval for that cut.
    missing = [shot for shot in shots if not approved_motion(shot)]
    raghu_narration.produce_clone(remote, folder, shots, check, progress, ASSETS)
    if not missing:
        return
    animate_stills(remote, folder, missing, successors, check, progress)


def animate_stills(remote, folder, missing, successors, check, progress):
    approved = [folder / still_file(s) for s in missing]
    for i in range(0, len(approved), 12):
        remote.push(approved[i:i+12])
    remote.phase("mv still-*.png results/", 60)
    model = motion_model()
    if uses_ending_image():
        prepare_ending_frames(remote, folder, missing, successors, check, progress, approved)
    model_name = {"wan22": "Wan 2.2", "hunyuan15": "HunyuanVideo-1.5", "ltx": "LTX-2.5"}[model]
    progress(f"Preparing {model_name} motion weights", 54)
    remote.start_motion_setup()
    remote.wait_motion_setup()
    remote.pull(["events.jsonl"], folder)
    for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["stage"] == "model_download":
            logging.warning("%s download %s: %s bytes in %.1fs", model_name,
                            event["file"], event["bytes"], event["seconds"])
    progress(f"Animating the starting images with {model_name}", 56)
    # Old queue manifests are history, not new work for a restarted watch process.
    remote.push([ASSETS/'motion_recovery.py'])
    remote.phase('python motion_recovery.py --park',60)
    remote.phase("{ nohup env RAGHU_WATCH=1 bash worker.sh motion >results/motion.log 2>&1 </dev/null & echo $! > motion.pid; }", 30)
    blocked = render_motion_queue(remote, folder, missing, successors, check, progress)
    remote.pull(["events.jsonl"], folder)
    if blocked:
        raise RenderReviewNeedsRetake("Motion review blocked final editing after targeted retakes: " + "; ".join(blocked[:6]))
    preserve = (folder / "vast-preserve-and-replace.json").is_file()
    progress("All source clips saved. Stopping Vast rental" if preserve else
             "All source clips saved. Destroying Vast rental", 79)


def prepare_ending_frames(remote, folder, missing, successors, check, progress, approved):
    progress("Creating ending frames from the starting images", 52)
    endings = end_frame_tasks(missing, folder, successors)
    opening_refs = {f"results/{path.name}" for path in approved}
    destination_refs = sorted({ref.removeprefix("results/") for item in endings for ref in item["refs"]
                               if ref not in opening_refs})
    for index in range(0, len(destination_refs), 12):
        batch = destination_refs[index:index + 12]
        remote.push([folder / name for name in batch])
        remote.phase("mv -- " + " ".join(batch) + " results/", 60)
    for item in endings:
        path = folder / item["file"]
        report = folder / (path.name + ".review.json")
        signature = end_frame_source_signature(folder, item)
        if path.exists() and (not report.exists() or store.read(report).get("source_signature") != signature):
            path.replace(folder / ("rejected-" + path.name))
            report.unlink(missing_ok=True)
    rejected = review_stills(remote, folder, endings, check, lambda message: progress(message, 52))
    if rejected:
        raise RenderReviewNeedsRetake("Ending frames need correction before motion: " + ", ".join(rejected))
    for i in range(0, len(endings), 12):
        remote.push([folder / item["file"] for item in endings[i:i+12]])
    remote.phase("mv end-*.png results/", 60)


def render_motion_queue(remote, folder, shots, successors, check, progress):
    """Keep one batch ready while reviewing the batch being collected."""
    round_id = retry_round(folder)
    by_id = {shot["id"]: shot for shot in shots}
    tickets={}

    def checkpoint(ident):
        return folder / f"motion-{ident}.retakes.json"

    def submit(batch, corrections=None):
        check()
        render = motion_plan(batch)
        for item in render["shots"]:
            if corrections:
                current = retry_state(checkpoint(item["id"]), round_id)
                item["seed"] += 1000
                override = motion_retake_prompt(folder, item["id"])
                if override:
                    item["seed"] += 1000
                item["video_prompt"] = override or (
                    "Retake priorities: preserve the opening composition and follow only the scripted camera. "
                    "Keep subject size stable when the camera is locked. Make the specified final pose and "
                    "the key hand or prop action clearly visible. Avoid these observed errors: "
                    + corrections[item["id"]][:550] + "\n" + item["video_prompt"]
                )
                if current.get("prompt"):
                    item["video_prompt"] = current["prompt"]
                    item["seed"] += current["serial"] * 1000
            inputs={name:hashlib.sha256((folder/item[name]).read_bytes()).hexdigest()
                    for name in ('starting_image_file','ending_image_file') if item.get(name)}
            invalidation=folder/f"motion-{item['id']}.invalidate.json"
            journal=folder/'raghu-rental.json'
            quantization=store.read(journal).get('quantization') if journal.is_file() else os.getenv('RAGHU_QUANTIZATION','fp8')
            payload=dict(request=item,images=inputs,model=motion_model(),quantization=quantization,
                         invalidation=store.read(invalidation) if invalidation.is_file() else None)
            item['recovery_key']=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            if not invalidation.is_file() and not corrections and motion_model()=='ltx':
                item['legacy_recovery']=dict(prompt=item['video_prompt'],seed=item['seed'],num_frames=item['frames'],
                    fps=item['fps'],source_sha256=inputs['starting_image_file'])
                if item.get('ending_image_file'):
                    item['legacy_recovery'].update(end_sha256=inputs['ending_image_file'],ending_frame=item['ending_frame'])
            (folder / f"motion-{item['id']}.review.json").unlink(missing_ok=True)
        ticket = "motion-request-" + uuid.uuid4().hex
        manifest(remote, folder, ticket + ".staged", render)
        remote.phase(f"python motion_recovery.py {ticket}.staged && mv {ticket}.staged {ticket}.json", 60)
        for shot in batch:tickets[shot['id']]=ticket

    def collect(batch, pool, on_started=None):
        reviews = []
        ticket=tickets[batch[0]['id']]
        remote.phase(f"while [ ! -s results/{ticket}.accepted ]; do "
                     "ps -p $(cat motion.pid) -o stat= | grep -q '^[^Z]' || exit 1; sleep 2; done",1800)
        if on_started is not None:on_started()
        for index, shot in enumerate(batch):
            check()
            clip = f"shot-{shot['id']}.mp4"
            remote.phase(f"while [ ! -s results/{clip} ]; do "
                         "ps -p $(cat motion.pid) -o stat= | grep -q '^[^Z]' || exit 1; "
                         "sleep 2; done", 1800 if motion_model() == "hunyuan15" else 900 if motion_model() == "wan22" else 600)
            remote.pull([clip], folder)
            reviews.append(pool.submit(validate_motion, folder, shot, successors.get(shot["id"])))
        started = time.monotonic()
        failed = {shot["id"]: result["reason"] for shot, future in zip(batch, reviews)
                  if not (result := future.result())["pass"]}
        logging.info("Motion batch: %s rendered, %s flagged, %.1fs waiting after last transfer",
                     len(batch), len(failed), time.monotonic() - started)
        return failed

    def archive(shot):
        ident = shot["id"]
        for name in (f"shot-{ident}.mp4", f"motion-{ident}.review.json"):
            (folder / name).replace(folder / ("rejected-" + name))

    def prepare_retake(shot, reason):
        ident = shot["id"]
        path = checkpoint(ident)
        previous = store.read(path) if path.is_file() else {}
        count = retry_state(path, round_id).get("count", 0)
        if count >= 2:
            return False
        brief = opening_motion_prompt(shot)
        clip = folder / f"shot-{ident}.mp4"
        prompt = brief
        if AI_VISUAL_REVIEW:
            with tempfile.TemporaryDirectory(dir=folder) as tmp:
                sheet = Path(tmp) / "frames.jpg"
                try:
                    media.ff("-i", clip, "-vf", "select='not(mod(n,24))',scale=320:-2,tile=3x2",
                             "-frames:v", "1", sheet)
                    prompt = rewrite_still_prompt(sheet, brief, reason, motion=True)
                except (GeminiUnavailable, RuntimeError, subprocess.SubprocessError):
                    logging.warning("Motion prompt rewrite unavailable for shot %s; using the locked action", ident)
        store.save(path, {"count": count + 1, "round": round_id,
                          "serial": previous.get("serial", 0) + 1, "prompt": prompt})
        archive(shot)
        return True

    if not shots:
        return []
    fresh = []
    retry = []
    reasons = {}
    blocked = []
    complete = 0
    for shot in shots:
        ident = shot["id"]
        clip = folder / f"shot-{ident}.mp4"
        rejected_report = folder / f"rejected-motion-{ident}.review.json"
        if clip.is_file():
            result = validate_motion(folder, shot, successors.get(ident))
            if result["pass"]:
                complete += 1
                continue
            if shot.get("simple_prompt"):
                if not prepare_retake(shot, result["reason"]):
                    blocked.append(f"{ident}: {result['reason']}")
                    continue
            elif rejected_report.is_file():
                blocked.append(f"{ident}: {result['reason']}")
                continue
            else:
                archive(shot)
        if rejected_report.is_file():
            retry.append(shot)
            reasons[ident] = store.read(rejected_report)["reason"]
        else:
            fresh.append(shot)
    batches = [fresh[i:i + 5] for i in range(0, len(fresh), 5)]
    if batches:
        submit(batches[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, batch in enumerate(batches):
            following = batches[index + 1] if index + 1 < len(batches) else None
            failed = collect(batch, pool, (lambda: submit(following)) if following else None)
            if failed:
                for shot in batch:
                    if shot["id"] in failed:
                        if not shot.get("simple_prompt"):
                            archive(shot)
                            retry.append(shot)
                        elif prepare_retake(shot, failed[shot["id"]]):
                            retry.append(shot)
                        else:
                            blocked.append(f"{shot['id']}: {failed[shot['id']]}")
                reasons.update(failed)
            complete += len(batch) - len(failed)
            progress(f"Motion files saved {complete}/{len(shots)} pending shots",
                     56 + int(22 * complete / len(shots)))
        while retry:
            progress(f"Retaking {len(retry)} flagged motion clips", 56 + int(22 * complete / len(shots)))
            submit(retry, reasons)
            failures = collect(retry, pool)
            complete += len(retry) - len(failures)
            retry = []
            for ident, reason in failures.items():
                shot = by_id[ident]
                if shot.get("simple_prompt") and prepare_retake(shot, reason):
                    retry.append(shot)
                    reasons[ident] = reason
                else:
                    blocked.append(f"{ident}: {reason}")
        if complete or blocked:
            progress(f"Motion files saved {len(shots)}/{len(shots)} pending shots", 78)
    return blocked

def narration(folder, shots, check, progress):
    if raghu_narration.silent():
        return
    if raghu_narration.uses_clone():
        if raghu_narration.pending_clone(folder, shots):
            raise RuntimeError("Some cloned narrator lines are missing. Retry the saved narration stage.")
        dialogue = [shot for shot in shots if not raghu_narration.narrator_line(shot)]
        if dialogue:
            raghu_narration.produce(raghu_narration.narrator_folder(folder), dialogue, check, progress)
        return
    if os.getenv("RAGHU_NARRATION_PROVIDER", "gemini") == "edge":
        from .raghu_narration import narrator_folder, produce
        produce(narrator_folder(folder), shots, check, progress)
        return
    selection_path = folder / "saved-still-selection.json"
    narration_model = store.read(selection_path).get("narration_model") if selection_path.is_file() else None
    for i, shot in enumerate(shots):
        check()
        path = folder / f"narration-{shot['id']}.wav"
        if not raghu_narration.readable_voice(path):
            for attempt in range(4):
                check()
                try:
                    if narration_model:
                        narrate(shot["narration"], path, model=narration_model)
                    else:
                        narrate(shot["narration"], path)
                    break
                except GeminiUnavailable as exc:
                    if attempt == 3:
                        fallback_model = os.getenv("GEMINI_TTS_FALLBACK_MODEL", "").strip()
                        if not fallback_model or "HTTP 429" not in str(exc):
                            raise
                        narrate(shot["narration"], path, model=fallback_model)
                        break
                    pause = (15, 30, 60)[attempt]
                    progress(f"Narration {i}/{len(shots)} saved · Gemini cooling down for {pause}s", 81)
                    for _ in range(pause // 5):
                        check()
                        time.sleep(5)
        if i % 10 == 0:
            progress(f"Narration {i+1}/{len(shots)}", 81)

def edit(folder, shot_plan, check, *, music=True, opening=False, sound_effects=False, music_track=None):
    """Retain a full frame at each cut and derive each shot end from its spoken line."""
    cuts = []
    timeline = []
    opening_file = opening_path() if opening else None
    opening_seconds = media.duration(opening_file) if opening_file else 0.0
    custom_seconds = shot_plan.get("requested_seconds")
    shot_count = len(shot_plan["shots"])
    cursor = 0.0
    audio_cuts = []
    speech_cursor = 0.0
    audio_folder = folder
    if os.getenv("RAGHU_NARRATION_PROVIDER", "gemini") == "edge":
        from .raghu_narration import narrator_folder
        audio_folder = narrator_folder(folder)
    with tempfile.TemporaryDirectory(prefix="raghu-edit-") as scratch:
        scratch = Path(scratch)
        for index, shot in enumerate(shot_plan["shots"]):
            check()
            sound = dict(score='none', sound_cues=[], unresolved_sound_cues=[])
            ident = shot["id"]
            clip = folder / f"shot-{ident}.mp4"
            if raghu_narration.uses_clone():
                speech_folder = (raghu_narration.clone_folder(folder) if raghu_narration.narrator_line(shot)
                                 else raghu_narration.narrator_folder(folder))
            else:
                speech_folder = audio_folder
            speech = speech_folder / f"narration-{ident}.wav"
            if raghu_narration.silent():
                speech = scratch / f"no-narration-{ident}.wav"
            trimmed = scratch / f"voice-{ident}.wav"
            if speech.exists():
                # Preserve all breaths and pauses; normalize format, never tempo or duration.
                media.ff("-i", speech, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", trimmed)
                spoken = media.duration(trimmed)
                if spoken < .2:
                    raise RuntimeError(f"Narration for shot {ident} is empty.")
                speech_cursor += spoken
                # Distribute frame rounding over the timeline, rather than adding a frame per line.
                length = (math.ceil(speech_cursor * FPS - 1e-6) - round(cursor * FPS)) / FPS
            else:
                if not raghu_narration.silent():
                    raise RuntimeError(f"Narration for shot {ident} is missing. Retry the saved narration stage.")
                available = min(5, media.duration(clip))
                if custom_seconds:
                    total_frames = custom_seconds * FPS
                    length = ((index + 1) * total_frames // shot_count - index * total_frames // shot_count) / FPS
                else:
                    length = available
                media.tone(trimmed, length, "none")
                # All entries in the audio concat have the same PCM format.
                normalized = scratch / f"silent-{ident}.wav"
                media.ff("-i", trimmed, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", normalized)
                trimmed = normalized
            video_filter = "setsar=1,fps=24"
            if motion_model() in {"wan22", "hunyuan15"}:
                video_filter = ("scale=1280:768:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                                "pad=1280:768:(ow-iw)/2:(oh-ih)/2," + video_filter)
            hold = max(0, length - media.duration(clip))
            if hold:
                video_filter += f",tpad=stop_mode=clone:stop_duration={hold + 1 / FPS:.6f}"
            cut = scratch / f"cut-{ident}.mp4"
            media.ff("-i", clip, "-an", "-vf", video_filter,
                     "-t", f"{length:.6f}", "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "17", "-pix_fmt", "yuv420p", cut)
            audio_cuts.append(trimmed)
            cuts.append(cut)
            timeline.append({"id": ident, "start": round(cursor + opening_seconds, 4), "seconds": length,
                             "narration": shot.get("narration", ""), **sound})
            cursor += length
        concat = scratch / "cuts.ffconcat"
        concat.write_text("ffconcat version 1.0\n" + "".join(f"file {p.name}\n" for p in cuts), encoding="ascii")
        joined = scratch / "joined.mp4"
        picture = scratch / "picture.mp4"
        media.ff("-f", "concat", "-safe", "1", "-i", concat, "-c", "copy", picture)
        audio_concat = scratch / "speech.ffconcat"
        audio_concat.write_text("ffconcat version 1.0\n" + "".join(f"file {p.name}\n" for p in audio_cuts), encoding="ascii")
        speech_track = scratch / "speech.wav"
        media.ff("-f", "concat", "-safe", "1", "-i", audio_concat, "-c:a", "pcm_s16le", speech_track)
        # Encode speech once for the whole film, avoiding AAC padding between scenes.
        speech_filter = "anull" if raghu_narration.silent() else "loudnorm=I=-16:TP=-2:LRA=9,volume=0.765"
        media.ff("-i", picture, "-i", speech_track, "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", "-af", speech_filter, "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k", joined)
        final = scratch / "final.mp4"
        # Only a newly supplied user track may accompany the raw voice tracks.
        # Legacy effect flags, score tags and the old Obsidian bed are ignored.
        music_source = (raghu_music.source(music_track) if music_track is not None
                        else raghu_music.source()) if music else None
        if music_source:
            bed = scratch / "user-music.wav"
            raghu_music.render(bed, cursor, music_source)
            raghu_music.mix(joined, final, bed)
        else:
            shutil.copyfile(joined, final)
        store.save(folder / 'sound-effects-report.json', dict(
            effects=[], timing_source='disabled', observations=[],
            music_source=str(music_source) if music_source else None,
            policy='raw narration/dialogue plus explicitly supplied user music only'))
        if opening:
            shutil.copyfile(opening_file, scratch / "opening.mp4")
            final.replace(scratch / "story.mp4")
            concat.write_text("ffconcat version 1.0\nfile opening.mp4\nfile story.mp4\n", encoding="ascii")
            media.ff("-f", "concat", "-safe", "1", "-i", concat, "-c", "copy",
                     "-movflags", "+faststart", final)
        media.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(final), "-f", "null", "-"])
        published = folder / "final.partial.mp4"
        shutil.copyfile(final, published)
        published.replace(folder / "final.mp4")
    store.save(folder / "raghu-timeline.json", {"duration": media.duration(folder / "final.mp4"),
                                               "opening_seconds": opening_seconds, "shots": timeline})

def saved_production_plan(folder):
    path = folder / "raghu-plan-v2.json"
    if not path.is_file():
        raise RuntimeError("The approved script and storyboard checkpoint is missing.")
    saved = store.read(path)
    seconds = saved.get("seconds")
    if type(seconds) is not int or not 30 <= seconds <= 600 or seconds % 5:
        raise RuntimeError("The approved storyboard has an invalid duration.")
    if not saved.get("script") or not isinstance(saved.get('shots'), list) or not saved['shots']:
        raise RuntimeError("The approved script and storyboard checkpoint is incomplete.")
    return saved


def work(ident, folder, request, check, progress):
    saved = saved_production_plan(folder) if request.get("raghu_produce_saved") else None
    planning_only = saved is None and (
        request.get("raghu_plan_only") or os.getenv("RAGHU_PLAN_ONLY", "").lower() == "true")
    if not planning_only:
        validate_music_for_request(request)
        production_credentials()
    requested_seconds = saved.get("requested_seconds", saved["seconds"]) if saved else request.get("duration_seconds")
    if requested_seconds is None:
        requested_seconds = int(os.getenv("RAGHU_TARGET_SECONDS", "600"))
    if type(requested_seconds) is not int or not 30 <= requested_seconds <= 600:
        raise RuntimeError("Story duration must be between 30 and 600 whole seconds.")
    seconds = 5 * ((requested_seconds + 4) // 5)
    if not planning_only:
        # Saved starts, motion and the export all occupy the persistent Railway volume.
        # Editing scratch lives on the separate container filesystem.
        required_bytes = 24_000_000 + seconds // 5 * 6_000_000
        saved_bytes = sum(path.stat().st_size for path in folder.iterdir()
                          if path.is_file() and path.suffix in (".mp4", ".png", ".wav"))
        if shutil.disk_usage(folder).free + saved_bytes < required_bytes:
            raise RuntimeError("Railway needs more free persistent storage for this film. No GPU was rented.")
        intro = opening_path()
        if not raghu_narration.silent() and (not intro.is_file() or media.duration(intro) < 6):
            raise RuntimeError("Install the intro and disclaimer as a video of at least six seconds. No GPU was rented.")
    if request.get("script_options") and saved is None and not planning_only:
        store.save(folder / "script-choice-enabled.json", {"enabled": True})
    legacy_plan = folder / "raghu-plan.json"
    recover_legacy = saved is None and os.getenv("RAGHU_FINISH_FROM_SAVED") == ident and legacy_plan.is_file()
    if saved is not None:
        shot_plan = saved
        progress("Approved script and storyboard loaded · preparing Vast rental", 24)
    elif recover_legacy:
        shot_plan = store.read(legacy_plan)
    else:
        if legacy_plan.is_file() and not (folder / "raghu-plan-v2.json").exists():
            raise RuntimeError("This saved production uses the old shot plan. Its assets are preserved. "
                               "Start a new production to use the continuity checks.")
        from . import youtube_clone_writer
        if youtube_clone_writer.enabled(seconds):
            if not planning_only and uses_ending_image():
                raise RuntimeError("Clone YouTube production requires only the opening image.")
            from . import clone_chat_planner
            writer = clone_chat_planner
            shot_plan = writer.plan(folder, request["title"], request.get("choose_topic"),
                                    check, progress, seconds=seconds)
        else:
            required("DEEPSEEK_API_KEY")
            required("DEEPSEEK_MODEL")
            progress("DeepSeek is writing the story and production prompts", 8)
            title = choose_title(folder, check) if request.get("choose_topic") else request["title"]
            if request.get("choose_topic"):
                progress(f"DeepSeek is writing {title}", 9)
            extra_runs = request.get("planning_resumes", 0)
            if extra_runs:
                shot_plan = plan(folder, title, seconds, check, progress, extra_runs=extra_runs)
            else:
                shot_plan = plan(folder, title, seconds, check, progress)
    if requested_seconds != seconds:
        shot_plan = {**shot_plan, "requested_seconds": requested_seconds}
    store.save(folder / "story.json", shot_plan)
    setting = folder / "clone-story-setting.json"
    if setting.is_file():
        script = shot_plan["script"]
        store.remember_script(ident, {"clone_setting": store.read(setting)["setting"],
                              "title": shot_plan.get("title"),
                              "cast": [{"tag": a["tag"], "look": a.get("identity_description") or a["prompt"]} for a in shot_plan.get("cast", [])],
                              "locations": [a["prompt"] for a in shot_plan.get("locations", [])]},
                              script, hashlib.sha256(script.encode()).hexdigest())
    if planning_only:
        return "Script and storyboard ready · planning only"
    if recover_legacy:
        progress("Finishing with saved motion and stills", 79)
        fallback = []
        for shot in shot_plan["shots"]:
            check()
            shot_id = shot["id"]
            target = folder / f"shot-{shot_id}.mp4"
            if target.is_file() and media.duration(target) >= 4.75:
                continue
            image = folder / still_file(shot)
            if not image.is_file():
                raise RuntimeError(f"Missing saved image for shot {shot_id}.")
            temp = target.with_suffix(".partial.mp4")
            media.ff("-i", image, "-vf",
                     "scale=1536:922,crop=1536:922,zoompan=z='1+0.0005*on':x='iw/2-iw/zoom/2':"
                     "y='ih/2-ih/zoom/2':d=121:s=1280x768:fps=24,setsar=1",
                     "-frames:v", str(FRAMES), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", temp)
            temp.replace(target)
            fallback.append(shot_id)
        store.save(folder / "saved-edit-recovery.json", {"camera_motion_from_still": fallback})
        logging.warning("Saved edit recovery used starting images for shots: %s", ", ".join(fallback) or "none")
    else:
        generate(folder, ident, shot_plan, check, progress)
    progress("Vast closed · preparing silent video" if raghu_narration.silent()
             else "Vast closed. Generating Hindi narration", 80)
    narration(folder, shot_plan["shots"], check, progress)
    progress("Editing the silent video" if raghu_narration.silent()
             else "Editing picture to narration", 90)
    edit(folder, shot_plan, check, opening=not raghu_narration.silent(),
         music=os.getenv("RAGHU_BACKGROUND_AUDIO", "true").lower() in {"1", "true", "yes"},
         sound_effects=False, music_track=request.get('music_track'))
    logging.warning("Finished saved production %s", ident)

def smoke(ident, folder, mode, check, progress):
    """Generate exactly one image. Only the motion check can render one clip."""
    credentials = ["VAST_API_KEY", "VAST_SSH_PRIVATE_KEY"]
    if mode == "motion" and motion_model() == "ltx":
        credentials.append("HF_TOKEN")
    for name in credentials:
        required(name)
    report_path = folder / "raghu-smoke.json"
    image = folder / "smoke-image.png"
    clip = folder / "shot-001.mp4"
    shot = {"id": "001", "motion": "The young man takes one grounded step backward away from "
            "the closed door toward screen right, raises the flashlight slightly, and stops. "
            "Locked medium-wide view. Keep his face, watch, mustard shirt and the door fixed."}
    item = task(image.name, "One adult Indian man, short black hair and thin mustache, mustard shirt, "
                "charcoal trousers and steel watch. He stands at screen center facing the closed wooden "
                "door at screen left. He holds one short black flashlight in his right hand. "
                "Show clear space behind him at screen right. Warm wall light and cool teal plaster. "
                "One complete pre-action pose, natural hands, no text or extra people.",
                ["style-interior.jpg"], 57300)
    if report_path.exists():
        saved = store.read(report_path)
        if not AI_VISUAL_REVIEW and image.is_file():
            saved["visual_review"] = check_image_file(image)
            saved["review_seconds"] = 0
            store.save(report_path, saved)
        if (folder / "raghu-rental.json").exists() and store.read(folder / "raghu-rental.json").get("destroyed"):
            if (mode == "image" and image.exists()) or (mode == "motion" and clip.exists()
                    and "clip_render_seconds" in saved
                    and (saved.get("motion_model", "ltx") == motion_model())
                    and (not uses_ending_image() or (folder / end_file(shot)).is_file())):
                return
            if AI_VISUAL_REVIEW and not saved["visual_review"]["pass"]:
                raise RuntimeError("The saved smoke-test still was rejected by the vision model.")
    with rent(folder, ident, check, image_only=mode == "image") as remote:
        progress("Vast is generating one Raghu-style test image", 25)
        manifest(remote, folder, "still-tasks.json", [item])
        remote.phase("bash worker.sh stills still-tasks.json >results/stills.log 2>&1", 1200)
        remote.pull([image.name, "events.jsonl"], folder)
        progress("Image saved · checking file readability", 55)
        start = time.monotonic()
        assessment = check_image_file(image) if not AI_VISUAL_REVIEW else review_still([image, ASSETS / "style-interior.jpg"],
            "Railway production quality check. Inspect the first image. The second image is style only. "
            "State the visible person, held object, door position, colors and any concrete image defects "
            "in reason. Fail if the image lacks the single adult, flashlight or closed door, "
            "or has severe anatomy, composition or style defects. Expected scene: " + item["prompt"])
        events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
        output = {"mode": mode, "visual_review": assessment,
                  "review_seconds": round(time.monotonic() - start, 2),
                  "image_render_seconds": next(row["seconds"] for row in events if row["stage"] == "still_done")}
        store.save(report_path, output)
        if mode == "motion" and assessment["pass"]:
            remote.phase("cp results/smoke-image.png results/still-001.png", 15)
            shutil.copyfile(image, folder / still_file(shot))
            if uses_ending_image():
                shot["end"] = {"staging": "The man stands one step farther right, flashlight raised slightly. Door stays closed."}
                endings = end_frame_tasks([shot])
                if review_stills(remote, folder, endings, check):
                    raise RuntimeError("The smoke-test ending image did not pass review.")
                remote.push([folder / end_file(shot)])
                remote.phase("mv end-001.png results/", 30)
            progress("Animating the approved test image" if not uses_ending_image()
                     else "Animating the approved opening and ending test frames", 70)
            plan = motion_plan([shot])
            plan["shots"][0]["starting_image_file"] = image.name
            manifest(remote, folder, "render-plan.json", plan)
            remote.phase("bash worker.sh motion >results/motion.log 2>&1", 2400)
            remote.pull([clip.name, "events.jsonl"], folder)
            events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
            generated = next(row for row in events if row["stage"] == "video_done")
            if hashlib.sha256(clip.read_bytes()).hexdigest() != generated["sha256"]:
                raise RuntimeError("The saved smoke-test clip failed checksum validation.")
    if mode == "motion":
        if not assessment["pass"]:
            raise RuntimeError("Railway rejected the test image. The Vast instance has been destroyed.")
        events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
        output["clip_render_seconds"] = next(row["seconds"] for row in events if row["stage"] == "video_done")
        output["motion_model"] = motion_model()
        output["clip_duration_seconds"] = media.duration(clip)
        store.save(report_path, output)
    progress("Vast destruction verified. Smoke-test results saved", 99)


def review_check(folder, check, progress):
    """Challenge the deployed reviewer using the same real frame with two prompts."""
    if not AI_VISUAL_REVIEW:
        raise RuntimeError("AI visual review is disabled. No reviewer test is required.")
    fixture = ASSETS / "review-fixture"
    data = store.read(fixture / "fixture.json")
    frame = fixture / data["image"]
    if hashlib.sha256(frame.read_bytes()).hexdigest() != data["sha256"]:
        raise RuntimeError("The approved review fixture failed checksum validation.")
    refs = [frame, *[fixture / filename for filename in data["refs"]]]
    mismatch = ("The frame contains two adult women in bright yellow raincoats inside a "
                "moving railway carriage. One woman holds a red open umbrella. "
                "Both women are fully visible. Use flat 2D ink outlines and matte cel shading.")
    progress("Checking the same Raghu image against matching and mismatched prompts", 25)
    check()
    with ThreadPoolExecutor(max_workers=2) as pool:
        positive = pool.submit(review_still, refs, data["prompt"])
        negative = pool.submit(review_still, [frame], mismatch)
        matching, mismatched = positive.result(), negative.result()
    check()
    shutil.copyfile(frame, folder / "review-check.png")
    evidence = " ".join([matching["reason"], *matching.get("visual_defects", [])]).lower()
    prop_flagged = (
        any(word in evidence for word in ("flashlight", "torch"))
        and any(word in evidence for word in ("float", "detach", "disconnected", "not held",
                                               "not gripping", "not grasp", "no contact",
                                               "separat", "below his hand", "airborne"))
    )
    checks = {
        "2D style recognized": matching["style"] == "illustrated_2d",
        "Unheld flashlight flagged": prop_flagged and not matching["pass"],
        "Wrong scene rejected": not mismatched["pass"] and not mismatched["content_matches"],
    }
    store.save(folder / "raghu-review-check.json",
               {"passed": all(checks.values()), "checks": checks,
                "matching": matching, "mismatched": mismatched})
