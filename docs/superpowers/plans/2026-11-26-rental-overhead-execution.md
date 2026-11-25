# SDD ledger — plan: docs/superpowers/plans/2026-11-26-rental-overhead.md
Ruling: Existing isolated codex branch is used; previous completed cost plan is not rerun.
Ruling: Execute measurement and cost ranking with no paid tests. Container publication is deferred
until measured setup evidence exists, to avoid speculative deployment changes.
Pre-flight: Task 1 worker events feed Task 2 setup estimate. Transfer cache is scoped to one Remote.
Task 1: complete — phase measurement tests observed RED then GREEN.
Task 2: complete — cache and measured ranking tests observed RED then GREEN.
Ruling: Unmeasured hosts use 77 seconds per clip, rounding the observed 76.6-second throughput,
with explicit estimate labeling. This is not a speed claim for other hosts.
Ruling: Snapshot events every six clips and each batch end, to limit monitoring transfer overhead.
Ruling: Prepared container remains deferred. No local logs can prove which package caused the
41-minute gap. The resulting instrumentation is required before selecting a prebuild strategy.
Final: fixed setup compatibility — test_setup_history_is_not_shared_across_narration_or_image_models RED→GREEN.
Final: fixed overlapping setup accounting — boundary-based timing, regression proves overlapping SCP is not added.
Final: fixed partial cold-start qualification — saved assets cannot supply measured complete setup, resumed
rentals cannot supply speed/setup observations. Covered by test_setup_boundary_avoids_overlap_and_rejects_partial_asset_runs.
Final: minor (deferred): shell timing test checks source wiring; Linux trap execution remains unverified locally.
Ruling: Use first render's wall-clock boundary rather than adding overlapping setup/transfer durations.
Ruling: Cacheless asset setup and narration/image recipe identity are required before measured setup is reused.
Task 3: complete — final whole suite 357 passed, compileall passed, diff whitespace check passed.
Final review: fresh reviewer /root/overhead_review; important findings fixed and covered.
Final: no paid run, rental mutation or deployment. Real cost/speed savings remain unverified.
