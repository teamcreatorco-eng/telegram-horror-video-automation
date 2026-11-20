# Telegram Horror Video Automation

Send a title or tap **Generate** in Telegram. Choose 2, 5, or 10 minutes, or enter a custom duration. The bot plans a complete Hindi story, creates illustrated scenes and motion on Vast, clones the narrator from your sample, and delivers the edited video in Telegram. AI image and video review is disabled. Saved media receives technical file checks.

## Get started

1. Clone this repository and open the folder in Codex.
2. Say **onboarding**. Choose **Railway** or **localhost**.
3. Follow the [step-by-step onboarding guide](ONBOARDING.md) and use the [environment template](.env.raghu.example). Install your intro and disclaimer, then install the shared Web 6 narrator file from the course materials.

Both hosts run the same production code and use a rented Vast GPU. Use `/setup` before your first run and `/status` to recover a paused job.

[Setup and recovery reference](docs/raghu-railway.md)


### Script choice and approved fast motion

New Telegram video requests write two complete Hindi script options after the duration
is selected. Telegram shows both scripts and **Use script 1 / Use script 2** buttons.
No shot prompts, images, or GPU rental start until a script is selected. The selected
script is reused verbatim as the input to the existing planner. Saved productions and
planning-only requests retain their existing behavior. `/status` can show the options again.

For new rentals, set `RAGHU_GPU=RTX_5090` and `RAGHU_QUANTIZATION=nvfp4` to use the
approved compact LTX checkpoint. Image generation, 1280x768 output, frame count,
sampling passes, and opening-image motion conditioning are unchanged. The worker
installs and checks NVFP4 kernels, including on a previously prepared video environment.
Existing paused jobs retain the precision recorded in their rental journal. The historical
51–57 second timings are benchmark results, not a guarantee for every host or shot.
