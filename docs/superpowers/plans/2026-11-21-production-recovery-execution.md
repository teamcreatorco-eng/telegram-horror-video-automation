# SDD ledger — plan: docs/superpowers/plans/2026-11-21-production-recovery.md
Ruling: Reuse isolated worktree on codex/production-recovery-and-local-launcher; user authorized all ten fixes and desktop launcher, no paid run.
Ruling: Preserve AI review disabled and unfinished rental disks; none of the ten bugs was requested.
Pre-flight: Task3 shared selection consumed by Task1 canonical planner; Tasks4/5 remote provenance invalidation shared; Task6 restart never auto-resumes paid jobs.
Task 1: complete (commits 2726536..b66c54c, tests: uv --system-certs run --python 3.12 --with pytest --with-requirements requirements.txt python -m pytest -q tests/test_unified_planner.py tests/test_clone_chat_planner.py tests/test_script_options.py → 28 passed, 1 warning in 0.89s)
Task 2: complete (commits b66c54c..3750c17, tests: uv --system-certs run --python 3.12 --with pytest --with-requirements requirements.txt python -m pytest -q tests/test_natural_narration.py tests/test_obsidian_music.py → 13 passed, 1 warning in 5.13s)
Task 3: Ruling: Store script choice in shared DB, not mutable S3 JSON; existing selection assertion updated to DB authority — avoids stale worker overwriting selection.
Task 3: complete (commits 3750c17..4b2d5d0, tests: uv --system-certs run --python 3.12 --with pytest --with-requirements requirements.txt python -m pytest -q tests/test_telegram.py tests/test_script_options.py tests/test_unified_planner.py → 53 passed, 1 warning in 24.77s)
Task 4: complete (commits 4b2d5d0..6b64bf5, tests: uv --system-certs run --python 3.12 --with pytest --with-requirements requirements.txt python -m pytest -q tests/test_rental_recovery.py tests/test_vast_startup.py tests/test_raghu_cost_integration.py tests/test_without_visual_review.py → 45 passed, 1 warning in 1.05s)
Task 5: complete (commits 6b64bf5..1f6687e, tests: uv --system-certs run --python 3.12 --with pytest --with-requirements requirements.txt python -m pytest -q tests/test_asset_recovery.py tests/test_natural_narration.py tests/test_rental_recovery.py tests/test_without_visual_review.py tests/test_production.py → 31 passed, 1 warning in 6.05s)
Task 4: Ruling: Remote results reused only with matching completed-output and input evidence; unreadable or unverifiable output is regenerated — prevents stale clips passing as current.
Task 5: Ruling: Keep narration on visual-only scene regeneration because script/voice did not change — avoids resynthesizing valid continuous speech.
Task 6: Ruling: Update legacy tests to supply real opening artifacts required by recovery hashes; visual rewrite test explicitly enables its legacy review mode — production review remains disabled.
Task 6: Ruling: Provide desktop shortcut installer for onboarding, without installing or starting a second real bot during offline checks — prevents competing pollers and paid work.
Task 6: complete (commits 1f6687e..cffaf9b, tests: uv --system-certs run --python 3.12 --with pytest --with pytest-socket --with-requirements requirements.txt python -m pytest -q --disable-socket --allow-hosts=127.0.0.1,::1 → 244 passed, 1 warning in 34.91s)
Final review: independent gpt-6.2-astra whole-branch review; one Critical and six Important issues accepted.
Final: fixed Windows destructive PID query and recycled PID detection — test_process_check_is_read_only_and_does_not_use_kill and test_recycled_pid_is_not_mistaken_for_previous_controller RED→GREEN, suite255/255.
Final: fixed hardcoded six-scene Telegram limit — test_ten_minute_scene_command_can_regenerate_last_scene RED→GREEN, suite255/255.
Final: fixed renderer schema errors outside writing retry — test_renderer_contract_is_corrected_inside_scene_response_retry three cases RED→GREEN, suite255/255.
Final: fixed recovered MP4 mistaken for worker acceptance — test_recovered_first_clip_is_not_mistaken_for_worker_acceptance RED→GREEN, suite255/255.
Final: fixed decoder unavailability deleting paid work — test_decoder_unavailability_preserves_paid_remote_clip two cases RED→GREEN, suite255/255; provision FFprobe and refresh worker setup on resume.
Final: fixed truncated PCM accepted due metadata — test_truncated_wav_with_extra_metadata_is_not_accepted RED→GREEN, suite255/255.
Final: fixed corrupt PNG pixels accepted by structural verification — test_png_with_valid_structure_but_bad_pixels_is_not_accepted RED→GREEN, suite255/255.
Final: Ruling: Reviewer set aside ten-minute provider token ceiling and response time; no local evidence mandates batching, keep same approved planner and preserve API checkpoints/errors. Cost if wrong: a provider may reject or time out; needs an authorized live planning test.
Final: Ruling: Live capacity, model output quality, paid generation and deployment remain untested per user scope. Cost if wrong: offline results cannot guarantee live visual quality or the target price.
Final: minor (deferred): none. compileall, bash syntax, PowerShell parser and credential-pattern scan passed.
