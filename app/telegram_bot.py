"""Private-chat Telegram interface, durable job routing and resumable delivery.

Polling is single-leader; video rendering remains in the existing worker queue.
No raw Telegram payloads, API keys, or Bot API URLs are logged or persisted.
"""
import hashlib,json,logging,os,re,secrets,tempfile,threading,time,zipfile
import shutil
from pathlib import Path
import httpx
from . import auth,database,media,pipeline,storage,store,zernio,raghu_music
from .models import Request

STATE='not_configured'

HELP='Send a story title to create a six-act horror film. I will update a progress message and send the finished video here.\n\n/status — latest production\n/cancel — stop latest production\n/resume — continue saved work\n/scene 3 — regenerate scene 3 (paid in live mode)\n/video — send latest finished video again\n/script — script and publishing metadata\n/approve — approve the finished video for YouTube\n/reject — reject the finished video\n/regenerate — choose a scene to regenerate\n/keep — keep a rejected video without regenerating\n/id — your chat ID\n/help — show commands'
VIDEO_ONLY='This Telegram is only for generating video. Please give us your idea and we will create a video.'
CASUAL_MESSAGES={
    'hi','hello','hey','hey there','hello there','hi there','how are you',
    'hey how are you','hello how are you','good morning','good afternoon','good evening',
    'namaste','kaise ho','कैसे हो','नमस्ते','क्या हाल है',
}
READY_VIDEO_REQUEST='pick any topic and generate video'
READY_VIDEO_TOPIC='The Last Bus That Never Reached Home'

class BotError(Exception):
    def __init__(self,code=0,retry_after=10,uncertain=False):
        super().__init__(f'Telegram request failed (code {code})')
        self.code=code;self.retry_after=retry_after;self.uncertain=uncertain

class API:
    def __init__(self,token):
        logging.getLogger('httpx').setLevel(logging.WARNING)
        self.client=httpx.Client(base_url='https://api.telegram.org/bot'+token+'/',timeout=40)
    def call(self,method,fields=None,files=None):
        try:
            response=self.client.post(method,data=fields if files else None,json=None if files else fields,files=files,timeout=180 if files else 40)
            body=response.json()
        except (httpx.HTTPError,ValueError):raise BotError(uncertain=True) from None
        if not body.get('ok'):
            if method=='editMessageText' and 'message is not modified' in body.get('description',''):return True
            raise BotError(body.get('error_code',response.status_code),body.get('parameters',{}).get('retry_after',10))
        return body['result']
    def text(self,chat,text):return self.call('sendMessage',{'chat_id':chat,'text':text[:4000]})
    def close(self):self.client.close()

def init():
    with store.db() as c:
        if database.postgres():c.execute('SELECT pg_advisory_xact_lock(7128503)')
        c.execute('CREATE TABLE IF NOT EXISTS telegram_cursor (bot TEXT PRIMARY KEY, next_update BIGINT NOT NULL)')
        c.execute("CREATE TABLE IF NOT EXISTS telegram_duration_requests (bot TEXT, chat_id TEXT, user_id TEXT, "
                  "request TEXT NOT NULL, PRIMARY KEY(bot, chat_id, user_id))")
        c.execute("CREATE TABLE IF NOT EXISTS telegram_jobs (job_id TEXT PRIMARY KEY, bot TEXT NOT NULL, chat_id TEXT NOT NULL, user_id TEXT NOT NULL, message_id BIGINT, last_text TEXT, notified DOUBLE PRECISION DEFAULT 0, delivery TEXT DEFAULT 'pending', video_message BIGINT)")
        c.execute("CREATE TABLE IF NOT EXISTS telegram_reviews (job_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'awaiting_approval', prompt_message BIGINT, youtube_status TEXT NOT NULL DEFAULT 'not_requested', youtube_post_id TEXT, error TEXT)")
        c.execute('CREATE INDEX IF NOT EXISTS telegram_jobs_bot ON telegram_jobs(bot,delivery)')

def allowed(user,chat):
    allowed_ids={value.strip() for value in os.getenv('TELEGRAM_ALLOWED_USER_IDS','').split(',') if value.strip()}
    # A literal wildcard makes the public bot mode explicit. Groups and
    # channels are still rejected by Service.handle before this check.
    return str(chat)==str(user) and ('*' in allowed_ids or str(user) in allowed_ids)

def is_casual_message(text):
    normalized=re.sub(r'[^\w\s]', ' ', text.casefold(), flags=re.UNICODE)
    normalized=' '.join(normalized.split())
    return normalized in CASUAL_MESSAGES

def owner(bot,user):return 'tg_'+bot+'_'+str(user)

def ensure_user(name):
    with store.db() as c:c.execute('INSERT INTO users(username,password_hash,role) VALUES (?,?,?) ON CONFLICT(username) DO NOTHING',(name,'telegram-only','member'))

def latest(bot,chat,complete=False):
    with store.db() as c:
        rows=c.execute("SELECT j.id, j.request FROM jobs j JOIN telegram_jobs t ON t.job_id=j.id WHERE t.bot=? AND t.chat_id=? "+("AND j.status='complete' " if complete else '')+"ORDER BY CASE WHEN j.status IN ('running','queued') THEN 0 ELSE 1 END, j.created DESC",(bot,str(chat))).fetchall()
    raghu = os.getenv('MEDIA_BACKEND', '').lower() == 'raghu'
    row = next((r for r in rows if not raghu or json.loads(r[1])['title'].casefold() != 'remove the limit'), None)
    return store.get(row[0]) if row else None

def review(job_id):
    with store.db() as c:
        row=c.execute('SELECT * FROM telegram_reviews WHERE job_id=?',(job_id,)).fetchone()
    return dict(row) if row else None

def plan_only_job(job):
    return bool(job['request'].get('raghu_plan_only') or job['stage'].endswith('· planning only'))


def status_text(job,detailed=False):
    if job['status']=='queued' and job['stage'].startswith('Waiting to retry'):
        text=f"{job['request']['title']}\n{job['progress']}% · {job['stage']}. I will retry automatically when the backoff ends."
    elif job['status'] in ('queued','running') and os.getenv('MEDIA_BACKEND', '').lower() == 'raghu':
        text=f"{job['progress']}% · {job['stage']}"
    elif job['status'] in ('queued','running'):
        text='Generating your script.' if job['stage'] in ('Waiting for worker','Checking production tools') or job['stage'].startswith('Writing ') else 'Your script is completed, scenes are generated. Now generating your video.'
    else:text=f"{job['request']['title']}\n{job['progress']}% · {job['stage']}"
    if detailed and job['status'] in ('queued','running'):text=job['request']['title']+'\n'+text
    if job['status'] in ('failed','paused'):
        reason = job.get('error') or 'The worker did not record a reason.'
        text += '\nError: ' + reason[:2000]
        text += ('\n' + recovery_guidance(reason)
                 if recovery_job(job) else '\nUse /resume after the reported issue is resolved.')
    if job['status']=='cancelled':text+='\nStopped. Use /resume to continue.'
    if job['status']=='complete':
        if plan_only_job(job):
            text += '\nScript and storyboard passed planning. Use /script to download them.'
        else:
            text += '\nYour test is ready.' if job['request'].get('raghu_smoke') else '\nYour film is ready.'
    return text[:3900]

DURATION_CHOICES = {"2 minutes": 120, "5 minutes": 300, "10 minutes": 600, "Other": None}
DURATION_PROMPT = "How long should the story be? Choose 2 minutes, 5 minutes, 10 minutes, or Other. The intro is added separately."
CUSTOM_DURATION_PROMPT = "Send a duration from 30 seconds to 10 minutes, like 47 seconds or 3:20."


def recovery_job(job):
    return (os.getenv("MEDIA_BACKEND", "").lower() == "raghu"
            and not plan_only_job(job) and not job["request"].get("raghu_smoke")
            and job["status"] in ("failed", "paused"))


def recovery_markup(job):
    ident = job["id"]
    return {"inline_keyboard": [[
        {"text": "Retry this step", "callback_data": f"retry:{ident}"},
        {"text": "Start new video", "callback_data": f"new:{ident}"},
    ]]}


def recovery_guidance(reason):
    reason = reason.casefold()
    if "could not restart" in reason:
        return ("Vast could not start the saved machine. Wait for capacity, then tap Retry this step. "
                "Starting a new video uses a new rental and may repeat model downloads.")
    if "rental window expired" in reason:
        return ("The saved test window expired. The rental disk is still preserved. "
                "Ask the deployment owner to check the stopped instance.")
    if "operation reached its deadline" in reason:
        return ("A Vast step stopped responding in time. Saved assets are still available. "
                "Ask the deployment owner to check the failed step before retrying.")
    if "no vast offer" in reason or "no replacement host" in reason or "no different host" in reason:
        return ("No eligible GPU fits the current settings. Try again later, or ask the deployment owner "
                "to check the hardware, region and download-speed settings.")
    if "credit" in reason or "http 402" in reason:
        return ("Check the named provider's balance or billing. Tap Retry this step once it is funded.")
    if any(term in reason for term in ("ssh key", "host identity", "http 401", "http 403", "required.")):
        return ("The deployment owner should check the named credential or provider access, then tap Retry "
                "this step. Use /setup to check the local configuration. Keep credentials out of Telegram.")
    if "storage" in reason or "no space left" in reason:
        return ("Check the Railway volume or local data disk for free space, keeping the saved job files. "
                "Then tap Retry this step.")
    if "http 429" in reason or "temporarily unavailable" in reason or "could not reach" in reason:
        return ("The provider is busy or unreachable. Wait a few minutes and tap Retry this step.")
    if "could not validate clone" in reason:
        return ("The planner reached its retry limit for this draft. Retry this step gives the failed "
                "planning stage another bounded round and keeps validated checkpoints.")
    return ("Tap Retry this step to continue with the saved work, or Start new video to begin a fresh production.")


def production_setup_text():
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
                "VAST_API_KEY"]
    if os.getenv("RAGHU_NARRATION_PROVIDER", "gemini").lower() == "gemini":
        required.append("GEMINI_API_KEY")
    if os.getenv("RAGHU_MOTION_MODEL", "ltx") == "ltx":
        required.append("HF_TOKEN")
    missing = [key for key in required if not os.getenv(key, "").strip()]
    if not (os.getenv("VAST_SSH_PRIVATE_KEY") or os.getenv("VAST_SSH_KEY_PATH")):
        missing.append("VAST_SSH_PRIVATE_KEY or VAST_SSH_KEY_PATH")
    tools = [name for name in ("vastai", "ssh", "scp", "ssh-keygen") if not shutil.which(name)]
    if not media.available():
        tools.append("FFmpeg/FFprobe")
    from . import raghu_narration, raghu_pipeline
    assets = []
    if not raghu_narration.silent() and not raghu_pipeline.opening_path().is_file():
        assets.append("intro/disclaimer video")
    if raghu_narration.uses_clone():
        try:
            raghu_narration.clone_reference()
        except RuntimeError:
            assets.append("6–30-second narrator reference")
    if missing or tools or assets:
        return "\n".join(["Setup needs attention."]
                         + (["Missing variables: " + ", ".join(missing)] if missing else [])
                         + (["Missing tools: " + ", ".join(tools)] if tools else [])
                         + (["Media to install: " + ", ".join(assets)] if assets else []))
    return ("Required settings and tools are present. Provider access and GPU availability are checked "
            "during production. This setup check did not rent a GPU.")


def video_duration(text):
    value = text.strip().casefold()
    if value in DURATION_CHOICES:
        return DURATION_CHOICES[value]
    clock = re.fullmatch(r"(\d{1,2}):([0-5]\d)", value)
    if clock:
        seconds = 60 * int(clock[1]) + int(clock[2])
        return seconds if 30 <= seconds <= 600 else None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?|m|min|minutes?)", value)
    if not match:
        return None
    seconds = float(match[1]) * (60 if re.search(r"m(?:in(?:utes?)?)?$", value) else 1)
    rounded = round(seconds)
    return rounded if 30 <= rounded <= 600 and abs(seconds - rounded) < 1e-8 else None


def scene_count(job):
    for name in ('raghu-plan-v2.json','story.json'):
        plan=storage.read_json(job['id'],name)
        if isinstance(plan,dict) and isinstance(plan.get('shots'),list):
            return len(plan['shots'])
    return 6


class Service:
    def __init__(self,api,bot):self.api=api;self.bot=str(bot)
    def handle(self,update):
        if "callback_query" in update:
            return self.recover(update)
        message=update.get('message',{})
        chat=message.get('chat',{});user=message.get('from',{})
        if chat.get('type')!='private' or user.get('is_bot') or not isinstance(message.get('text'),str):return
        cid=chat['id'];uid=user['id'];text=message['text'].strip()
        command=text.split(maxsplit=1)[0].split('@')[0].lower() if text else ''
        if command == '/id' and str(cid) == str(uid):
            self.api.text(cid, f"Your Telegram user ID is {uid}.")
            return
        if not allowed(uid,cid):return
        raghu = os.getenv('MEDIA_BACKEND', '').lower() == 'raghu'
        if command == '/start' and raghu:
            self.api.call('sendMessage', {'chat_id': cid, 'text': 'Choose how to create your video, or send a story title.',
                          'reply_markup': {'keyboard': [[{'text': 'Generate from scratch'}, {'text': 'Use my script'}]], 'resize_keyboard': True}})
            return
        if command in ('/start','/id'):
            self.api.text(cid,HELP)
            return
        if raghu and text.casefold() in ('generate', '/generate'):
            self.api.call('sendMessage', {'chat_id': cid, 'text': 'How would you like to create this video?',
                          'reply_markup': {'keyboard': [[{'text': 'Generate from scratch'}, {'text': 'Use my script'}]],
                                           'resize_keyboard': True}})
            return
        scratch_selected = raghu and text.casefold() == 'generate from scratch'
        if scratch_selected:
            text = command = '/generate'
        draft_key = (self.bot, str(cid), str(uid))
        pending = None
        duration = None
        if raghu:
            if scratch_selected:
                with store.db() as c:
                    c.execute('DELETE FROM telegram_duration_requests WHERE bot=? AND chat_id=? AND user_id=?', draft_key)
            with store.db() as c:
                draft = c.execute("SELECT request FROM telegram_duration_requests "
                                  "WHERE bot=? AND chat_id=? AND user_id=?", draft_key).fetchone()
            if draft:
                pending = json.loads(draft[0])
            if text.casefold() == 'use my script':
                with store.db() as c:
                    c.execute("INSERT INTO telegram_duration_requests(bot,chat_id,user_id,request) VALUES (?,?,?,?) ON CONFLICT(bot,chat_id,user_id) DO UPDATE SET request=excluded.request",
                              (*draft_key, json.dumps({'awaiting_script_input': True})))
                self.api.text(cid, 'Paste your script here. You can send it in several messages. Send /done when you have finished, or /cancel to cancel.')
                return
            if pending and pending.get('awaiting_script_input') and text.casefold() != '/cancel':
                if command == '/done':
                    script = pending.get('supplied_script', '').strip()
                    if not script:
                        self.api.text(cid, 'Paste your script first, then send /done.')
                        return
                    request = Request(title='Your supplied story', language=os.getenv('TELEGRAM_LANGUAGE','Hindi'),
                                      mode=os.getenv('TELEGRAM_PRODUCTION_MODE','live')).model_dump()
                    request['supplied_script'] = script
                    with store.db() as c:
                        c.execute("UPDATE telegram_duration_requests SET request=? WHERE bot=? AND chat_id=? AND user_id=?",
                                  (json.dumps(request, ensure_ascii=False), *draft_key))
                    self.ask_duration(cid)
                    return
                if command.startswith('/'):
                    self.api.text(cid, 'Paste the script, send /done to continue, or /cancel.')
                    return
                pending['supplied_script'] = (pending.get('supplied_script', '') + '\n' + text).strip()
                with store.db() as c:
                    c.execute("UPDATE telegram_duration_requests SET request=? WHERE bot=? AND chat_id=? AND user_id=?",
                              (json.dumps(pending, ensure_ascii=False), *draft_key))
                self.api.text(cid, 'Script saved. Send more text or /done to continue.')
                return
            if draft:
                if text.casefold() == "/cancel":
                    with store.db() as c:
                        c.execute("DELETE FROM telegram_duration_requests WHERE bot=? AND chat_id=? AND user_id=?", draft_key)
                    self.api.text(cid, "Video setup cancelled.")
                    return
                if pending.get('awaiting_music') and command == '/music':
                    self.ask_music(cid)
                    return
                if pending.get('awaiting_music') and not command.startswith('/'):
                    choice = next((c['id'] for c in raghu_music.choices()
                                   if text.casefold() in (c['title'].casefold(), c['id'])), None)
                    if text.casefold() == 'no music':
                        choice = 'none'
                    if choice is None:
                        self.api.text(cid, 'Choose a music title below, No music, or /music to hear the samples again.')
                        return
                    pending = {k:v for k,v in pending.items() if k != 'awaiting_music'}
                    pending['music_track'] = choice
                    duration = pending['duration_seconds']
                    text = pending['title']
                elif not command.startswith('/'):
                    if text.casefold() == "other":
                        self.api.call("sendMessage", {"chat_id":cid, "text":CUSTOM_DURATION_PROMPT,
                                      "reply_markup":{"remove_keyboard":True}})
                        return
                    duration = video_duration(text)
                    if duration is None:
                        self.api.text(cid, CUSTOM_DURATION_PROMPT)
                        return
                    text = pending['title']
            if not pending and not command.startswith('/') and video_duration(text) is not None:
                self.api.text(cid, "Send a story title or /generate first.")
                return
        topic_request = ' '.join(re.findall(r"[\w']+", text.casefold()))
        if raghu and topic_request in ('remove the limit', 'remove the budget limit', 'remove the spending limit'):
            self.api.text(cid, 'Vast rentals are ranked by estimated cost. There is no hourly or per-video spending cap.')
            return
        choose_topic = raghu and (
            (command == '/generate' and len(text.split(maxsplit=1)) == 1)
            or (not command.startswith('/') and re.fullmatch(
                r"(?:please )?(?:run this automation|start the mission|"
                r"(?:pick|choose)(?: any| a| an| your own)? (?:horror )?topic"
                r"(?: yourself| for me| and (?:generate|make|start generating) (?:a )?video)?|"
                r"(?:generate|make|create) (?:any (?:type|kind) of|any|a) (?:horror )?video)(?: please)?",
                topic_request) is not None))
        if pending and duration is not None:
            choose_topic = pending.get('choose_topic', False)
        if choose_topic:
            text = 'Choose an original Hindi folk-horror topic'
            command = ''
        elif raghu and command == '/generate':
            text = text.split(maxsplit=1)[1].strip()
            command = ''
        elif not raghu and text.casefold() == READY_VIDEO_REQUEST:
            text = READY_VIDEO_TOPIC
            self.api.text(cid, f"Okay, I'm going with this topic: {text}.\n\nGenerating your script.")
        if not command.startswith('/') and is_casual_message(text):
            self.api.text(cid,VIDEO_ONLY)
            return
        name=owner(self.bot,uid);ensure_user(name)
        if command=='/help':
            self.api.text(cid, HELP + ('\n/setup — check deployment settings\n/images — download saved images and review reports\n/image_review still-001.png — view an image and its verdict' if raghu else ''))
            return
        if raghu and command == '/setup':
            self.api.text(cid, production_setup_text())
            return
        if raghu and command == '/images':
            requested_id = text.partition(' ')[2].strip()
            if requested_id:
                with store.db() as c:
                    row = c.execute("SELECT job_id FROM telegram_jobs WHERE bot=? AND chat_id=? AND job_id=?",
                                    (self.bot, str(cid), requested_id)).fetchone()
                job = store.get(row[0]) if row else None
            else:
                job = latest(self.bot, cid)
            if not job:
                self.api.text(cid, 'No matching production found.')
                return
            try:
                self.send_images(cid, job)
            except BotError:
                raise
            except Exception:
                logging.exception("Could not read saved images for job %s", job['id'])
                self.api.text(cid, 'Could not read the saved image archive. Check storage availability and retry /images.')
            return
        if raghu and command in ('/image_review', '/raghu_review_check'):
            self.api.text(cid, 'AI image and video reviews are disabled. Production continues after file checks.')
            return
        smoke_mode = {'/raghu_image': 'image', '/raghu_motion': 'motion'}.get(command) if raghu else None
        planning_only = raghu and os.getenv('RAGHU_PLAN_ONLY', '').lower() == 'true'
        if planning_only and smoke_mode in ('image', 'motion'):
            self.api.text(cid, 'Planning-only mode is active. GPU generation is disabled.')
            return
        if smoke_mode:
            text = 'Raghu Railway ' + smoke_mode + ' smoke test'
            command = ''
        if command.startswith('/'):
            job=latest(self.bot,cid,command in ('/video','/approve','/reject','/regenerate','/keep'))
            if command in ('/status','/resume','/cancel') and (requested_id := text.partition(' ')[2].strip()):
                with store.db() as c:
                    row = c.execute("SELECT job_id FROM telegram_jobs WHERE bot=? AND chat_id=? AND job_id=?",
                                    (self.bot, str(cid), requested_id)).fetchone()
                job = store.get(row[0]) if row else None
            if command not in ('/status','/cancel','/resume','/video','/script','/scene','/approve','/reject','/regenerate','/keep'):self.api.text(cid,HELP);return
            if not job:self.api.text(cid,'No matching production yet. Send a story title.');return
            if command in ('/approve','/reject','/regenerate','/keep'):
                if job['status']!='complete':self.api.text(cid,'The latest video is not ready for review yet.');return
                current=review(job['id'])
                if not current:self.api.text(cid,'This finished video has not been delivered for review yet.');return
                if job['request'].get('raghu_smoke'):
                    self.api.text(cid,'This smoke test is for production verification.')
                    return
                if command=='/approve':
                    if current['state']=='published':self.api.text(cid,'This video has already been published to YouTube.');return
                    with store.db() as c:c.execute("UPDATE telegram_reviews SET state='approved',error=NULL WHERE job_id=?",(job['id'],))
                    if not zernio.publishing_enabled():self.api.text(cid,'Approved. YouTube publishing is disabled, so nothing has been posted.');return
                    self.publish_approved_video(job,cid);return
                if command=='/reject':
                    with store.db() as c:c.execute("UPDATE telegram_reviews SET state='rejected' WHERE job_id=?",(job['id'],))
                    self.api.text(cid,'Not approved. Should I regenerate the video? Reply /regenerate to choose a scene, or /keep to keep this version.');return
                if command=='/regenerate':
                    if current['state']!='rejected':self.api.text(cid,'Reject the video first with /reject, then choose what to regenerate.');return
                    self.api.text(cid,f'Choose the scene to regenerate: /scene 1 through /scene {scene_count(job)}. Live regeneration uses paid APIs.');return
                if command=='/keep':
                    with store.db() as c:c.execute("UPDATE telegram_reviews SET state='kept' WHERE job_id=?",(job['id'],))
                    self.api.text(cid,'Kept this version. It will not be published unless you approve it later.');return
            if command=='/status':
                self.send_status(cid, job, detailed=True)
                return
            if command=='/scene':
                number=text.partition(' ')[2].strip()
                maximum=scene_count(job)
                if number not in {str(n) for n in range(1,maximum+1)}:self.api.text(cid,f'Use /scene followed by a number from 1 to {maximum}. Live regeneration uses paid APIs.');return
                try:pipeline.regenerate(job['id'],int(number))
                except ValueError as exc:self.api.text(cid,str(exc));return
                with store.db() as c:c.execute("UPDATE telegram_jobs SET delivery='pending',last_text=NULL WHERE job_id=?",(job['id'],))
                with store.db() as c:c.execute("UPDATE telegram_reviews SET state='awaiting_approval',prompt_message=NULL,youtube_status='not_requested',youtube_post_id=NULL,error=NULL WHERE job_id=?",(job['id'],))
                self.api.text(cid,'Scene regeneration queued. I will send the updated film when ready.');return
            if command=='/cancel':
                with store.db() as c:c.execute("UPDATE jobs SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=? AND status IN ('running','queued')",(job['id'],))
                self.api.text(cid,'Stop requested. Saved work is retained.');return
            if command=='/resume':
                if job['status']=='complete' and plan_only_job(job):
                    self.api.text(cid,'The script and storyboard are ready. Use /script to download them, or send a new title to start another plan.');return
                if job['status'] not in ('failed','paused','cancelled'):self.api.text(cid,'This production is already active or complete.');return
                resume_planning = (job['error'] or '').startswith(pipeline.PLANNING_BUDGET_ERROR)
                if recovery_job(job) and not resume_planning:
                    folder = store.folder(job['id'])
                    folder.mkdir(parents=True, exist_ok=True)
                    store.save(folder / "raghu-user-retry.json", {"round": secrets.token_hex(12)})
                try:
                    queued = (pipeline.enqueue(job['id'], resume_planning=True) if resume_planning
                              else pipeline.enqueue(job['id']))
                except ValueError as exc:
                    self.api.text(cid,str(exc));return
                if queued is False:
                    self.api.text(cid,'This production is already queued or active.');return
                with store.db() as c:c.execute("UPDATE telegram_jobs SET delivery='pending',last_text=NULL WHERE job_id=?",(job['id'],))
                self.api.text(cid,'Resuming the saved production.');return
            if command=='/video':
                if plan_only_job(job):
                    self.api.text(cid, 'This job contains a script and storyboard. Use /script to download them.')
                    return
                with store.db() as c:c.execute("UPDATE telegram_jobs SET delivery='pending' WHERE job_id=?",(job['id'],))
                self.api.text(cid,'I will send the finished video again.');return
            if command=='/script':
                if plan_only_job(job):
                    if job['status'] != 'complete':
                        self.api.text(cid, 'The storyboard has not passed planning yet. Use /status for progress.')
                    else:
                        package = storage.read_json(job['id'], 'story.json')
                        self.api.call('sendDocument', {'chat_id': cid}, files={'document': (
                            'script-and-storyboard.json', json.dumps(package, ensure_ascii=False, indent=2).encode(),
                            'application/json')})
                    return
                if job['request'].get('raghu_produce_saved'):
                    package = storage.read_json(job['id'], 'story.json')
                    self.api.call('sendDocument', {'chat_id': cid}, files={'document': (
                        'script-and-storyboard.json', json.dumps(package, ensure_ascii=False, indent=2).encode(),
                        'application/json')})
                    return
                package={k:storage.read_json(job['id'],k+'.json') for k in ('story','publishing')}
                self.api.call('sendDocument',{'chat_id':cid},files={'document':('script-and-publishing.json',json.dumps(package,ensure_ascii=False,indent=2).encode(),'application/json')});return
        try:request=Request(title=text,language=os.getenv('TELEGRAM_LANGUAGE','Hindi'),mode=os.getenv('TELEGRAM_PRODUCTION_MODE','live')).model_dump()
        except ValueError:self.api.text(cid,'Send a title between 2 and 120 characters.');return
        if choose_topic:
            request['choose_topic'] = True
        if planning_only and not smoke_mode:
            request['raghu_plan_only'] = True
        if smoke_mode:
            request['mode'] = 'live'
            request['raghu_smoke'] = smoke_mode
        if raghu and not smoke_mode:
            if duration is None:
                with store.db() as c:
                    c.execute("INSERT INTO telegram_duration_requests(bot,chat_id,user_id,request) VALUES (?,?,?,?) "
                              "ON CONFLICT(bot,chat_id,user_id) DO UPDATE SET request=excluded.request",
                              (*draft_key, json.dumps(request, ensure_ascii=False)))
                self.ask_duration(cid)
                return
            request = {**pending, "duration_seconds": duration}
            if not planning_only and 'music_track' not in request and raghu_music.choices():
                with store.db() as c:
                    c.execute("UPDATE telegram_duration_requests SET request=? WHERE bot=? AND chat_id=? AND user_id=?",
                              (json.dumps({**request, 'awaiting_music': True}, ensure_ascii=False), *draft_key))
                self.ask_music(cid)
                return
            request.setdefault('music_track', 'none')
            if not planning_only and not request.get("supplied_script"):
                request["script_options"] = True
        if request['mode']=='live' and smoke_mode != 'review' and os.getenv('ALLOW_PAID_GENERATION','false').lower()!='true':
            self.api.text(cid,'Live production is disabled in the deployment settings.');return
        if raghu and request['mode']=='live' and not smoke_mode and not planning_only:
            from .raghu_pipeline import production_credentials, validate_music_for_request
            try:
                validate_music_for_request(request)
                production_credentials()
            except RuntimeError as exc:
                self.api.text(cid, f'Production setup is incomplete. {exc} No GPU was rented.')
                return
        ident=hashlib.sha256(f'{self.bot}:{update["update_id"]}'.encode()).hexdigest()[:32]
        try:store.create(request,name,ident=ident)
        except ValueError as exc:self.api.text(cid,str(exc));return
        with store.db() as c:c.execute('INSERT INTO telegram_jobs(job_id,bot,chat_id,user_id) VALUES (?,?,?,?) ON CONFLICT(job_id) DO NOTHING',(ident,self.bot,str(cid),str(uid)))
        store.folder(ident).mkdir(parents=True,exist_ok=True)
        if not database.postgres() and store.get(ident)['status']=='queued':pipeline.enqueue(ident)
        if duration is not None:
            with store.db() as c:
                c.execute("DELETE FROM telegram_duration_requests WHERE bot=? AND chat_id=? AND user_id=?", draft_key)
            self.api.call('sendMessage', {'chat_id':cid, 'text':f"Queued a {duration}-second video. I’ll update you here.",
                                          'reply_markup': {'remove_keyboard': True}})

    def recover(self, update):
        query = update["callback_query"]
        user = query.get("from", {})
        chat = query.get("message", {}).get("chat", {})
        if chat.get("type") != "private" or user.get("is_bot") or not allowed(user.get("id"), chat.get("id")):
            return
        cid, uid = str(chat["id"]), str(user["id"])
        choice = re.fullmatch(r"script([12]):([a-f0-9]{32})", query.get("data", ""))
        if choice:
            from . import script_options
            number, ident = choice.groups()
            with store.db() as c:
                owned = c.execute("SELECT job_id FROM telegram_jobs WHERE bot=? AND chat_id=? AND user_id=? AND job_id=?",
                                  (self.bot, cid, uid, ident)).fetchone()
            job = store.get(ident) if owned else None
            valid = bool(job and job['status'] == 'awaiting_script')
            self.answer_callback({"callback_query_id": query['id'],
                          "text": "Script selected." if valid else "This choice is no longer available."})
            if valid:
                try:
                    script_options.select(store.folder(ident), int(number))
                    queued = pipeline.enqueue(ident)
                except ValueError as exc:
                    self.api.text(cid, str(exc))
                    return
                if queued:
                    with store.db() as c:
                        c.execute("UPDATE telegram_jobs SET delivery='pending',last_text=NULL WHERE job_id=?", (ident,))
                    self.api.text(cid, f"Script {number} selected. Preparing its characters and scene prompts.")
            return
        action = re.fullmatch(r"(retry|new):([a-f0-9]{32})", query.get("data", ""))
        if not action:
            return
        kind, ident = action.groups()
        with store.db() as c:
            row = c.execute("SELECT job_id FROM telegram_jobs WHERE bot=? AND chat_id=? AND user_id=? AND job_id=?",
                            (self.bot, cid, uid, ident)).fetchone()
        job = store.get(ident) if row else None
        active = bool(job and recovery_job(job))
        self.answer_callback({"callback_query_id": query["id"],
                      "text": "Working on it." if active else "This recovery option has expired."})
        if active:
            text = f"/resume {ident}" if kind == "retry" else "/generate"
            self.handle({"update_id": update["update_id"], "message": {"chat": chat, "from": user, "text": text}})

    def answer_callback(self, fields):
        try:
            self.api.call('answerCallbackQuery',fields)
        except BotError as exc:
            if exc.code not in (400,403,404):raise
            logging.info('Callback acknowledgement expired (code %s); checking saved action',exc.code)

    def ask_duration(self, cid):
        self.api.call('sendMessage', {'chat_id': cid, 'text': DURATION_PROMPT, 'reply_markup': {
            'keyboard': [[{'text': label} for label in DURATION_CHOICES]],
            'resize_keyboard': True, 'one_time_keyboard': True,
        }})

    def ask_music(self, cid):
        tracks = raghu_music.choices()
        self.api.text(cid, 'Choose the background music. Narration keeps the approved volume; these are short previews of each installed track.')
        for track in tracks:
            with tempfile.TemporaryDirectory(prefix='music-sample-') as temp:
                audio = track.get('audio_preview')
                if audio is None:
                    audio = Path(temp) / 'sample.mp3'
                    media.ff('-i', track['preview'], '-t', '12', '-vn', '-c:a', 'libmp3lame', '-q:a', '3', audio)
                with audio.open('rb') as stream:
                    self.api.call('sendAudio', {'chat_id': cid, 'title': track['title'],
                                  'caption': track['title']}, files={'audio': (
                                      audio.name, stream, 'audio/mpeg')})
        self.api.call('sendMessage', {'chat_id': cid, 'text': 'Which track should this video use?',
                      'reply_markup': {'keyboard': [[{'text': t['title']}] for t in tracks]
                          + [[{'text': 'No music'}]], 'resize_keyboard': True, 'one_time_keyboard': True}})

    def send_status(self, cid, job, *, detailed=False):
        if job['status'] == 'awaiting_script':
            return self.send_script_choices(cid, job)
        message = status_text(job, detailed=detailed)
        if recovery_job(job):
            return self.api.call("sendMessage", {"chat_id": cid, "text": message,
                                 "reply_markup": recovery_markup(job)})
        return self.api.text(cid, message)

    def send_script_choices(self, cid, job):
        from . import script_options
        packet = script_options.read_options(store.folder(job['id']))
        for number, value in enumerate(packet["options"], 1):
            text = f"Script option {number}\n\n" + script_options.script_text(value)
            # Telegram limits message length; never truncate a long script.
            for start in range(0, len(text), 3500):
                self.api.text(cid, text[start:start + 3500])
        return self.api.call('sendMessage', {'chat_id': cid,
            'text': 'Which script should become your video? No GPU has been rented for this job.',
            'reply_markup': {'inline_keyboard': [[
                {'text': 'Use script 1', 'callback_data': f"script1:{job['id']}"},
                {'text': 'Use script 2', 'callback_data': f"script2:{job['id']}"}]]}})

    def notify(self):
        with store.db() as c:rows=[dict(r) for r in c.execute("SELECT t.* FROM telegram_jobs t JOIN jobs j ON j.id=t.job_id LEFT JOIN telegram_reviews r ON r.job_id=t.job_id WHERE t.bot=? AND t.delivery NOT IN ('blocked') AND (t.delivery IN ('pending','sending') OR j.status IN ('running','queued') OR (j.status='complete' AND t.delivery='sent' AND r.prompt_message IS NULL)) ORDER BY t.notified LIMIT 100",(self.bot,))]
        for row in rows:
            try:self.notify_one(row)
            except BotError as exc:
                if exc.code==429:raise
                if exc.code==403:
                    with store.db() as c:c.execute("UPDATE telegram_jobs SET delivery='blocked' WHERE job_id=?",(row['job_id'],))
                logging.warning('Telegram notification deferred (code %s)',exc.code)
            except Exception:logging.error('Telegram artifact delivery deferred; film remains saved')

    def notify_one(self,row):
        if not allowed(row['user_id'],row['chat_id']):return
        job=store.get(row['job_id']);text=status_text(job)
        if row['delivery']=='sending':
            # A prior process may have sent the video without saving the response.
            with store.db() as c:c.execute("UPDATE telegram_jobs SET delivery='uncertain' WHERE job_id=?",(job['id'],))
            self.api.text(row['chat_id'],'Video delivery was interrupted. If it did not arrive, use /video to request it again.');return
        if row['last_text']!=text and (time.time()-row['notified']>=10 or job['status'] not in ('running','queued')):
            if job['status'] in ('failed', 'paused', 'awaiting_script'):
                # Edits do not notify the user of a production that needs attention.
                row['message_id'] = None
            if row['message_id']:
                try:self.api.call('editMessageText',{'chat_id':row['chat_id'],'message_id':row['message_id'],'text':text})
                except BotError as exc:
                    if exc.code!=400:raise
                    row['message_id']=None
            if not row['message_id']:
                row['message_id'] = self.send_status(row["chat_id"], job)["message_id"]
            with store.db() as c:c.execute('UPDATE telegram_jobs SET message_id=?,last_text=?,notified=? WHERE job_id=?',(row['message_id'],text,time.time(),job['id']))
        if job['status']=='complete' and plan_only_job(job):
            with store.db() as c:
                c.execute("UPDATE telegram_jobs SET delivery='stopped' WHERE job_id=?", (job['id'],))
        elif job['status']=='complete' and row['delivery']=='pending':self.deliver(row,job)
        elif job['status']=='complete' and row['delivery']=='sent':self.ask_for_review(row,job)
        elif job['status'] in ('failed','paused','cancelled','awaiting_script'):
            with store.db() as c:c.execute("UPDATE telegram_jobs SET delivery='stopped' WHERE job_id=?",(job['id'],))

    def send_images(self, cid, job):
        folder = store.folder(job['id'])
        pattern = r'(?:rejected-)?(?:(?:still|end)-[0-9]{3}|character-[A-Z0-9-]{1,30}|background-[A-Z0-9-]{1,30}|smoke-image|review-check)\.png'
        hosted = storage.enabled()
        records = {r['name']: r for r in storage.records(job['id'])} if hosted else {}
        available = records.keys() if hosted else (p.name for p in folder.glob('*.png') if p.is_file())
        images = sorted(name for name in available if re.fullmatch(pattern, name))
        if not images:
            self.api.text(cid, 'No images have been saved for this production yet.')
            return
        with tempfile.TemporaryDirectory(prefix='raghu-images-') as temp:
            directory = Path(temp)
            batch, size, part = [], 0, 1
            for name in images:
                report = name + '.review.json'
                has_report = report in records if hosted else (folder / report).is_file()
                names = [name, report] if has_report else [name]
                needed = sum((records[n]['size'] if hosted else (folder / n).stat().st_size) + 512 for n in names)
                if batch and size + needed > 42_000_000:
                    self.send_image_archive(cid, directory, batch, part)
                    if hosted:
                        for path in batch:
                            path.unlink()
                    batch, size, part = [], 0, part + 1
                for asset in names:
                    path = folder / asset
                    if hosted:
                        path = directory / asset
                        storage.client().download_file(os.environ['S3_BUCKET'], records[asset]['object_key'], str(path))
                        if storage.file_digest(path) != records[asset]['digest']:
                            raise RuntimeError('A saved image archive asset failed its checksum.')
                    batch.append(path)
                size += needed
            self.send_image_archive(cid, directory, batch, part)

    def send_image_archive(self, cid, directory, files, part):
        target = directory / f'production-images-{part}.zip'
        with zipfile.ZipFile(target, 'w') as archive:
            for path in files:
                archive.write(path, path.name)
        with target.open('rb') as stream:
            self.api.call('sendDocument', {'chat_id': cid},
                          files={'document': (target.name, stream, 'application/zip')})
        target.unlink()

    def send_image_review(self, cid, job, filename):
        ident = job['id']
        if filename == 'smoke-image.png':
            packet = storage.read_json(ident, 'raghu-smoke.json')
            decision = packet.get('visual_review') if packet else None
        else:
            decision = storage.read_json(ident, filename + '.review.json')
        if not decision:
            self.api.text(cid, 'This image has no saved verdict yet.')
            return
        with tempfile.TemporaryDirectory(prefix='raghu-image-review-') as directory:
            path = store.folder(ident) / filename
            if storage.enabled():
                record = next((r for r in storage.records(ident) if r['name'] == filename), None)
                if record:
                    path = Path(directory) / filename
                    storage.client().download_file(os.environ['S3_BUCKET'], record['object_key'], str(path))
            verdict = 'Passed' if decision['pass'] else 'Flagged'
            style = decision.get('style', 'unrecorded').replace('_', ' ')
            observations = decision.get('observations', '')
            message = f"{verdict} · {style}\n{decision['reason']}"
            if decision.get('minor_findings'):
                message += '\nMinor findings are listed in the attached report.'
            if observations and observations != decision['reason']:
                message += '\nObserved: ' + observations
            self.send_review_files(cid, path, message, decision)

    def send_review_files(self, cid, image, caption, report):
        encoded = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
        attachment = ('railway-image-review.json', encoded, 'application/json')
        if not image.is_file():
            return self.api.call('sendDocument', {'chat_id': cid, 'caption': caption[:1024]},
                                 files={'document': attachment})
        album = [
            {'type': 'document', 'media': 'attach://image', 'caption': caption[:1024]},
            {'type': 'document', 'media': 'attach://report'},
        ]
        with image.open('rb') as stream:
            messages = self.api.call('sendMediaGroup', {'chat_id': cid, 'media': json.dumps(album)},
                files={'image': (image.name, stream, 'image/png'), 'report': attachment})
        return messages[0]

    def deliver_smoke(self, row, job, mode):
        if mode == 'review':
            packet = storage.read_json(job['id'], 'raghu-review-check.json')
            matching, mismatched = packet['matching'], packet['mismatched']
            caption = (('Reviewer check passed.' if packet['passed'] else 'Reviewer check flagged.') +
                       f"\nOriginal prompt · {'pass' if matching['pass'] else 'reject'} · {matching['reason']}" +
                       f"\nWrong prompt · {'pass' if mismatched['pass'] else 'reject'} · {mismatched['reason']}")
            caption += "\n" + " · ".join(name + (": yes" if ok else ": no") for name, ok in packet["checks"].items())
            self.send_smoke_asset(row, job, 'image', 'review-check.png', caption, report=packet)
            return
        report = storage.read_json(job['id'], 'raghu-smoke.json')
        decision = report['visual_review']
        result_text = ('Image saved · AI review disabled' if decision.get('review_skipped') else
                       'Image review passed' if decision['pass'] else 'Image review rejected the still')
        caption = (f"{result_text} in {report['review_seconds']:.1f}s. "
                   f"Image generation {report['image_render_seconds']:.1f}s. Vast closed.\n"
                   + decision['reason'])
        if mode == 'motion':
            caption = f"Motion generation {report['clip_render_seconds']:.1f}s. " + caption
        filename = 'smoke-image.png' if mode == 'image' else 'shot-001.mp4'
        self.send_smoke_asset(row, job, mode, filename, caption, report=report if mode == "image" else None)

    def send_smoke_asset(self, row, job, mode, filename, caption, report=None):
        folder = store.folder(job['id'])
        with tempfile.TemporaryDirectory(prefix='raghu-telegram-') as directory:
            asset = folder / filename
            if storage.enabled():
                with store.db() as connection:
                    record = connection.execute("SELECT object_key FROM artifacts WHERE job_id=? AND name=?",
                                                (job['id'], filename)).fetchone()
                if not record:
                    raise RuntimeError('Smoke-test media is missing from storage.')
                asset = Path(directory) / filename
                storage.client().download_file(os.environ['S3_BUCKET'], record[0], str(asset))
            field = 'document' if mode == 'image' else 'video'
            mime = 'image/png' if mode == 'image' else 'video/mp4'
            with store.db() as connection:
                connection.execute("UPDATE telegram_jobs SET delivery='sending' WHERE job_id=?", (job['id'],))
            try:
                if report is not None:
                    result = self.send_review_files(row['chat_id'], asset, caption, report)
                else:
                    with asset.open('rb') as stream:
                        result = self.api.call('sendDocument' if mode == 'image' else 'sendVideo',
                            {'chat_id': row['chat_id'], 'caption': caption[:1024]},
                            files={field: (filename, stream, mime)})
            except BotError as exc:
                with store.db() as connection:
                    connection.execute("UPDATE telegram_jobs SET delivery=? WHERE job_id=?",
                                       ('sending' if exc.uncertain else 'pending', job['id']))
                raise
            with store.db() as connection:
                connection.execute("UPDATE telegram_jobs SET delivery='sent',video_message=? WHERE job_id=?",
                                   (result['message_id'], job['id']))
                connection.execute("INSERT INTO telegram_reviews(job_id,state,prompt_message) VALUES (?,?,?) ON CONFLICT(job_id) DO NOTHING",
                                   (job['id'], 'kept', result['message_id']))

    def deliver(self,row,job):
        smoke_mode = job['request'].get('raghu_smoke')
        if smoke_mode:
            self.deliver_smoke(row, job, smoke_mode)
            return
        with tempfile.TemporaryDirectory(prefix='nightfall-telegram-') as temp:
            video=Path(temp)/'film.mp4'
            link = None
            if storage.enabled():
                with store.db() as c:
                    record = c.execute("SELECT object_key,size FROM artifacts WHERE job_id=? AND name='final.mp4'",
                                       (job['id'],)).fetchone()
                if not record:
                    raise RuntimeError('Final film is not available in storage')
                if record['size'] > 48_000_000:
                    link = storage.client().generate_presigned_url('get_object',
                        Params={'Bucket': os.environ['S3_BUCKET'], 'Key': record['object_key']}, ExpiresIn=86400)
                else:
                    storage.client().download_file(os.environ['S3_BUCKET'],record['object_key'],str(video))
            else:
                video = store.folder(job['id']) / 'final.mp4'
            if not link and video.stat().st_size>48_000_000:
                compressed=Path(temp)/'telegram.mp4'
                media.ff('-i',video,'-c:v','libx264','-preset','fast','-b:v',str(max(160, min(3500, int(355000/media.duration(video))-128)))+'k','-maxrate','4000k','-bufsize','8000k','-c:a','aac','-b:a','128k','-movflags','+faststart',compressed)
                video=compressed
            if not link and video.stat().st_size>48_000_000:raise RuntimeError('Film exceeds the Telegram upload budget after compression')
            if not link:
                story = storage.read_json(job['id'], 'story.json') if job['request'].get('choose_topic') else None
                title = story.get('title', job['request']['title']) if story else job['request']['title']
            with store.db() as c:c.execute("UPDATE telegram_jobs SET delivery='sending' WHERE job_id=?",(job['id'],))
            try:
                if link:
                    result = self.api.text(row['chat_id'], 'Your full-quality video is ready. Download link expires in 24 hours.\n' + link)
                else:
                    with video.open('rb') as stream:result=self.api.call('sendVideo',{'chat_id':row['chat_id'],'caption':title+('\nSix acts' if os.getenv('MEDIA_BACKEND', '').lower() == 'raghu' else '\nSix acts · 16:9')+ ('\nDEMO: test cards, no AI footage.' if job['request']['mode']=='demo' else ''),'supports_streaming':'true'},files={'video':('film.mp4',stream,'video/mp4')})
            except BotError as exc:
                with store.db() as c:c.execute('UPDATE telegram_jobs SET delivery=? WHERE job_id=?',('sending' if exc.uncertain else 'pending',job['id']))
                raise
            with store.db() as c:
                c.execute("UPDATE telegram_jobs SET delivery='sent',video_message=? WHERE job_id=?",(result['message_id'],job['id']))
                c.execute("INSERT INTO telegram_reviews(job_id) VALUES (?) ON CONFLICT(job_id) DO NOTHING",(job['id'],))
            self.ask_for_review(row,job)

    def ask_for_review(self,row,job):
        current=review(job['id'])
        if not current or current['prompt_message'] is not None:return
        result=self.api.text(row['chat_id'],'Your video is ready for review. Is it approved for YouTube?\n\n/approve — approve for YouTube\n/reject — not approved')
        with store.db() as c:c.execute('UPDATE telegram_reviews SET prompt_message=? WHERE job_id=?',(result['message_id'],job['id']))

    def publish_approved_video(self,job,cid):
        """The only route that can call Zernio's publishing endpoint."""
        try:
            with tempfile.TemporaryDirectory(prefix='nightfall-youtube-') as temp:
                video=Path(temp)/'film.mp4'
                if storage.enabled():
                    with store.db() as c:record=c.execute("SELECT object_key FROM artifacts WHERE job_id=? AND name='final.mp4'",(job['id'],)).fetchone()
                    if not record:raise RuntimeError('Final film is not available in storage')
                    storage.client().download_file(os.environ['S3_BUCKET'],record[0],str(video))
                else:
                    import shutil
                    shutil.copyfile(store.folder(job['id'])/'final.mp4',video)
                result=zernio.publish_youtube(video,storage.read_json(job['id'],'publishing.json'),job['id'])
            post=result.get('post',result)
            post_id=post.get('_id',post.get('id',''))
            with store.db() as c:c.execute("UPDATE telegram_reviews SET state='published',youtube_status='published',youtube_post_id=?,error=NULL WHERE job_id=?",(post_id,job['id']))
            self.api.text(cid,'Approved and posted to YouTube.')
        except (RuntimeError,zernio.ZernioError) as exc:
            with store.db() as c:c.execute("UPDATE telegram_reviews SET youtube_status='failed',error=? WHERE job_id=?",(str(exc)[:500],job['id']))
            self.api.text(cid,'The video was approved, but YouTube publishing did not complete. Nothing will be retried automatically.')

def run(stop):
    global STATE
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    if not token or os.getenv('TELEGRAM_ENABLED','true').lower()!='true':return
    STATE='connecting'
    api=API(token);leader=None
    try:
        bot=str(api.call('getMe')['id']);service=Service(api,bot)
        if database.postgres():
            import psycopg
            leader=psycopg.connect(os.environ['DATABASE_URL'],autocommit=True)
            while not leader.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',('telegram:'+bot,)).fetchone()[0]:
                STATE='standby'
                if stop.wait(3):return
        if api.call('getWebhookInfo').get('url'):raise RuntimeError('Remove the existing bot webhook before enabling polling')
        with store.db() as c:c.execute('INSERT INTO telegram_cursor VALUES (?,0) ON CONFLICT(bot) DO NOTHING',(bot,))
        STATE='polling'
        while not stop.is_set():
            try:
                if leader:leader.execute('SELECT 1') # Lost leader connection must stop polling.
                with store.db() as c:offset=c.execute('SELECT next_update FROM telegram_cursor WHERE bot=?',(bot,)).fetchone()[0]
                updates=api.call('getUpdates',{'offset':offset,'timeout':10,'allowed_updates':['message','callback_query'],'limit':20})
                for update in updates:
                    try:service.handle(update)
                    except BotError as exc:
                        if exc.code not in (400,403,404):raise
                        logging.warning('Skipped permanently rejected Telegram update %s (code %s)',update['update_id'],exc.code)
                    with store.db() as c:c.execute('UPDATE telegram_cursor SET next_update=? WHERE bot=?',(update['update_id']+1,bot))
                service.notify()
            except BotError as exc:
                logging.warning('Telegram temporarily unavailable (code %s)',exc.code)
                stop.wait(min(300,max(3,exc.retry_after)))
            except Exception:
                logging.error('Telegram processing paused; saved jobs are retained')
                if leader and leader.closed:break
                stop.wait(10)
    except Exception:logging.error('Telegram startup incomplete; check token, webhook and database settings')
    finally:
        STATE='stopped'
        api.close()
        if leader:leader.close()

def supervise(stop):
    if not os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_ENABLED','true').lower()!='true':return
    while not stop.is_set():
        run(stop)
        stop.wait(10)
