# Telegram horror video automation

Treat "onboarding" as a request to set up this checkout. Read [ONBOARDING.md](ONBOARDING.md). Ask whether to use Railway or localhost before making setup changes. Use the user’s answer to follow the matching steps. The approved music, previews, Web 6 narrator reference and intro/disclaimer are bundled in app/course_assets and work automatically on either host. Do not ask students to supply these files or choose a narrator voice. Ask for replacement paths only if the user requests custom assets. Ask the user to enter credentials in their local .env or Railway Variables. Keep credential values and student-supplied private replacements out of Git; the repository owner's explicitly approved course assets are bundled for distribution.

Both hosts run the same DeepSeek, Vast, FLUX, LTX, and Chatterbox workflow. Keep the approved single-opening-image motion setup and approved NVFP4 settings on RTX 5090. AI visual review is disabled. Run configuration checks before generation and file-readability checks before using saved media. Start a paid test only when requested.

Keep saved checkpoints and retry only failed assets. Preserve an unfinished production’s Vast disk on failure. Report unavailable services, invalid credentials, exhausted budgets, and failed quality checks accurately.

Run `python -m pytest -q` and `python -m compileall -q app` after code changes.
