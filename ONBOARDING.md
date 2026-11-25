# Onboarding

In Codex, say **“Clone this repository”** and paste its GitHub URL. After opening the checkout, say **“onboarding.”** The first question is **Railway or localhost?** Codex prepares the selected host and walks you through credentials. Music, previews, the narrator reference and intro/disclaimer are included; no media upload or file-path questions are needed.

## 1. Pick where the bot runs

- **Localhost.** Install Python 3.11, FFmpeg (including FFprobe) and the OpenSSH client. From the repo, run `python -m pip install -r requirements.txt vastai==1.9.1`. Copy `.env.raghu.example` to `.env`. Run `python run.py` after entering credentials. The default media is included. Keep that process and the computer running when you use Telegram. Keep the generated `data/` folder so jobs can resume.

- **Railway.** In Railway, choose **New Project → Deploy from GitHub Repo** and select your fork. Keep one replica and attach a persistent volume at `/data`. Copy the settings from `.env.raghu.example` into the service’s **Variables** tab. The Docker image installs Python, FFmpeg, OpenSSH and the Vast CLI. [Volumes](https://docs.railway.com/volumes)

Both hosts use the same writer, motion model, voice clone and editor. AI image and video reviewers are disabled. Generated files are checked for readability before use. The image, motion, and voice-generation weights are downloaded to the rented Vast GPU host in both modes. Localhost installs the controller and editing tools on your computer.

For cloned narration, Vast generates the paragraph recordings. After the clips and recordings are saved and rental cleanup finishes, the editing host aligns the Hindi speech on CPU. The first edit downloads the Whisper base model into `DATA_DIR/_narration-alignment`. Existing localhost installations should install the updated requirements before restarting the bot. A timing-only retry reuses the saved recordings and clips.

### Image-model downloads and recovery

The FLUX worker uses your `HF_TOKEN` privately on the rented host. It downloads the same
pinned approved model, verifies file sizes and content hashes, and resumes saved partial
files after an interrupted transfer. Telegram shows downloaded GB, verified file counts,
and retries during the image stage; the stage percentage alone is not download progress.
Transfers use a 20-second socket timeout and restart an attempt if it makes less than
1 MiB of progress for 60 seconds. Each failed file gets up to three attempts. If access
or networking still fails, the job reports the reason and uses the existing stop-and-preserve
recovery path. Fix the reported account/network issue and select **Retry this step** to reuse
verified files and partial downloads. Do not destroy the preserved instance if you want
to keep those downloads. Stopped-instance disk storage can still incur Vast charges.
External service failures remain possible; these checks do not guarantee a fixed setup time.

### Video decoding acceleration

The approved rented RTX 5090 runtime checks the optional NATTEN decoding kernel before
motion rendering. On Linux x86-64, Python 3.12, Torch 2.10.0 and CUDA 13.0, it can install
the matching official 185 MB binary with a pinned SHA-256 hash. This does not download
another model or upgrade Torch. Already-working installations are reused. Kernel validation
runs in a separate process; unavailable or incompatible acceleration keeps the existing
fallback and records that limitation. Model weights, NVFP4 precision, prompts, opening-image
conditioning, resolution, frames and sampling settings remain unchanged. Per-stage timings
are written to `results/events.jsonl` on the GPU. A 20-second clip-render target is not a
verified benchmark; cold setup and first-clip overhead must be counted separately.

### Raw audio and optional music

Automatic effects, rain, ambience, Obsidian and generated music are disabled. No
Gemini sound analysis runs. Saved cue tags and the old `RAGHU_SOUND_EFFECTS` flag
cannot add sound. Narration/dialogue and intro/disclaimer behavior are preserved.

For one user-supplied background track, place the file on the chosen host and set
`RAGHU_USER_BGM_FILE` to its readable path. Leave it blank for raw voice only.
Keep `RAGHU_BACKGROUND_AUDIO=true` when using the supplied track. It loops as
needed at a moderate level, gently lowered beneath speech. The legacy `BGM_FILE`
and default Obsidian path are ignored. No sound API key or extra GPU is needed.

Telegram's four choices are included under `app/course_assets/music`: Ghostly Piano
Horror, Midnight Graveyard Ambient, Shadows of Halloween Night and The Haunted Manor,
with their approved narrated previews. Both localhost and the Railway Docker image
read these files directly; no separate media upload, installer or API key is needed.
Only if you request replacements, use `python -m app.install_music PATH_TO_PACK`
to install custom tracks/previews in DATA_DIR. Those replacements stay out of Git.

After choosing a duration, Telegram sends the available previews and asks for one
track or **No music**. `/music` resends samples while this question is pending.
Selection is saved per production. **No music** keeps raw narration. All
narration—including reported character speech—uses the same narrator.
The approved mix applies narration gain 0.765 after normalization and -24 LUFS music
with gentle speech ducking; speech pacing is unchanged.

### Windows desktop shortcut for localhost

During localhost onboarding, install dependencies in a project environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt vastai==1.9.1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-local-shortcut.ps1
```

After credentials are configured, double-click **Start Horror Automation**
on your desktop (or `start-local.cmd` in this folder). It starts the controller in the background
and opens a local status page. Use Telegram for generation and `/status` or **Retry this step**
for saved jobs. After shutting down and restarting the laptop, double-click the same shortcut.
Keep the checkout and `data/` folder in place. The launcher reuses a running controller and
does not automatically resume paid jobs. Logs are in `data/local-host/server.log` (or your
configured `DATA_DIR/local-host/server.log`). A sleeping or switched-off laptop cannot run the bot.

## 2. Enter your own credentials

- **Telegram.** Open **BotFather**, send `/newbot`, and follow its prompts. Paste the token as `TELEGRAM_BOT_TOKEN`. After the service starts, send `/id` to your new bot in a private chat. Put the number it returns in `TELEGRAM_ALLOWED_USER_IDS` and restart or redeploy. Use one running deployment per bot token. [BotFather](https://t.me/BotFather)
- **DeepSeek.** Open **API Keys**, click **Create new API key**, and paste it as `DEEPSEEK_API_KEY`. Keep `DEEPSEEK_MODEL=deepseek-flash`. Ensure the account has API credit. [DeepSeek API Keys](https://platform.deepseek.com/api_keys)
- **Gemini, optional narration only.** The default Chatterbox voice clone does not require Gemini. If you choose `RAGHU_NARRATION_PROVIDER=gemini`, create an API key in Google AI Studio and paste it as `GEMINI_API_KEY`. [Google AI Studio API Keys](https://aistudio.google.com/app/apikey)
- **Vast.ai.** In **Manage Keys**, create an API key with access to list offers and create, start, stop and release instances. Paste it as `VAST_API_KEY`. Fund your Vast account and set the hourly rate and job estimate cap in the template. [Vast Manage Keys](https://cloud.vast.ai/manage-keys/)
- **Vast SSH.** Run `ssh-keygen -t ed25519 -f path/to/vast-worker` and use a dedicated key that can run in batch mode. Add the contents of `vast-worker.pub` in Vast’s SSH keys section. On localhost set `VAST_SSH_KEY_PATH` to the full path of the private key file. On Railway paste the full private-key text into `VAST_SSH_PRIVATE_KEY`. [Vast SSH setup](https://github.com/vast-ai/docs/blob/main/guides/get-started/quickstart.mdx)
- **Hugging Face.** Open the LTX model page, sign in and request access if prompted. Go to **Settings → Access Tokens → Create new token**, select read access for the model, and paste it as `HF_TOKEN`. [LTX-2.5 weights](https://huggingface.co/Lightricks/LTX-2.5) · [Hugging Face tokens](https://huggingface.co/settings/tokens)

Use the ignored `.env` locally or Railway Variables. When asking Codex for a setup check, say which step is done and keep the credential values in those settings.

## 3. Included intro and narrator

- `app/course_assets/opening.mp4` contains the approved combined intro/disclaimer.
  The editor prepends it automatically.
- `app/course_assets/narrator.wav` contains the approved Web 6 narrator reference.
  The same voice reads the whole script, including reported character dialogue.
- Leave `RAGHU_NARRATOR_REFERENCE` and `RAGHU_OPENING_PATH` blank for these defaults.
  Students do not need to find or upload them.
- Optional replacements: use `python -m app.install_assets opening "PATH_TO_INTRO"`
  or `python -m app.install_assets narrator "PATH_TO_NARRATOR"` on the chosen host,
  or set explicit paths. Existing installed replacements take priority over the bundle.

Voice cloning is enabled in the template. Keep the same course reference on localhost and Railway.

## 4. Check it from your phone

- Send `/setup`. Fix missing variables and tools first. AI visual review is disabled and no Gemini review test is required.
- When ready to spend on a first run, set `ALLOW_PAID_GENERATION=true`, restart the app, then tap **Generate → Other → 30 seconds**. The selected time is the full story. The opening video is added separately. Longer options give a complete story with a final payoff.
- If a run stops, send `/status` and select **Retry this step**. The bot uses saved work and shows the reason if it needs you to fix credentials, budget, or provider access. See the [recovery guide](docs/raghu-railway.md).

In Telegram, Generate offers **Generate from scratch** or **Use my script**. For your own story, paste it in one or more messages and send /done, then choose music. The formatted Hindi narration determines the estimated duration, and recorded speech determines the final edit timing. The writer formats the supplied story into Hindi narration and stages it directly, without script-option approval. Music choices arrive as short audio samples.


Before renting, production checks that its packaged worker scripts, model manifests, style references and narrator reference are available. Generated images and clips are checked when they arrive. A missing generation output can only be detected after its generation attempt.

Narration alignment runs on the editing CPU after rental cleanup. If transcription is unavailable or cannot match the Hindi script, editing uses approximate cuts near quiet pauses, guided by each line's word count. The complete recording and original speech pace are preserved. Paragraphs using this fallback are recorded in narration-timing.json as approximate_pause_timing. Exact scene-to-word matching is less certain for those paragraphs.
