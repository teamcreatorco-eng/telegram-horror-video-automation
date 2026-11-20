"""Prevent ambiguous opening compositions without adding review calls."""

SHOT_RULES = """
VISUAL COMPOSITION CONTRACT:
Write the opening, action and ending as a causal sequence using the same named actor,
target prop and location. The opening must visibly contain the prerequisites for the
literal narrated event. For placing a tile in a kiln, show that actor holding ONE tile
beside the accessible kiln opening; do not replace this with another person gripping
their wrist. For opening a door from inside, establish the interior side, closed door,
handle within reach and space for its hinge swing. An exterior establishing view cannot
stand in for that action. Never substitute a thematic illustration for its physical setup.
Use short concrete directions like the approved examples: camera; actor and one action;
visible ending. Background descriptions must not override this shot's crop or cast.
Distinguish the moving subject from the surface receiving an effect. Footprints moving
up stairs means wet footprint MARKS appear on stationary stair treads in sequence;
the stairs do not move and no person needs to climb them. For an unseen source, use
an empty environment insert with character_tags=[] and explicitly zero human limbs.
Keep observers off screen unless their reaction is the subject of this particular shot.
For progressive effects specify the opening state and order: first mark on the lowest
tread, next tread clean; action adds the next marks one tread at a time from bottom to
top; ending preserves earlier marks. Keep stair geometry fixed. Never illustrate a
sequential effect with a person sitting, crouching or standing on the target surface.
Do not put every stage of an action in one still. Generate the single opening instant;
put the ordered changes only in visual_action and ending_state. Continue the established
effect state into the next shot instead of resetting it or adding an unrelated observer.
Mentioned people, memories and offscreen voices do not become visible cast. When a man
speaks to another established man, preserve those identities; do not add a girl as a
generic listener. A location reference provides geometry, never permission to add cast.
In sound_design, start with one score tag [score:unease], [score:suspense], or [score:dread].
Add only audible scene effects as [effect:name@fraction], where fraction is 0 to 1 within
this shot and name is fire, rain, wind, footstep, knock, creak, latch, rustle, thud, sting,
bell or rattle. Example: [score:dread] [effect:fire@0] [effect:knock@0.6].
Use fire/rain/wind for ambience present in the scene; time impacts to the action. Do not
add an effect for an absent, extinguished or merely mentioned sound source.

Plan one visible action OR one emotional reaction per shot, approximately five seconds.
Budget separate action and reaction beats inside the requested duration; never append extra
shots beyond the requested count or truncate the story ending to make room.
A hand/prop insert stays a hand/prop insert throughout its motion. Put a face reaction in a
separate shot whose opening already shows that character's canonical face. Do not tilt,
zoom out, or turn a hidden/back-facing head to invent an unseen face within an insert.
For a facial reaction, start with a readable front or three-quarter face, then describe
specific small eye, eyebrow and mouth changes. Do not combine the hand action and face reveal.
List only physically visible characters in character_tags, once each. For each opening,
name each person's screen position, pose and relative height. Resolve 'same positions'
into explicit positions. Two people means two distinct established identities, not copies
of one person. Preserve age, hairstyle, outfit and relative stature across angles.
State whose left/right hand performs the action, its anatomical connection, target and
contact. For listening to a bangle, the owner's wrist is beside their own ear with nose
and mouth unobstructed; no other person's hand reaches across their face. Keep hands
separated and readable; avoid overlapping torsos and interlocked fingers unless essential.
Maintain floor contact and consistent perspective; never miniaturize an adult to fit the
frame. Choose a crop large enough for the action and keep required body parts visible.
Vary shot size and angle for a story reason: fixed inserts for object actions, readable
face close-ups for emotion, wider views for locomotion or two-person interactions.
Do not give every shot the same push-in. Camera movement is optional, motivated, and must
not reveal an unspecified person, unseen face, or new room. Keep the camera fixed for
precise hand contact. A cut to another view does not change character positions or props.
For each locked narration beat, translate the literal physical event into visual_action:
name the actor, action verb, target object, direction, contact and visible result. Do not
substitute generic walking, talking or smiling for the narrated action. Figurative narration
needs one concrete story-relevant visual, not a literal extra person. Show approach/contact/
release only when required by that action; do not add another action after its completion.
Preserve the same object size, rigid shape, material, count and owner through motion unless
the story explicitly changes them. Keep limbs attached and feet grounded; movement must
not exchange faces, outfits or body proportions. For breaking or transforming an object,
use a dedicated readable shot rather than combining transfer, removal, impact and reaction.
Keep scripted flames and their established state; do not extinguish fire unless the beat
asks for it. Match background fire to the narration and carry its state across cuts.
Choose different motivated shot sizes/angles across action, reaction and establishing beats,
not random orbiting within a clip. Maintain screen direction and the same room geography.

"""


def opening_contract(tags, identities):
    tags = list(dict.fromkeys(tags))
    if not tags:
        return "Empty composition: zero visible people, faces or human limbs. "
    clean = {tag: identities[tag].split(" Neutral full-body", 1)[0] for tag in tags}
    cast = "; ".join(f"{tag}: {clean[tag]}" for tag in tags)
    return (
        f"Visible cast: exactly {len(tags)} distinct people, one instance of each: {cast}. "
        "References identify those people; do not reproduce reference-sheet layouts or additional figures. "
        "Preserve each person's face, hair, outfit, age and relative height. Follow the stated screen positions. "
        "Every visible hand and arm belongs to its named character and connects anatomically to that body; "
        "keep hand contact and the target prop readable, without fused fingers or extra torsos. "
        "Use natural body proportions and consistent floor perspective, not miniature people. "
        "For a cropped insert show only the required body parts, not a detached floating limb. "
    )


def motion_contract(shot):
    """The actual inference brief: story context, physical action and stable geometry."""
    start = shot.get("start", {}).get("staging", "Use the supplied opening pose")
    end = shot.get("end", {}).get("staging", "Finish the requested action naturally")
    action = shot.get("visual_action") or shot.get("motion", "")
    camera = shot.get("camera") or "Hold the supplied framing; no automatic zoom or orbit"
    return (
        f"Camera instruction: {camera}. Starting pose: {start}. Physical action: {action}. Finish: {end}. "
        "Perform only the specified actor's action on the named target, in the stated direction. "
        "Show the required contact and release continuously; settle at the finishing pose without adding another action. "
        "Keep hands attached to their own arms, body proportions stable, feet grounded and object contact physical. "
        "Preserve object count, shape, size, material and ownership except for the explicitly scripted change. "
        "Preserve established fire unless extinguishing or ignition is requested. "
        "Keep hand/prop inserts within their opening crop; do not reveal an unseen face. "
        "If the ending asks to reveal a face absent from the opening, keep it hidden and finish the visible action. "
        "Maintain screen direction and room geometry; no unrequested zoom, orbit or angle switch. "
    )
