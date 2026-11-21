# Natural narration and picture-led editing implementation plan

> **For agentic workers:** Use executing-plans inline, with test-driven-development.

**Goal:** Preserve the approved narrator's natural timing, cut picture to speech, and remove the production scene-record rejection gate.

**Architecture:** Generate narrator paragraphs independently of shot length in one resumable narration job. Use word timestamps to split the resulting speech at scene boundaries without changing its samples. Keep the complete narrator master and timing manifest. The editor trims picture to each speech span and holds the last video frame when speech exceeds available footage; it never speeds, trims, or pads spoken audio to a requested duration.

**Tech stack:** Existing Python/FFmpeg/Chatterbox; faster-whisper base on the rented worker CPU for word timestamps (additional model download, approximately 150 MB). No new visual model or paid test.

## Constraints and design decisions

- User explicitly authorizes implementation and GitHub push; no generation test now.
- Chatterbox's pinned generate method has a 1,000-token output limit. A ten-minute single inference would truncate. Use bounded sentence groups, not five-second shot synthesis, and retain all pauses within and between groups. No promise of identical reference emotion.
- Never reuse old per-scene narration as if it were a completed continuous narration job; new completion manifest gates new jobs. A validated continuous master can regenerate missing split files without synthesis.
- Do not change image generation, LTX settings, model precision, or visual prompt style.
- Remove production semantic scene validation/repair; retain readable file, valid JSON, declared asset identity, and credentials checks. These are executable input checks, not a visual reviewer.
- Requested duration becomes approximate when voice is enabled. Silent duration behavior stays unchanged.

## Tasks

1. Add a failing editor regression covering short and overlong speech, requested duration, and no audio tempo/silence removal. Implement video-only trim/hold and continuous final narration mix.
2. Add failing tests for paragraph grouping, timestamp alignment, continuous-job checkpoint selection and worker integration. Implement narration batching, CPU timestamp alignment, atomic manifests, and no per-scene trim.
3. Reproduce production rejection using a semantically flagged scene. Remove the semantic rejection/repair path while preserving prompt compilation, file checks and disabled visual review. Add fresh-planner regression coverage.
4. Run full pytest, compileall, diff/secret checks; review; commit and push to the existing GitHub repository. Do not deploy or generate unless requested.

## Review focus

- Long scripts cannot silently truncate at Chatterbox's generation limit.
- Missing ASR timestamps cannot silently map all scenes to zero or cut words.
- Resumed jobs must preserve valid saved audio and repair only missing output files.
- Dialogue voice turns remain separate; their original timing is also preserved.
- A long narration span must survive intact even when its picture is shorter.

## Execution ledger

- Root cause verified: edit() uses atempo, removes edge silence and forces five-second cadence. voice_clone.py seeds/generates/trims each shot independently. Production planning calls repair_records with semantic validate_record before rental.
- Editor regression observed red then green; short speech trims picture, long speech holds picture, no atempo/silenceremove or per-cut AAC encoding remains.
- Narration tests cover paragraph grouping, timestamp matching, legacy-cache migration, missing split restoration without a rental, full-script transfer and completion-last download.
- Independent review found corrupt-checkpoint retries, planner compilation outside JSON correction, and missing ending completeness. Added failing regressions and fixed each issue; timestamp cache is atomic and failed transcription preserves paragraph audio.
- No narration or visual generation has been run. Emotion, ASR reliability, final rendered visuals, speed and total cost remain unverified in production.
- Final offline verification: 217 tests passed (one existing pytest deprecation warning), compileall passed, diff check passed. Independent focused verification: 24 tests passed; all three important findings confirmed resolved. Credential-pattern scan of the new diff found no matches.
