# Narrator and music choice implementation plan

**Goal:** One narrator and approved audio balance with Telegram track choice.
**Spec:** ../specs/2026-11-23-narrator-and-music-choice.md
**Execution:** inline with a final independent code review.

- [x] Test and implement narrator-only writer policy and all-line clone routing.
- [x] Test and implement fixed private music catalog, per-job selection and gain .765.
- [x] Test and implement durable Telegram preview/selection before enqueue, including
  ownership, duplicate updates, no-music, and absent tracks.
- [x] Create four previews from separate narration and unchanged approved footage;
  install private originals/previews outside Git. Update onboarding.
- [ ] Run pytest/compileall, review and fix findings, commit/push, deploy existing
  Railway service and verify health/config without creating a production job.

## Verification and deployment
317 tests passed; compileall and Python 3.11 AST checks passed. Independent review
found and verified fixes for malformed-cast handling, irrelevant default music
validation and locked legacy-script migration. Focused review tests: 54 passed.
No GPU was started and no paid generation test was run. Four approved previews and
private tracks are installed locally and packaged outside Git.

Railway: saved account/project credentials were tested. The earlier account token
authenticates identity but lists no projects and cannot access the linked production
project; the saved public production URL returns Application not found. Deployment
and private asset installation require an account/token authorized for that project.
