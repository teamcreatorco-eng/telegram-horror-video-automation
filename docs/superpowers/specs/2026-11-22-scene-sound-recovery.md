# Scene sound recovery

The user requests automatic action-matched background effects and a corrected copy
of the latest accessible video. Keep the existing GPU stopped and preserve visuals,
motion settings, narration content and speech pacing.

## Root causes

The latest accessible local narrated library preview used edit_preview.py, which
mixed voice and Obsidian music but never rendered its saved sound cues. Production's
sound parser accepted only twelve names and ignored several planner outputs, including
radio_static, glass_break, drip and whisper. The editor coupled both effects and music
to one switch and ducked the combined music/effects bus, hiding impacts under narration.

## Correction

Resolve effects from saved cues and sound_design tags, with aliases and a larger
catalog. Recover readable legacy sound_design prose conservatively; never infer audio
from incidental narration words. Report unknown names instead of silently dropping them.
Bundle twelve small CC0 recorded sounds with provenance and integrity hashes; keep
synthesized fallbacks for other named effects or unavailable files. Do not add API calls,
provider credentials, new AI reviewers, or GPU jobs. Apply RMS-based effects gain with
soft peak limiting so quiet Foley survives beneath speech. Duck music only.

RAGHU_SOUND_EFFECTS independently controls scene effects and defaults true; the existing
RAGHU_BACKGROUND_AUDIO switch controls music. Resolve timing after speech-driven cuts.
Persist sound-effects-report.json with selected sources and story/final timestamps.
For the available narrated preview, mix only scene effects over its existing audio so
voice/music are not duplicated; copy video packets without re-encoding.

## Acceptance

Regression tests: alias recovery, prose/negative directions, no narration keyword guessing,
unknown cue reporting, actual cue onset timing, recorded-asset integrity and audibility,
FFmpeg sound-only export and byte-identical video stream. Run full pytest, compileall,
independent review and decode validation of the deliverable. Do not claim all possible
sounds or exact motion-event detection; cue timing follows the planner and final edit.

## Verification result

All 300 tests pass, including fifteen sound regression cases. Python compilation and
diff whitespace checks pass. Independent review's music-bus and negative-direction
findings were reproduced, fixed and covered by tests. The 113-second narrated library
preview was remixed on CPU with 29 timed effects (24 recorded), zero unresolved names,
successful complete decode, and byte-identical video-packet hash. Original voice/music
timing was retained; the AAC container duration differs by about 10 ms. The effect bus
measures approximately -32.3 dB RMS over the whole film, including its quiet gaps.
No GPU was started or modified. The latest radio-story export on the other computer
was not available locally; this demonstration uses the most recent accessible narrated
preview, as authorized by the user.
