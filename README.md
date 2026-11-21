# Telegram Horror Video Automation

Send a title or tap **Generate** in Telegram. Choose 2, 5, or 10 minutes, or enter a custom duration. The bot plans a complete Hindi story, creates illustrated scenes and motion on Vast, clones the narrator from your sample, and delivers the edited video in Telegram. AI image and video review is disabled. Saved media receives technical file checks.

## Get started

1. Clone this repository and open the folder in Codex.
2. Say **onboarding**. Choose **Railway** or **localhost**.
3. Follow the [step-by-step onboarding guide](ONBOARDING.md) and use the [environment template](.env.raghu.example). Install your intro and disclaimer, then install the shared Web 6 narrator file from the course materials.

Both hosts run the same production code and use a rented Vast GPU. Use `/setup` before your first run and `/status` to recover a paused job.

Normal Telegram requests use **full generation**, with `RAGHU_PLAN_ONLY=false`:
choose duration → choose one of two scripts → scene prompts and reference images →
GPU motion and narrator generation → verified asset collection → editing and delivery.
The normal workflow does not stop after planning. `ALLOW_PAID_GENERATION` stays `false`
in a fresh environment until onboarding is complete and the owner enables paid work.

[Included fixes, recovery behavior and verification limits](docs/production-recovery-release.md)

### Natural narrator timing

Chatterbox runs one resumable narration job for the complete selected story. Long stories
are synthesized in sentence groups because the pinned model cannot generate ten minutes
in one inference. Groups span scenes, use the same narrator conditioning, and preserve
their natural breaths and pauses. Dialogue remains a separate voice turn.

The voice worker automatically installs `faster-whisper==1.2.1` and downloads its multilingual
`base` model (approximately 150 MB) on the rented host CPU to find spoken word boundaries.
No additional API key is required. This adds setup and transcription time; the previous
narration cost projection does not include this new work.

Editing never speeds up or slows down speech. Picture is cut to spoken timing; if a line
outlasts its footage, the final picture frame is held so no spoken words are lost. The
requested duration guides the script; the final narrated film can differ from that duration.
Saved paragraph audio, a continuous narrator master, and word timestamps support retries.
There is no AI visual reviewer or scene-semantic rejection loop. Basic JSON, asset identity,
credential, audio-timing and file-readability errors are still reported accurately.

[Setup and recovery reference](docs/raghu-railway.md)

Windows localhost installations can create a **Start Horror Automation** desktop shortcut
with `scripts/install-local-shortcut.ps1` after setting up `.venv`. Double-click it after
a reboot to start the controller, then use Telegram to resume saved jobs. See
[localhost restart instructions](ONBOARDING.md#windows-desktop-shortcut-for-localhost).

All new Clone productions, from 30 seconds through ten minutes, use the same approved
compiled-scene planner. The ten-minute option no longer selects an older storyboard writer.


### Script choice and approved fast motion

New Telegram video requests write two complete Hindi script options after the duration
is selected. Telegram shows both scripts and **Use script 1 / Use script 2** buttons.
No shot prompts, images, or GPU rental start until a script is selected. The selected
script is reused verbatim as the input to the existing planner. Saved productions retain
their checkpoints. `/status` can show the options again.

For new rentals, set `RAGHU_GPU=RTX_5090` and `RAGHU_QUANTIZATION=nvfp4` to use the
approved compact LTX checkpoint. Image generation, 1280x768 output, frame count,
sampling passes, and opening-image motion conditioning are unchanged. The worker
installs and checks NVFP4 kernels, including on a previously prepared video environment.
Existing paused jobs retain the precision recorded in their rental journal. The historical
51–57 second timings are benchmark results, not a guarantee for every host or shot.
