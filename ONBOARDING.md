# Onboarding

In Codex, say **“Clone this repository”** and paste its GitHub URL. After opening the checkout, say **“onboarding.”** The first question is **Railway or localhost?** Codex prepares the selected host, walks you through the credentials, and asks for the intro and disclaimer path.

## 1. Pick where the bot runs

- **Localhost.** Install Python 3.11, FFmpeg (including FFprobe) and the OpenSSH client. From the repo, run `python -m pip install -r requirements.txt vastai==1.9.1`. Copy `.env.raghu.example` to `.env`. Run `python run.py` after entering credentials and installing the assets. Keep that process and the computer running when you use Telegram. Keep the generated `data/` folder so jobs can resume.
- **Railway.** In Railway, choose **New Project → Deploy from GitHub Repo** and select your fork. Keep one replica and attach a persistent volume at `/data`. Copy the settings from `.env.raghu.example` into the service’s **Variables** tab. The Docker image installs Python, FFmpeg, OpenSSH and the Vast CLI. [Volumes](https://docs.railway.com/volumes)

Both hosts use the same writer, motion model, voice clone and editor. AI image and video reviewers are disabled. Generated files are checked for readability before use. The AI weights are downloaded to the rented Vast GPU host in both modes. Localhost installs the controller and editing tools on your computer.

### Windows desktop shortcut for localhost

During localhost onboarding, install dependencies in a project environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt vastai==1.9.1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-local-shortcut.ps1
```

After credentials and private assets are installed, double-click **Start Horror Automation**
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

## 3. Install the intro and shared narrator

- Provide the path to your **combined intro and disclaimer video** (at least six seconds). If they’re two files, tell Codex their order so it can join them first. The editor prepends the result to every story.
- Use the instructor-provided **`web-6-narrator-reference.wav`** on every installation. If it is missing, get it from the course materials before testing. The bot clones that voice to read each new Hindi script. Character dialogue uses its own voices.
- On **localhost**, Codex can install them with `python -m app.install_assets opening "PATH_TO_INTRO"` and `python -m app.install_assets narrator "PATH_TO_NARRATOR"`.
- On **Railway**, use the service’s SSH connection to stream each file to the same installer on its volume. In a shell with binary-safe stdin redirection, use `ssh -T SERVICE_SSH_DESTINATION "cd /app && python -m app.install_assets opening -" < intro.mp4` and the equivalent `narrator -` command for the audio. Use the destination and SSH key from your own service. On Windows, Codex can pass file bytes to the SSH subprocess directly. [Railway SSH](https://docs.railway.com/cli/ssh)

Voice cloning is enabled in the template. Keep the same course reference on localhost and Railway.

## 4. Check it from your phone

- Send `/setup`. Fix missing variables and tools first. AI visual review is disabled and no Gemini review test is required.
- When ready to spend on a first run, set `ALLOW_PAID_GENERATION=true`, restart the app, then tap **Generate → Other → 30 seconds**. The selected time is the full story. The opening video is added separately. Longer options give a complete story with a final payoff.
- If a run stops, send `/status` and select **Retry this step**. The bot uses saved work and shows the reason if it needs you to fix credentials, budget, or provider access. See the [recovery guide](docs/raghu-railway.md).
