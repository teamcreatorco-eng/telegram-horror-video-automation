# Narrator-only speech and music choice

User-approved specification: all spoken text uses the established narrator, including
reported character dialogue (e.g. उसने कहा…). No character voice switches. The approved
preview's gain is 0.765 after -16 LUFS narration normalization. Music remains -24 LUFS
with the same gentle ducking. Do not change approved images or motion settings.

Telegram asks duration, then sends short video samples for installed tracks and asks
which track to use before queuing writing/generation. Tracks: Ghostly Piano Horror,
Midnight Graveyard Ambient, Shadows of Halloween Night and The Haunted Manor. Also
allow No music. Persist selection per job; user/chat ownership must remain isolated.
No installed tracks means raw narration, not a stalled onboarding or surprise music.

Keep private music/video assets and credentials out of Git. Install originals and
short approved previews on Railway's persistent volume and document local installation.
Use a fixed catalog; no arbitrary Telegram paths. Failed delivery can be retried with
/music while the choice is pending. Retain disabled visual reviewer/effects policy.

Root cause: narrator_line excludes character speaker tags and generates those lines
with Edge voices. Existing writing instructions allow dialogue. Fix both prompt policy
and voice routing; old saved plans still use the narrator without rewriting visuals.

Verify routing, gain, prompts, music choice ownership/idempotency, missing assets and
actual FFmpeg output. Run full pytest/compileall, independent review, then push and
deploy the existing Railway service. No GPU rental or paid generation test.
