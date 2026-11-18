# Raghu production on Railway or localhost

Deploy one Docker service with a persistent volume mounted at `/data`. Keep one replica when using SQLite. Allow at least 1 GB of persistent capacity for a ten-minute production with room to save sources and retakes. The Telegram bot accepts a story title, `/generate`, or “start the mission”. It delivers the finished video back to the requesting private chat. YouTube publishing is separate and disabled unless explicitly configured.

For localhost, install Python 3.11, FFmpeg/FFprobe, and OpenSSH. Run `python -m pip install -r requirements.txt vastai==1.9.1`, put the variables below in the ignored `.env` or `.env.telegram`, and run `python run.py`. Keep that process running while using Telegram. With the default SQLite setup, save and keep the local `data/` folder. The app uses Vast for image and motion generation. Use a separate Telegram bot token for each concurrently running installation.

## Service variables

Use the [Vast-backed environment template](../.env.raghu.example). Paid generation starts disabled in the template. Enable it after configuring your own credentials and checking the rental limits. Start with **Other → 30 seconds** for the first live test. The 10-minute option needs a host and budget that cover the full job.

```dotenv
MEDIA_BACKEND=raghu
TELEGRAM_PRODUCTION_MODE=live
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=
ALLOW_PAID_GENERATION=false
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-flash
RAGHU_PLANNER=clone
GEMINI_API_KEY=
GEMINI_TTS_MODEL=gemini-3.8-flash-tts
GEMINI_TTS_VOICE=Riko
RAGHU_NARRATION_PROVIDER=edge
VAST_API_KEY=
VAST_SSH_PRIVATE_KEY=
HF_TOKEN=
RAGHU_GPU=RTX_5090
RAGHU_MOTION_MODEL=ltx
RAGHU_LTX_END_FRAME=false
RAGHU_QUANTIZATION=fp8
RAGHU_TARGET_SECONDS=600
RAGHU_MAX_HOURLY_USD=0.77
RAGHU_MAX_JOB_USD=2.00
```

Use a dedicated SSH key and paste its full private-key text into Railway. The Hugging Face read token must have access to the pinned LTX-2.5 weights. Gemini is optional for Gemini narration. AI visual review is disabled. The Vast job estimate screens offers using the estimated rent and transfer cost. Production can continue past that estimate, and the final invoice can exceed it. Use a short target such as 30 seconds for a first end-to-end integration test.

## SSH from both installations

The app opens an outbound SSH connection to the rented Vast instance and uses SCP to copy files. Railway includes OpenSSH in the Docker image. Locally, check `ssh -V`, `ffmpeg -version`, and `vastai --version` in the terminal that runs the app. Add the dedicated public key to the Vast account. Locally set `VAST_SSH_KEY_PATH` to the private-key file. On Railway set `VAST_SSH_PRIVATE_KEY` to the full private key. The unattended worker requires a key that can authenticate in batch mode. A public inbound port on the local computer is unnecessary for the polling Telegram bot.

The worker tries both the direct endpoint and the SSH endpoint Vast reports when they differ. If a live rental is available, use the SSH host and port shown in Vast to test from the same environment that runs the bot. A refused port or a startup timeout can indicate a Vast host problem. An authentication error calls for checking the key and the Vast account.

With `RAGHU_PLANNER=clone`, DeepSeek uses the Clone YouTube automation repo's literal templates in the order hook, short Hindi story, character sheets, empty backgrounds, and scene prompts. The narration and occasional character lines are locked before the scene prompts. Each scene gets one visible beat, an opening pose and a camera instruction. Existing saved plans remain usable. FLUX.2 Klein creates the canonical and scene images. Production uses LTX 2.5 Distilled with FP8 casting and CPU offload at 1280×768, 121 frames and 24 fps. Each request contains one reviewed opening image, and the written prompt provides the ending. The job's `raghu-generation-plan.json` records the generation inputs. Dialogue in this LTX path is dubbed and has no native lip-sync guarantee.

The worker checks image readability and video dimensions, frame count and decoding. AI image, transition and motion judgments are disabled, and visual findings do not trigger retakes. Reports identify technical acceptance with `review_skipped=true` and `approval_basis`. Previously rejected visual assessments are superseded by file checks on resume. Keep the data directory or Railway volume attached so retries can reuse saved work and startup can reconcile the rental.

## Recover a job from Telegram

1. Run `/setup` to check required settings and tools without renting a GPU. No AI review test is required.
2. Use `/status` to see the last error and the available recovery buttons. **Retry this step** resumes the same job using saved scripts, images, and clips. Existing files receive technical checks before the GPU starts. **Start new video** requests a duration for a separate production.
3. If a new rental fails to establish SSH because its endpoint is unreachable, the worker verifies that contract has stopped before trying another eligible host within budget. If a previously working host cannot restart, retry when capacity returns. Check the existing contract in Vast before abandoning it. A stopped contract can still accrue disk charges.
4. Credentials, access or credit errors need a fix in the provider account or deployment settings. Resolve the error before retrying the same job. For an operation timeout, check which step stopped responding. For a budget error, check the offer estimate against the remaining work. If Telegram itself is offline, check the local process or Railway logs and the bot token, then use `/status` again.

The production worker makes no Gemini vision calls. Visual reviewer outages cannot pause generation. File corruption, provider failures, missing credentials and SSH problems can still require recovery. An unfinished rental keeps its disk when stopped.

After the approved source assets are copied, the service checks that the Vast rental is stopped or released. With `RAGHU_NARRATION_PROVIDER=edge`, it uses Madhur for Hindi narration and chooses the character voice from the script. Use `gemini` for the Gemini narrator. Chatterbox is optional and requires a usable narrator-only WAV on persistent storage, set with `RAGHU_NARRATOR_REFERENCE`. LTX opening-only scenes preserve five seconds of picture and fit their own narration. An original synthesized horror bed is ducked beneath the narrator. The 6.5-second opening includes the Kaali Kitaab intro and a Hindi fiction disclaimer. Editing scratch is on the container filesystem, and the full-quality export stays on the volume. Optional S3 storage enables a private full-quality link for Telegram files above its upload limit.

## Timing benchmark

From this repository, with Vast, Hugging Face, and the rental limit variables configured:

```bash
python -m tools.benchmark_raghu /path/to/render-plan.json /path/to/approved-stills /path/to/new-benchmark-dir
```

This reuses the exact stills, motion prompts, seeds and frame counts from Raghu. It records per-clip timings and a separate warm average. The renderer uses the configured quantization and defaults to CPU offload. Only use the `--offload none` comparison on a GPU with enough memory.

## One-image production checks

### Qwen image and silent-video test

Set `RAGHU_IMAGE_MODEL=qwen2511` to use Qwen-Image-Edit-2511 through a pinned ComfyUI worker. It downloads the 30.2 GB FP8 package with its text encoder and VAE, uses 40 editing steps, and saves 1536×864 scene images. Scene references contain the background and up to two characters. Character sheets start from a neutral canvas. Style-only pictures containing unrelated people are excluded from scene reference inputs.

Set `RAGHU_NARRATION_PROVIDER=none` for a silent test. It skips voice generation and its model download, ignores saved speech, and omits the intro and synthesized music. LTX then contributes one five-second moving clip per scene. Choose **Generate → Other → 30 seconds** for six clips. No AI visual reviewer is used.

Qwen inference has not yet been benchmarked on the rental. The worker is configured for the first GPU test, and the actual generation time, quality and bill must be measured on that test. Both Qwen and the current LTX files total about 101.3 GB on disk. They run sequentially on the GPU.

- `/raghu_image` rents Vast, generates one still, checks that its file is readable, verifies Vast teardown, then sends the original PNG to Telegram.
- `/raghu_motion` starts a separate rental, generates one new still and one five-second LTX shot, copies and verifies the saved files, and destroys the rental before Telegram delivery.
- These checks bypass DeepSeek and narration. They require Telegram, Vast and SSH settings. The motion check also needs the Hugging Face token.

## AI visual review is disabled

The retired `/image_review` and `/raghu_review_check` commands report that visual review is disabled. They make no reviewer requests and queue no jobs. Generated images proceed to animation after technical file checks. Character, pose, composition and motion defects can therefore appear in the final output.

### HunyuanVideo-1.5 silent motion test

Set `RAGHU_MOTION_MODEL=hunyuan15`, `RAGHU_IMAGE_MODEL=qwen2511`,
`RAGHU_NARRATION_PROVIDER=none`, and choose Generate → Other → 30 seconds.
This makes six single-opening-image clips (121 frames, 24 fps, 1280×720),
then trims and stitches a silent 30-second export. No ending image or AI visual
reviewer is used. The motion worker follows Comfy-Org's official Hunyuan 1.5
720p I2V template: FP16 diffusion weights, Euler/simple, 20 steps, CFG 6,
shift 7, with tiled VAE decoding and low-VRAM loading. This is the template's
20-step test preset, not a measured speed or quality guarantee.

Pinned manifests verify downloads by SHA256. The 9.38 GB Qwen text encoder
is shared between image and motion workers; combined distinct model weights
are approximately 50.6 GB. No LTX weights or HF token are required for this
public Hunyuan path. Setup and per-clip elapsed times are recorded in
`events.jsonl`. Hunyuan GPU speed and cost have not been benchmarked here.
