# Bundled course media

The user explicitly replaces the previous private-media distribution requirement:
include the four approved music files and previews, approved narrator reference and
existing intro/disclaimer in Git, so fresh clones have them without questions.
Keep credentials and students' private replacement assets out of Git.

Read defaults directly from app/course_assets on both hosts. DATA_DIR-installed
custom overrides retain priority; an explicit invalid path remains a clear error.
Music track and matching preview must come from the same asset set. Include the
bundle in Docker. Update onboarding and AGENTS.md so students are asked only about
hosting and credentials, not default-media paths. No rendering-model changes.

Tests: empty DATA_DIR still has four choices and reference/opening; custom override
priority; fresh Telegram generation shows four previews before queuing; actual bundled
files decode and match manifest. Run full pytest and compileall. Push code and media
to the existing repository. Railway live deployment remains blocked by project access.
