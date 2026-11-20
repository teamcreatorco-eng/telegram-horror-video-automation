# Planning reliability without visual review

Status: design approved. Local implementation in progress; testing stopped at the user's request. Not deployed.

Implementation boundary: scene-record validation, shared prompt compilation, targeted bounded correction,
prop-state preservation and record version/digest are implemented locally. Saved legacy plans remain
unchanged on resume. Cross-scene semantic agreement and the match between Hindi narration and English
directions remain model responsibilities; these are not proven by the structural validator. No image
or video test has been run for these changes. Do not describe this as verified or 99% reliable.

## Evidence and limits

The current check_shots validator accepts an empty cast alongside an opening description
of a man sitting on stairs and an action describing footprints on empty stairs. This was
reproduced locally without a model call. The checker validates fields, IDs and counts,
not agreement between the prose fields. plan_title calls it with editorial_checks=False.
The generated-image check, with visual review off, checks file readability only.

The approved one-minute run and current image worker share FLUX.2-klein-4B, the pinned
revision, four steps, guidance 1.0 and 1536x864 output. This does not isolate runtime,
references or actual production prompts. The failed run's exact planner output and
worker input manifests still need recovery before attributing its images to one layer.

The renderer currently rebuilds simple opening prompts from staging, camera and
reference roles. It does not send the entire original still prompt in this branch.
The adapter initializes props to an empty dictionary. Essential facts expressed only in
other prompt fields therefore have no structured preservation guarantee.

## Intended experience

Choose topic and duration, select a script if enabled, then receive the finished video.
Visual review remains disabled. Planning corrections happen before GPU rental and do
not create a manual approval step for ordinary repairable drafting problems.
Infrastructure outages and impossible plans must still be reported honestly.

## Approaches

1. More prose instructions: small change, but contradictions remain possible and hard
   to detect. Prior edits are unproven prompt changes, not a reliability guarantee.
2. AI reviewer with rejection and rewrite loops: flexible, but repeats the user's
   unwanted delays, variability and manual recovery experience.
3. Structured scene construction with deterministic compilation: recommended engineering
   choice. More initial work; exposes relationships that code can check and preserves
   them through both image and motion prompt construction. User has specified the
   outcome and no visual review; this implementation approach is proposed.

## One scene record

Store stable IDs for visible actors, referenced/offscreen actors, environment view,
camera side, subject, target, static surfaces and required props. An action includes
its category, performer (actor or environmental effect), target, direction, before
state and after state. Separate still composition from changes that occur over time.
Support object interaction, locomotion, reaction, environmental effect and establishing
shots. A moving mark is distinct from the stationary surface carrying it.

Identity and drawing style remain anchored to the approved assets. Derive references
from visible actor IDs only. Empty effect inserts receive no character references.
Carry explicit object and location state between adjacent shots with the same IDs.
Generate concise opening and motion prompts from this record; avoid independently
rewriting the same facts in multiple prose fields. Preserve these fields in the
handoff and renderer rather than recreating empty props dictionaries.

## Checks and automatic recovery

Before rental, check IDs, required objects, visible-cast/reference agreement, camera
side, legal state transitions and effect/surface separation. A schema cannot prove
that an AI correctly understood Hindi; narration-to-action alignment remains a model
task and must be evaluated separately.

Repair only invalid scene records and dependent adjacent transitions. Keep valid
scenes, approved speech and the story ending. Use a bounded number of correction calls
within the writing budget. For unresolved complex staging, attempt a simpler framing
of the SAME event that fits the existing beat and visible cast; revalidate it. Never
silently replace it with generic walking or an unrelated reaction. If no valid plan
is possible, report planning failure before renting; never label a bad plan successful.

Persist the original record, repair reason, repaired record, compiled prompts,
ordered reference paths/hashes, model configuration and generation input hashes.
Version the planner so saved plans are not silently treated as newly repaired plans.

## Validation before deployment

Begin with regression fixtures for the reproduced empty-cast contradiction, moving
footprints versus moving stairs, camera on the wrong doorway side, an offscreen person
added as visible cast, a cropped hand shot revealing an unseen face, and a valid
two-person interaction. Verify valid plans pass and invalid structures are locally
repaired without renting, losing speech or changing valid scenes.

Compare compiled inputs with approved examples. Evaluate multiple new titles and
report event alignment and cast correctness, not a subjective similarity score.
Then run a small controlled image comparison holding model, settings, references and
seed constant while changing prompt construction. Record both failures and successes.
Only after that run a small motion comparison from the resulting opening images.

99% good output is a measurement target, not an established result. Define shot-level
and whole-video success separately. Without observing generated pixels, planning
cannot detect every extra character, malformed hand or failure to follow an action.

## Scope

No model switch, automatic visual reviewer, production deployment or paid generation
is included in this design-only step. Implementation should be isolated and tested,
with an explicit rollout after the planner and controlled visual checks are complete.
