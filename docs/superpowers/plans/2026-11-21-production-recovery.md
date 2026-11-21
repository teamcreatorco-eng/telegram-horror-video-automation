# Production recovery and local launcher implementation plan

> **For agentic workers:** Execute inline using executing-plans and test-driven-development. One independent whole-branch review at the end.

**Goal:** Fix the ten reviewed issues, use the latest approved planner for every duration, and let Windows localhost users restart with a desktop shortcut.

**Architecture:** Keep the existing Python controller, Telegram, Vast, FLUX, LTX and Chatterbox paths. Repair boundaries between components: planner selection, audio export, durable Telegram actions/shared artifacts, checkpoint validation and rental reconciliation. Add a small localhost launcher and shortcut installer, without adding local model generation or automatic paid jobs.

## Approved requirements and provenance

None of the ten findings was an explicitly requested behavior. Earlier user requests intentionally disabled AI review, preserved unfinished rental disks, kept approved visual settings, and preserved speech timing. Preserve those decisions. The old long-video planner and the incompatible audio format are implementation defects; they are not user preferences to retain.

- Use the latest compiled-scene Clone planner for all requested durations, including 600 seconds.
- Preserve selected scripts/checkpoints and repair only invalid or missing artifacts.
- Do not add AI visual or semantic review gates.
- Do not change approved image/video model settings, precision or artwork.
- Do not start paid generation or deploy during offline implementation.
- Windows launcher starts localhost after reboot, avoids duplicate local processes, writes private logs, and preserves the data directory. Restarting the server must not silently restart paid generation; unfinished jobs remain resumable from Telegram.

## Review focus

- Provider/network errors can occur after a durable side effect, not just before it.
- PostgreSQL web and worker processes can have separate filesystems and race on artifact updates.
- Locally missing media may already exist on a preserved remote disk.
- Readability checks must not silently trust truncated media or erase valid reference assets.
- Existing saved plans must remain usable; new ten-minute plans must not fall back to the older author.

### Task 1: One approved planner for all durations

**Files:** app/raghu_pipeline.py, app/clone_chat_planner.py, app/youtube_clone/pipeline/planning_writer.py; tests/test_unified_planner.py.

**Interface:** raghu_pipeline.work routes every new clone production to clone_chat_planner.plan; planning_writer.duration_limits accepts 30..600 seconds. Completed saved plans stay immutable.

- [x] Write regressions for 30, 120, 300, 600-second routing and a 120-scene no-review plan; observe failures.
- [x] Extend canonical duration support and remove duration-dependent planner routing. Preserve legacy selected speech when carrying unfinished work into the canonical planner.
- [x] Run focused planner tests; commit and record results.

### Task 2: Reliable narrated exports

**Files:** app/raghu_pipeline.py; tests/test_natural_narration.py.

- [x] Add failing synthetic narration+intro regression with music enabled and disabled, checking decode, 48 kHz stereo and expected duration.
- [x] Set compatible audio formats on joined and mixed story exports before intro concatenation; keep speech tempo and pauses unchanged.
- [x] Run narration/editor tests; commit.

### Task 3: Durable Telegram controls and shared script selection

**Files:** app/telegram_bot.py, app/script_options.py, app/store.py or app/storage.py; tests/test_telegram.py, tests/test_script_options.py.

**Interface:** acknowledgement failures do not prevent enqueue; expired callback acknowledgement is nonfatal. Script choices are durably stored on the shared job row, and options are read through storage.read_json when needed.

- [x] Reproduce expired callback polling, duration acknowledgement failure, and separate-filesystem script selection with offline fakes.
- [x] Persist/enqueue actions before best-effort acknowledgements; prevent permanent per-update errors from poisoning polling. Keep transient errors retryable and owner checks intact.
- [x] Publish and read shared options, make selection idempotent, hydrate selected state on the worker rather than trust stale local options.
- [x] Run Telegram, queue and option regressions; commit.

### Task 4: Recover remote work and close completed rentals

**Files:** app/raghu_vast.py, app/raghu_pipeline.py; tests/test_rental_recovery.py.

**Interface:** Remote recovery binds output to saved generation-input signatures. Reuse matching readable remote clips; remove only an explicitly changed generation or corrupt output. Refresh current HF credentials on resume. Reconcile preserved rentals even when local assets are complete.

- [x] Add failing tests for remote-clip reuse after failed download, stale-input invalidation, saved-success cleanup, failure preservation and refreshed credentials.
- [x] Add content-addressed remote result markers to the worker queue and retain original render parameters, rather than delete all outputs. Existing unmarked outputs are only recovered when an exact saved request proves provenance.
- [x] Refresh protected credentials on resume and invoke success cleanup for existing journals on local-complete fast paths.
- [x] Run rental/queue regression tests; commit.

### Task 5: Correct scene invalidation and corrupt media recovery

**Files:** app/pipeline.py, app/raghu_pipeline.py, app/raghu_narration.py; tests/test_asset_recovery.py.

- [x] Add failing tests for Raghu scene invalidation, truncated PNG and corrupt dialogue recovery.
- [x] Archive correct three-digit scene assets and dependent metadata. Explicit regeneration invalidates remote output provenance without regenerating unrelated scenes or changing canonical references.
- [x] Quarantine unreadable PNGs; invalidate dependent shots only when their image/reference changed. Re-synthesize only corrupt/missing dialogue, retaining valid files.
- [x] Run saved-asset, scene and narration regressions; commit.

### Task 6: Restartable localhost launcher and verification

**Files:** start-local.cmd, scripts/start-local.ps1, scripts/install-local-shortcut.ps1, app/local_launcher.py, ONBOARDING.md, README.md; tests/test_local_launcher.py.

- [x] Write offline tests for command/environment construction, existing healthy instance reuse, stale process state and private logging. No actual bot/GPU job start.
- [x] Add one-click launcher that uses configured project Python/venv, starts the server hidden, records PID/logs in ignored data, waits for health, then opens localhost. Report setup/startup failures clearly without displaying secrets. Add desktop-shortcut installer to localhost onboarding.
- [x] Run full python -m pytest -q with external sockets disabled (loopback allowed), python -m compileall -q app, diff/credential checks and Windows PowerShell parse validation.
- [x] Request one fresh whole-branch review; fix important findings with red/green tests; commit final verification.

## Execution ledger

Ruling: Existing linked worktree is already isolated; use codex/production-recovery-and-local-launcher. No additional checkout required.
Ruling: Prior approval to publish this automation persists, but no paid generation or new production deployment is part of this task.
Pre-flight: Tasks 1 and 3 share script choice state; Task 3's shared selection must be consumed by the canonical planner. Tasks 4 and 5 share output invalidation; regeneration must invalidate recovery signatures. Task 2 keeps Task 5's restored speech unchanged. Task 6 only starts the controller, never unpauses jobs.

## Verification and final review

Independent whole-branch review found one critical and six important issues: Windows destructive PID query, hardcoded six-scene command, renderer schema errors outside retry, prefetch acceptance race, missing decoder interpreted as corrupt output, truncated PCM accepted because of metadata size, and PNG structure accepted without pixel decode. Each has a failing offline regression and implementation correction. No paid render or live service credentials were used.
