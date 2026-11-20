# Silent video cost implementation plan

> **For agentic workers:** Use executing-plans inline. Tasks are tracked below and in the plan ledger.

**Goal:** Select rentals using compatible measured throughput and account for a complete silent video without changing approved artwork.

**Architecture:** Pure accounting helpers consume event logs and produce observations, estimates and rankings. The existing rental wrapper records compatible observations at cleanup and uses them in its existing offer screening. Generation workers are byte-for-byte unchanged.

**Tech Stack:** Python standard library, existing store/Vast integration, pytest.

**Spec:** ../specs/2026-11-21-video-cost-design.md

## Global Constraints

- Approved workers, prompts, models, precision, frames, steps and references remain unchanged.
- No rental, generation, deployment or live provider tests.
- Total target INR150–170 at INR96/USD is not a guarantee.
- Preserve checkpoints and failure cleanup behavior; no new budget abort.
- Distinguish observed warm speed from unknown estimates; include cold overhead and transfer fees.

## Review Focus

- Corrupt or absent benchmark files must not break rental cleanup.
- Different visual/runtime configuration or CPU allocation must not inherit a fast result.
- First-clip overhead and overlapping downloads must not be counted as warm speed or summed wall time.
- Failed or duplicate render events must not produce false qualification.
- Historical evidence must be fresh and have at least three warmed clips.

### Task 1: Pure accounting and observation model

**Files:** Create app/raghu_cost.py; test tests/test_raghu_cost.py.

**Interfaces:** estimate_cost(rate, clips, seconds_per_clip, overhead_seconds, download_gb, download_rate, upload_gb, upload_rate, other_usd=0) -> dict; summarize_events(events) -> dict; workload_key(manifest, worker_bytes, quantization, motion_model) -> str.

- [x] Write tests: 120 clips at 50 sec and $0.70/hour cost $1.166667 motion; overhead/transfers/other costs are included; non-finite/negative inputs fail. Download wall time uses union of intervals. Duplicate IDs or multiple model sessions are not valid qualification evidence.
- [x] Run python -m pytest -q tests/test_raghu_cost.py; expect missing module failure.
- [x] Implement helpers and validate numeric/event inputs conservatively.
- [x] Run focused tests; expect pass. Commit task files only.

### Task 2: Compatible historical host ranking

**Files:** Extend app/raghu_cost.py; test tests/test_raghu_cost.py.

**Interfaces:** host_signature(offer) -> str; measured_seconds(records, offer, key, now) -> float|None; offer_cost(offer, records, key, clips, overhead_seconds, download_gb, upload_gb, now) -> dict.

- [x] Write tests: higher-hourly fast host wins on total cost; unknown host keeps explicit 70-second estimate; mismatched CPU allocation/workload, stale (>7 days), insufficient warm samples (<3), malformed and nonfinite records do not qualify.
- [x] Run focused tests and observe failures for missing helpers.
- [x] Implement stable host fingerprint and conservative warm p90 plus separately recorded first-clip overhead.
- [x] Run focused tests; expect pass. Commit task files only.

### Task 3: Rental integration and failure-safe telemetry

**Files:** Modify app/raghu_vast.py; extend app/raghu_cost.py; create tests/test_raghu_cost_integration.py; document docs/raghu-railway.md.

**Interfaces:** record_rental_cost(folder, catalog_path, now) -> dict|None; quote loads records once and uses offer_cost without changing GPU/visual settings.

- [x] Write tests: finish_rental records valid events before existing cleanup; corrupt telemetry cannot prevent cleanup; runtime worker fingerprints change qualification; selection uses same estimate for sorting and admission.
- [x] Run integration tests; observe failure before wiring.
- [x] Persist allowlisted offer signature and cost estimate in rental journal; record event summary and actual elapsed rental estimate at cleanup, separately excluding unknown taxes/storage/writing/hosting. Do not log credentials or full environment.
- [x] Run focused tests, then full pytest suite in credential-free worktree with live HTTP blocked, and compileall. Record pre-existing failures rather than claiming full green.
- [x] Verify approved worker hashes unchanged, commit task files only, obtain one independent whole-change review, fix material findings with regression tests.

## Handoff

User selected inline execution. Do not deploy or benchmark paid GPUs. Explain that observed host records require future approved generation before reliable ranking is possible. Do not report the speed/price target achieved from offline checks.

Verified: 204 offline tests passed; compileall and frozen-file diff passed. Independent review fixes covered. No deployment or paid benchmark.
