# Rental overhead execution
Spec: docs/superpowers/specs/2026-11-26-rental-overhead-design.md
Global constraints: approved visual and audio recipe frozen, no GPU rental, no hard spend cap.
## Task 1: Telemetry and recovery
Add offline regressions for worker phase timing and optional event snapshots.
Record phase duration/status, pull model events during clip collection, retain malformed-tail
handling without qualifying partial logs. Expected: focused tests pass.
## Task 2: Transfer reuse and ranking
Test unchanged reference re-upload caching and invalidation on restart.
Measure successful setup worker phases. Qualify setup only for one uninterrupted completed
session. Use setup measurements when present, retain labeled estimates otherwise.
Expected: tests distinguish exact render time, setup and transfer overhead.
## Task 3: Verify and review
Run python -m pytest -q and python -m compileall -q app.
Fresh whole-branch review of correctness, failure handling, privacy and recipe preservation.
