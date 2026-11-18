"""Railway connection for the original Clone chat planner."""

import hashlib
import json
import re

import httpx
from pydantic import Field

from . import raghu_services, store, youtube_clone_writer
from .raghu_vast import image_model
from .youtube_clone.pipeline import planning_writer


CONTINUITY_FAILURES = (
    "Shot review still flagged continuity issues after the final repair pass",
    "A proposed repair must actually change a field",
    "Every blocking review must propose a repair",
)


class CloneChatDraft(youtube_clone_writer.Draft):
    characters: list[youtube_clone_writer.Character] = Field(min_length=1, max_length=3)
    backgrounds: list[youtube_clone_writer.Background] = Field(min_length=1, max_length=6)


def bind_locked_cast(content, locked_cast):
    """Treat provider output as asset descriptions for the script's fixed cast."""
    try:
        assets = planning_writer.json_object(content)
        entries = assets["characters"]
        by_tag = {item["tag"]: item for item in entries}
        if len(by_tag) == len(entries) and {item["tag"] for item in locked_cast} <= by_tag.keys():
            assets["characters"] = [
                {**by_tag[item["tag"]], "name": item["name"]} for item in locked_cast
            ]
            return json.dumps(assets, ensure_ascii=False)
    except (KeyError, TypeError, ValueError):
        pass
    # Missing or ambiguous locked characters still go through the original validator.
    return content


class DeepSeekWriter:
    def __init__(self, folder, check, progress):
        self.folder = folder
        self.check = check
        self.progress = progress
        self.key = raghu_services.required("DEEPSEEK_API_KEY")
        self.model = raghu_services.required("DEEPSEEK_MODEL")
        self.simpler_draft = False
        self.last_review_issues = []
        self.rewrite_notes = []
        self.last_story = None

    def complete(self, prompt, *, max_tokens, temperature):
        self.check()
        writing_story = "the exact title is the only story input" in prompt
        if writing_story:
            if image_model() == "qwen2511":
                prompt += "\nUse at most two adult characters in this story and at most two people in any shot."
            prompt += "\n" + youtube_clone_writer.setting_direction(self.folder)
            prompt += (
                "\nEach line must work as one five-second shot. Before returning the script, silently trace "
                "where each person's hands and the main prop are after each beat. If a line tells someone to "
                "remove a hand or let go, the preceding beat must put that hand there or establish the grip. "
                "Keep the cause and its immediate visible result together. Omit instructions to another character "
                "if they would require an extra unscripted movement. Aim for 11–12 words per line, "
                "counting words separated by spaces before returning the script."
            )
            if self.simpler_draft:
                prompt += "\nThe first storyboard failed continuity review. Write a fresh, simpler draft."
                if self.rewrite_notes:
                    prompt += "\nAvoid these concrete problems from the previous draft:\n" + "\n".join(
                        self.rewrite_notes[:4])
            if self.simpler_draft or "Target 30 seconds total" in prompt:
                prompt += (
                    "\nUse one physical space and one continuous time span. Build around one prop with a simple "
                    "visible supernatural change. In each beat show one movement, a short line of dialogue, "
                    "or one reaction. Keep the same object and show each change of position or ownership. "
                    "No travel, time jumps, off-screen exchanges or unclear pronouns in the final reveal."
                )
        if "Locked speech by shot" in prompt:
            prompt += (
                "\nBuild in shot order. Carry the exact side, contact and state of each important hand and prop "
                "from the prior ending into the next opening. Describe the actual change in visual_action, "
                "then write its result in ending_state. For example, if shot 2 says 'take your hand off the lid', "
                "shot 1 ends with that hand on the lid, shot 2 opens on that same contact, and the hand may "
                "move away during shot 2. Never jump from lap to lid across the cut. A spoken warning does "
                "not by itself move a hand. Use one simple action and a locked camera where practical. "
                "Write the opening as one frozen instant, naming who is visible, their starting pose and "
                "the exact placement of the important object. Keep the actor and action space in frame. "
                "Put the movement in visual_action and describe one observable final pose or prop state. "
                "Avoid repeating character-sheet or wide-room descriptions inside scene directions."
            )
        reviewing = "You are the shot continuity editor" in prompt
        if reviewing:
            prompt += (
                "\nReport only defects viewers would notice in picture or sound. Recap wording and established "
                "states can already be visible at frame zero. An event narrated in this shot should occur in motion. "
                "A different crop, angle, or offscreen listener is valid when it does not change positions or states. "
                "Treat a line of dialogue as an utterance, not proof its speaker's claim is true. "
                "Do not repair a detail you describe as minor or acceptable. For each blocking issue quote the "
                "specific incompatible instructions or identify the missing story action. For a broken hand or prop "
                "handoff, repair both sides together when needed: the earlier action and ending, then the next opening. "
                "Read the repaired pair in order before returning it. An opening pose may remain still while "
                "the main event occurs in motion; the event need not already be under way at frame zero. "
                "Prefer removing a gratuitous limb, grip, expression change, or pose from the new framing "
                "over adding an extra action to explain it. Make object-only inserts object-only. "
                "A prior state need not be restated when cropped out. Ignore harmless omitted details and "
                "return no issue for a shot whose action and required states already agree. "
                "Before returning a repair, compare each changed field to that shot's current field. "
                "Do not submit a repair that repeats the current wording. An ordinary stationary item can "
                "be revealed by a new framing; do not require a pickup, movement or prior close-up for it "
                "unless the locked Hindi or a prior state actually requires that action. For example, "
                "striking a bangle against a stone already on the floor does not require anyone to pick "
                "up the stone. Never issue two repairs that require incompatible starting states. "
                "The explicit supernatural event is allowed to violate ordinary physics. Judge whether the "
                "picture shows it clearly, and apply normal geometry to the rest of the shot. "
                "Treat Plan to check as the only current storyboard. Before flagging anything, read the current "
                "field and verify the defect is still there. For an object that must be hidden, wording such as "
                "'rim is bare' or 'no bangle visible' establishes its absence, never its presence. "
                "Do not ask for a change that is already in the current draft."
            )
        cast_line = next((line for line in prompt.splitlines()
                          if line.startswith("Return both stages as a single JSON object. The exact cast identities are ")), None)
        locked_cast = json.loads(cast_line.split("identities are ", 1)[1]) if cast_line else None
        if locked_cast:
            prompt += "\nCopy each locked cast tag and name exactly, including its original spelling and script."
        validation_retry = "Previous response failed validation:" in prompt
        length_error = re.search(r"Speech has (\d+) words; expected (\d+)–(\d+)", prompt)
        if writing_story and validation_retry and length_error and self.last_story:
            words, low, high = map(int, length_error.groups())
            direction = "Shorten" if words > high else "Expand"
            previous = planning_writer.json_object(self.last_story)
            lengths = [len(beat["line"].split()) for beat in previous["speech"]]
            target = (low + high) // 2
            if words > high:
                # Give the model counts from Python instead of asking it to estimate Hindi word totals.
                limits = lengths.copy()
                while sum(limits) > target:
                    index = max(range(len(limits)), key=limits.__getitem__)
                    limits[index] -= 1
                extra = f"Maximum words for each line in order: {limits}. Do not expand any line."
            else:
                extra = "Add only details that can be shown in the same action."
            prompt = (
                f"{direction} this Hindi script to {low}–{high} whitespace-separated words total. "
                f"Python counted {lengths} words per line in the draft. Aim for {target} words total. {extra} "
                "Edit only the speech line strings; keep the entity, cast, speakers, beat count and actions. "
                "Count the words after editing. Return the complete corrected JSON object.\n"
                f"Draft: {self.last_story}"
            )
        # Extra review tokens cover both reasoning and the JSON repair.
        reasoning = reviewing and (self.simpler_draft or validation_retry)
        if reasoning:
            max_tokens += 3500
        digest = hashlib.sha256(f"{self.model}\n{max_tokens}\n{temperature}\n{prompt}".encode()).hexdigest()
        path = self.folder / f"clone-writing-{digest}.json"
        if path.is_file():
            content = store.read(path)["content"]
            if reviewing:
                self.record_review_issues(content)
            if writing_story:
                self.last_story = content
            return bind_locked_cast(content, locked_cast) if locked_cast else content
        floor = 19 if self.simpler_draft else 0
        if "Audit who is visible" in prompt:
            self.progress("Clone YouTube is checking the opening-frame cast", 22)
        elif reviewing:
            self.progress("Clone YouTube is reviewing and repairing shot continuity", max(18, floor))
        elif "Locked speech by shot" in prompt:
            self.progress("Clone YouTube is staging shots from the locked Hindi script", max(14, floor))
        elif "Return both stages" in prompt:
            self.progress("Clone YouTube is preparing character and background references", max(12, floor))
        for use_reasoning in (True, False) if reasoning else (False,):
            self.check()
            response = httpx.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": self.model, "response_format": {"type": "json_object"},
                      "max_tokens": max_tokens, **({} if use_reasoning else {"temperature": temperature}),
                      "thinking": {"type": "enabled" if use_reasoning else "disabled"},
                      "messages": [{"role": "system", "content": "Return only the requested JSON object."},
                                   {"role": "user", "content": prompt}]},
                timeout=150,
            )
            response.raise_for_status()
            result = response.json()
            choice = result["choices"][0]
            content = choice["message"].get("content")
            if isinstance(content, str) and content.strip() and choice.get("finish_reason") != "length":
                break
        else:
            # Let the original planner retry this validation failure within its bounded attempts.
            return ""
        if reviewing:
            self.record_review_issues(content)
        if writing_story:
            self.last_story = content
        self.check()
        store.save(path, {"content": content, "usage": result.get("usage"), "model": result.get("model")})
        return bind_locked_cast(content, locked_cast) if locked_cast else content

    def record_review_issues(self, content):
        try:
            issues = planning_writer.json_object(content).get("issues")
            if isinstance(issues, list) and all(isinstance(issue, str) for issue in issues):
                self.last_review_issues = issues
        except ValueError:
            pass


def project_for_renderer(source, seconds):
    writer = youtube_clone_writer
    cast = []
    for character in source["characters"]:
        look = f"{character['look_summary']} {character['voice_description']}"
        voice = "female" if re.search(r"\b(?:woman|female|girl|feminine)\b", look, re.I) else "male"
        cast.append(writer.Character(**character, voice=voice))
    draft = CloneChatDraft(
        title=source["idea"]["selected"]["title"], summary=source["script"]["monster"],
        entity=source["script"]["monster"], characters=cast, backgrounds=source["backgrounds"],
        scenes=[writer.Scene(**scene, end_pose=scene["ending_state"],
                             speech=scene["narration_text"] or scene["devanagari_dialogue"])
                for scene in source["scenes"]],
    )
    return writer.renderer_plan(source, draft, seconds)


def plan(folder, title, choose_topic, check, progress, *, seconds):
    final = folder / "raghu-plan-v2.json"
    if final.is_file():
        return store.read(final)
    output = folder / "clone-chat-handoff"
    if not output.exists():
        if choose_topic:
            title = raghu_services.choose_title(folder, check)
        progress("Clone YouTube is writing the Hindi script", 9)
        model = DeepSeekWriter(folder, check, progress)
        planning_writer.write_plan(title, seconds, output, model=model, short_editorial_margin=True)
    raw = (output / "pipeline.json").read_bytes()
    envelope = store.read(output / "railway-planning-export.json")
    if envelope != planning_writer.export_source(raw):
        raise ValueError("The saved Clone plan does not match its export.")
    source = json.loads(raw)
    result, preview = project_for_renderer(source, seconds)
    check()
    store.save(folder / "clone-source-plan.json", source)
    store.save(folder / "clone-renderer-preview.json", preview)
    store.save(final, result)
    progress(f"Clone YouTube script and prompts ready · {len(result['shots'])}/{len(result['shots'])} scenes written", 24)
    return result
