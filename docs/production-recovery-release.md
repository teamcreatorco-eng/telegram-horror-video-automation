# Full-generation workflow and recovery fixes

The standard Telegram workflow runs through to a finished video. The example environment
explicitly sets `RAGHU_PLAN_ONLY=false`. A one-off planning-only test does not change the
default for new requests. Existing jobs retain their saved request and checkpoints; a
stopped test is not automatically resumed when the app or laptop restarts.

## Workflow shared by Railway and localhost

1. User supplies a title or asks the bot to choose one, then selects a duration.
2. The writer creates two complete script options. No GPU is rented before selection.
3. The latest approved Clone planner produces the selected story's reference assets and
   scene prompts. All supported durations, including ten minutes, use this same planner.
4. The rented GPU generates FLUX images and LTX motion using the approved opening-image,
   RTX 5090 NVFP4 configuration. Character sheets and empty backgrounds remain part of
   the image workflow. Approved sampling, resolution and motion settings are unchanged.
5. Chatterbox clones the installed course narrator reference. Long narration uses sentence
   groups because the model cannot produce ten minutes in one inference. Word timestamps
   align scene cuts; speech is not sped up, slowed down or trimmed to fit a five-second clip.
6. Source assets are collected and checked. The success cleanup follows
   `RAGHU_DESTROY_ON_SUCCESS`; unfinished failures preserve the rental disk.
7. Editing cuts picture to speech, holds the final picture frame if speech is longer,
   prepends the installed intro, and mixes background music and scene cues. Narrator
   breaths and pauses remain intact. Actual narrated duration can differ from the target.

## Corrected failure and recovery paths

- Story and intro audio share the same 48 kHz stereo format, preventing invalid concatenation.
- Expired Telegram button acknowledgements no longer block subsequent messages.
- Local jobs are enqueued before sending the queued confirmation, so a failed Telegram
  acknowledgement does not strand them.
- Script choices use shared database state; options can be read through shared storage
  when web and worker instances have separate filesystems.
- A retry recovers completed remote clips when generation inputs, output hashes and
  technical checks match. Unknown or invalid outputs are regenerated rather than accepted.
- Worker acceptance is acknowledged separately from an already-existing MP4, preventing
  recovered clips from causing unsafe prefetch ordering. Old queue requests are parked.
- Missing or temporarily unavailable FFprobe does not cause completed clips to be deleted.
  Motion setup provisions FFprobe; resumed workers receive the updated setup script.
- A locally completed production reconciles its previous rental, including configured
  destruction, instead of leaving a preserved disk billed indefinitely.
- Updated Hugging Face credentials are synchronized to a resumed rental.
- Scene regeneration invalidates the actual three-digit Raghu image/clip files and remote
  recovery identity. Unrelated scenes, canonical references and unchanged narration remain.
- Telegram scene controls use the saved plan's actual scene count, including scene 120.
- Corrupt saved images are quarantined; affected dependent visuals are invalidated.
  Both PNG structure and pixel decoding are checked. WAV checks read the declared PCM
  samples rather than trusting file size or metadata. Corrupt dialogue is regenerated.
- Renderer-required JSON types, asset IDs and speaker references are corrected inside
  the bounded writing-response loop. These checks do not add AI visual or semantic review.

## Windows restart

Complete [localhost onboarding](../ONBOARDING.md#windows-desktop-shortcut-for-localhost)
and run `scripts/install-local-shortcut.ps1` once. Then double-click **Start Horror
Automation** after reboot. It launches the controller in the background and opens the
local status page. A healthy existing controller is reused; process checks are read-only
and distinguish recycled Windows PIDs. Resume saved work from Telegram when ready.

The computer must remain on and connected for localhost operation. Keep the checkout,
`.env`, private references and `data/` directory. Do not run the same bot token on localhost
and Railway simultaneously. The launcher does not reinstall GPU models on the laptop.

## Credentials and private files

Use `.env.raghu.example` as a blank template. Credentials belong in an ignored local
`.env` or Railway Variables. Install the course narrator, intro/disclaimer and approved
music privately on each host. Real environment values, private recordings, generated
productions and runtime database state are not included in GitHub.

## Verification limits

The recovery release passed 255 offline tests, Python compilation, shell syntax and
PowerShell parsing checks. An independent whole-branch review's critical and important
findings were corrected with regressions. These checks do not prove live model quality,
voice likeness, provider availability, a guaranteed render time or a final dollar cost.
No paid generation benchmark was performed for this release. AI visual review stays disabled.
