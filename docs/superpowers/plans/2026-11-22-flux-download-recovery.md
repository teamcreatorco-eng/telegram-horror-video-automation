# FLUX download recovery implementation plan

**Goal:** Prevent the observed silent, prolonged image-model download wait on fresh clones without changing visual generation.

**Evidence:** The original worker forced anonymous downloads. A partially downloaded 7.75 GB transformer needed separate recovery. Rendering itself was fast. The original network failure was not logged, so no specific upstream cause is claimed.

**Design:** Download the same pinned FLUX revision using bounded HTTP transfers, authenticated when configured. Resume partial files, verify sizes and upstream content hashes, cache verification against file metadata, and retry only failed files. Read timeouts and a minimum-progress watchdog bound a stalled attempt. Publish atomic per-file progress for Telegram. Exhausted retries raise a sanitized error into the existing stop-and-preserve recovery path.

**Tasks:**
- [x] Write failing offline tests for authentication, interruption/resume, ignored ranges, corrupt files, stalled reads, bounded retries, and progress reporting.
- [x] Implement the worker downloader and wire it into FLUX, including fresh and resumed rental uploads.
- [x] Connect download status to existing Telegram progress without changing review or render settings.
- [x] Run targeted tests, the full pytest suite, compileall, and review the diff.

**Verification:** 20 downloader/transport regression cases pass; full suite 275 passed.
Compilation and diff whitespace checks pass. A local HTTP server test truncates a real
response and confirms range recovery with byte-identical output. Read-only public
Hugging Face checks confirm the pinned manifest (18 files, 15,980,131,745 bytes) and
verify the actual 446-byte model_index.json Git blob hash. No model weights or GPU
generation were used in verification. Independent review found missing credential
forwarding in fresh image-only/non-LTX runs; fixed and covered by four regression cases.

**Limits:** No paid GPU test or deployment is part of this verification. External outages remain possible; the fix must fail promptly and preserve data rather than promise zero failures.
