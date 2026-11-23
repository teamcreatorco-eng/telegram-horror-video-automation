# Flexible production story segmentation

At the user's request, the approved production writer no longer rejects a valid
script because its speech array differs from duration/5. Beat and word counts are
pacing targets. Keep all accepted spoken text and the ending, without padding or
truncation. Build scene records for the actual number of speech beats. Rendering
handoff and saved-plan loading accept that actual count too. Final duration follows
natural narration; requested duration is a writing target, not a fixed shot quota.

Keep non-empty structured JSON, valid asset references and readable media checks.
These remain necessary to run the pipeline. Do not add quality review, change image
or motion settings, start a GPU, or resume a paid production as part of this fix.
