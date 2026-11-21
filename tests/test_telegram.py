import json
import pytest
from fastapi.testclient import TestClient
from app import store,auth,pipeline,storage,telegram_bot as tg
from app.main import app, queue_saved_story
from app.pipeline import enqueue as enqueue_job

class FakeAPI:
    def __init__(self):self.calls=[];self.fail_video=False;self.block_chat=None
    def call(self,method,fields=None,files=None):
        self.calls.append((method,fields,files is not None))
        if self.block_chat is not None and fields and str(fields.get('chat_id'))==str(self.block_chat):raise tg.BotError(403)
        if method=='sendVideo' and self.fail_video:raise tg.BotError(uncertain=True)
        if method=='sendVideo':assert files['video'][1].read()
        return {'message_id':len(self.calls)}
    def text(self,chat,text):return self.call('sendMessage',{'chat_id':chat,'text':text})

@pytest.fixture
def service(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.delenv('S3_BUCKET',raising=False)
    monkeypatch.delenv('MEDIA_BACKEND',raising=False)
    monkeypatch.setenv('TELEGRAM_PRODUCTION_MODE','demo')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','')
    monkeypatch.setenv('TELEGRAM_ALLOWED_USER_IDS','101,202,303')
    monkeypatch.setattr(pipeline,'enqueue',lambda ident:True)
    store.init();auth.init();tg.init()
    return tg.Service(FakeAPI(),'900')

def update(text='Haunted road',uid=101,ident=1,kind='private'):
    return {'update_id':ident,'message':{'message_id':ident,'text':text,'chat':{'id':uid,'type':kind},'from':{'id':uid}}}


def test_expired_callback_acknowledgement_does_not_block_retry(service, monkeypatch):
    service.handle(update())
    ident=tg.latest(service.bot,101)['id'];store.update(ident,status='failed',error='saved error')
    monkeypatch.setenv('MEDIA_BACKEND','raghu')
    queued=[];monkeypatch.setattr(pipeline,'enqueue',lambda ident:queued.append(ident) or True)
    original=service.api.call
    def call(method,*a,**kw):
        if method=='answerCallbackQuery':raise tg.BotError(400)
        return original(method,*a,**kw)
    monkeypatch.setattr(service.api,'call',call)
    service.handle({'update_id':2,'callback_query':{'id':'expired','data':f'retry:{ident}',
        'from':{'id':101},'message':{'chat':{'id':101,'type':'private'}}}})
    assert queued==[ident]


def test_duration_ack_failure_happens_after_local_enqueue(service, monkeypatch):
    from app import raghu_pipeline
    monkeypatch.setenv('MEDIA_BACKEND','raghu');monkeypatch.setenv('RAGHU_PLAN_ONLY','false')
    monkeypatch.setenv('TELEGRAM_PRODUCTION_MODE','live');monkeypatch.setenv('ALLOW_PAID_GENERATION','true')
    monkeypatch.setattr(raghu_pipeline,'production_credentials',lambda:None)
    queued=[];monkeypatch.setattr(pipeline,'enqueue',lambda ident:queued.append(ident) or True)
    service.handle(update('/generate'))
    original=service.api.call
    def call(method,fields=None,**kwargs):
        if method=='sendMessage' and fields['text'].startswith('Queued a'):raise tg.BotError(502)
        return original(method,fields,**kwargs)
    monkeypatch.setattr(service.api,'call',call)
    with pytest.raises(tg.BotError):service.handle(update('30 seconds',ident=2))
    assert len(queued)==1


def test_script_choice_uses_shared_options_and_durable_selection(service, monkeypatch):
    from app import script_options
    service.handle(update());ident=tg.latest(service.bot,101)['id']
    store.update(ident,status='awaiting_script')
    packet={'options':[{'speech':[{'line':'First'}]},{'speech':[{'line':'Second'}]}]}
    monkeypatch.setattr(storage,'enabled',lambda:True)
    monkeypatch.setattr(storage,'read_json',lambda job,name:packet)
    monkeypatch.setattr(storage,'publish_path',lambda _:None)
    service.send_script_choices(101,store.get(ident))
    script_options.select(store.folder(ident),2)
    assert store.get(ident)['script_choice']==2
    # A worker's old local copy must not erase the user's shared choice.
    store.save(store.folder(ident)/'script-options.json',packet)
    assert script_options.choose(store.folder(ident),lambda *a:pytest.fail('Rewrote saved script'))==packet['options'][1]

def test_raghu_recovery_buttons_retry_only_the_chat_owned_job(service, monkeypatch):
    service.handle(update())
    ident = tg.latest(service.bot, 101)["id"]
    store.update(ident, status="failed", error="Motion retakes exhausted")
    monkeypatch.setenv("MEDIA_BACKEND", "raghu")
    queued = []
    monkeypatch.setattr(pipeline, "enqueue", lambda ident: queued.append(ident) or True)
    service.notify()
    sent = next(fields for method, fields, _ in service.api.calls if method == "sendMessage"
                and "inline_keyboard" in fields.get("reply_markup", {}))
    assert sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"retry:{ident}"

    def callback(user, action, update_id):
        service.handle({"update_id": update_id, "callback_query": {
            "id": str(update_id), "data": f"{action}:{ident}", "from": {"id": user},
            "message": {"chat": {"id": user, "type": "private"}},
        }})

    callback(202, "retry", 2)
    assert not queued
    callback(101, "retry", 3)
    assert queued == [ident]
    assert store.read(store.folder(ident) / "raghu-user-retry.json")["round"]
    callback(101, "new", 4)
    assert any(fields.get("text") == tg.DURATION_PROMPT for _, fields, _ in service.api.calls)


def test_raghu_status_explains_operation_timeout_and_returns_recovery_buttons(service, monkeypatch):
    service.handle(update())
    ident = tg.latest(service.bot, 101)["id"]
    store.update(ident, status="failed", error="Vast operation reached its deadline.")
    monkeypatch.setenv("MEDIA_BACKEND", "raghu")
    service.handle(update("/status", ident=2))
    message = service.api.calls[-1][1]
    assert "A Vast step stopped responding in time" in message["text"]
    assert message["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"retry:{ident}"


def test_raghu_setup_lists_missing_credentials_without_printing_values(service, monkeypatch):
    monkeypatch.setenv("MEDIA_BACKEND", "raghu")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    service.handle(update("/setup"))
    message = service.api.calls[-1][1]["text"]
    assert "GEMINI_API_KEY" in message
    assert "fixture-secret" not in message
    assert store.all_jobs() == []


@pytest.mark.parametrize("command", ["/image_review", "/raghu_review_check"])
def test_retired_visual_review_commands_do_not_queue_work(service, monkeypatch, command):
    monkeypatch.setenv("MEDIA_BACKEND", "raghu")
    service.handle(update(command))
    assert "AI image and video reviews are disabled" in service.api.calls[-1][1]["text"]
    assert store.all_jobs() == []


def test_chatterbox_setup_does_not_request_a_gemini_review_key(service, monkeypatch):
    monkeypatch.setenv("MEDIA_BACKEND", "raghu")
    monkeypatch.setenv("RAGHU_NARRATION_PROVIDER", "chatterbox")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_VISION_MODEL", raising=False)
    service.handle(update("/setup"))
    assert "GEMINI" not in service.api.calls[-1][1]["text"]


def test_title_routes_once_and_preserves_chat_owner(service):
    service.handle(update());service.handle(update())
    jobs=store.all_jobs();assert len(jobs)==1 and jobs[0]['owner']=='tg_900_101'
    assert jobs[0]['request']['title']=='Haunted road'
    assert jobs[0]['request']['audio_mode']=='elevenlabs'
    service.notify();service.notify()
    assert len(service.api.calls)==1

def test_casual_message_gets_video_only_reply_without_a_job(service):
    service.handle(update('Hey, how are you?'))
    assert store.all_jobs()==[]
    assert service.api.calls[-1][1]['text']==tg.VIDEO_ONLY

def test_ready_video_request_creates_a_real_production(service):
    service.handle(update('Pick any topic and generate video'))
    texts=[call[1].get('text','') for call in service.api.calls if call[0]=='sendMessage']
    assert texts==["Okay, I'm going with this topic: The Last Bus That Never Reached Home.\n\nGenerating your script."]
    jobs=store.all_jobs()
    assert len(jobs)==1 and jobs[0]['request']['title']==tg.READY_VIDEO_TOPIC

def test_allowed_private_chat_can_start_and_groups_are_ignored(service):
    service.handle(update(uid=303));service.handle(update(kind='group',ident=2))
    assert len(store.all_jobs())==1
    assert store.all_jobs()[0]['owner']=='tg_900_303'
    service.handle(update('/start',uid=303,ident=3))
    assert 'Send a story title' in service.api.calls[-1][1]['text']

def test_unlisted_private_chat_cannot_create_a_job(service):
    service.handle(update(uid=404))
    assert store.all_jobs()==[]

def test_wildcard_allows_any_private_chat_but_not_groups(service,monkeypatch):
    monkeypatch.setenv('TELEGRAM_ALLOWED_USER_IDS','*')
    service.handle(update(uid=404))
    service.handle(update(uid=405,ident=2,kind='group'))
    assert [job['owner'] for job in store.all_jobs()]==['tg_900_404']

def test_cancel_and_status_are_chat_scoped(service):
    service.handle(update());service.handle(update('Second film',202,2))
    service.handle(update('/cancel',101,3))
    assert tg.latest('900',101)['cancel']==1
    assert tg.latest('900',202)['cancel']==0
    service.handle(update('/status',202,4))
    assert 'Second film' in service.api.calls[-1][1]['text']

def complete(service,uid=101,ident=1):
    service.handle(update(uid=uid,ident=ident));job=tg.latest('900',uid)
    (store.folder(job['id'])/'final.mp4').write_bytes(b'test-video')
    store.update(job['id'],status='complete',stage='Complete',progress=100)
    return job

def test_resume_keeps_completed_plan_and_only_requeues_failed_job(service, monkeypatch):
    service.handle(update("Finished story"))
    finished = tg.latest('900', 101)['id']
    store.update(finished, status='complete', stage='Script and storyboard ready · planning only', progress=100)
    store.save(store.folder(finished) / 'story.json', {'script': 'Saved script'})
    queued = []
    monkeypatch.setattr(pipeline, 'enqueue', queued.append)
    service.handle(update('/resume', ident=2))
    assert '/script' in service.api.calls[-1][1]['text']
    assert queued == []
    service.handle(update('/script', ident=3))
    assert service.api.calls[-1][0] == 'sendDocument'

    service.handle(update("Second story", ident=4))
    unfinished = tg.latest('900', 101)['id']
    store.update(unfinished, status='failed', stage='Outline review failed')
    queued.clear()
    service.handle(update('/resume', ident=5))
    assert queued == [unfinished]
    assert store.get(finished)['status'] == 'complete'

def test_queue_saved_story_starts_once_and_sends_new_telegram_progress(service, monkeypatch):
    from app import raghu_pipeline

    service.handle(update("Saved film"))
    ident = tg.latest('900', 101)['id']
    store.update(ident, status='complete', stage='Script and storyboard ready · planning only', progress=100)
    story = {'title': 'Approved title', 'script': 'Approved script', 'seconds': 30,
             'shots': [{'id': f'{number:03d}'} for number in range(1, 7)]}
    store.save(store.folder(ident) / 'raghu-plan-v2.json', story)
    service.notify()
    queued = []
    monkeypatch.setenv('ALLOW_PAID_GENERATION', 'true')
    monkeypatch.setenv('MEDIA_BACKEND', 'raghu')
    monkeypatch.setattr(raghu_pipeline, 'production_credentials', lambda: None)
    monkeypatch.setattr(pipeline, 'enqueue', lambda job_id: queued.append(job_id) or True)
    queue_saved_story(ident)
    queue_saved_story(ident)
    job = store.get(ident)
    assert queued == [ident]
    assert job['request']['title'] == 'Approved title'
    assert job['request']['raghu_produce_saved']
    assert not tg.plan_only_job(job)
    store.update(ident, status='running', stage='Approved script and storyboard loaded', progress=24)
    service.notify()
    assert service.api.calls[-1][0] == 'sendMessage'
    assert 'Approved script and storyboard loaded' in service.api.calls[-1][1]['text']

def test_resume_grants_bounded_budget_to_the_failed_plan(service, monkeypatch):
    service.handle(update("Story with an exhausted planning budget"))
    ident = tg.latest('900', 101)['id']
    monkeypatch.setattr(pipeline, 'enqueue', enqueue_job)
    monkeypatch.setattr(pipeline, 'active', set())
    monkeypatch.setattr(pipeline.pool, 'submit', lambda *args: None)
    for count in range(1, 4):
        store.update(ident, status='failed', error=pipeline.PLANNING_BUDGET_ERROR)
        pipeline.active.clear()
        service.handle(update(f'/resume {ident}', ident=count + 1))
        assert store.get(ident)['planning_resumes'] == min(count, 2)
    assert 'both extra planning attempts' in service.api.calls[-1][1]['text']
    assert store.get(ident)['status'] == 'failed'

def test_video_delivery_and_restart_do_not_resend(service):
    complete(service)
    service.notify();tg.Service(service.api,'900').notify()
    assert [c[0] for c in service.api.calls].count('sendVideo')==1
    service.handle(update('/video',ident=2));service.notify()
    assert [c[0] for c in service.api.calls].count('sendVideo')==2

def test_uncertain_upload_requires_explicit_resend(service):
    complete(service);service.api.fail_video=True
    service.notify();service.notify();service.notify()
    assert [c[0] for c in service.api.calls].count('sendVideo')==1
    assert any('/video' in c[1].get('text','') for c in service.api.calls)

def test_blocked_chat_does_not_block_other_delivery(service):
    complete(service);complete(service,202,2);service.api.block_chat='101'
    service.notify()
    assert any(c[0]=='sendVideo' and c[1]['chat_id']=='202' for c in service.api.calls)

def test_no_dashboard_or_browser_api(service):
    with TestClient(app) as client:
        assert client.get('/healthz').json()['interface']=='telegram'
        for path in ('/','/login','/signup','/api/jobs','/static/app.js'):
            assert client.get(path).status_code==404

def test_live_requires_operator_approval_before_creating_job(service,monkeypatch):
    monkeypatch.setenv('TELEGRAM_PRODUCTION_MODE','live')
    monkeypatch.setenv('ALLOW_PAID_GENERATION','false')
    service.handle(update())
    assert store.all_jobs()==[]

def test_private_owner_receives_updates(service):
    complete(service);service.notify()
    assert any(call[0]=='sendVideo' for call in service.api.calls)

def test_scene_regeneration_cannot_target_another_chat(service,monkeypatch):
    own=complete(service);complete(service,202,2)
    regenerated=[]
    monkeypatch.setattr(pipeline,'regenerate',lambda ident,number:regenerated.append((ident,number)))
    service.handle(update('/scene 3',ident=3))
    service.handle(update('/scene 99',ident=4))
    assert regenerated==[(own['id'],3)]

def test_title_to_real_demo_video_delivery(service):
    from app import media
    if not media.available():pytest.skip('FFmpeg unavailable')
    service.handle(update('Telegram end-to-end demo'))
    job=tg.latest('900',101)
    pipeline.work(job['id'])
    assert store.get(job['id'])['status']=='complete',store.get(job['id'])['error']
    assert abs(media.duration(store.folder(job['id'])/'final.mp4')-45)<.15
    service.notify()
    assert any(call[0]=='sendVideo' for call in service.api.calls)


def test_image_review_attachment_keeps_the_entire_railway_report(service, monkeypatch):
    job = complete(service)
    folder = store.folder(job['id'])
    report = {"pass": False, "style": "illustrated_2d", "content_matches": False,
              "visual_defects": ["Detached flashlight"], "reason": "Flashlight detached.",
              "observations": "Full observation. " * 100}
    store.save(folder / 'still-001.png.review.json', report)
    (folder / 'still-001.png').write_bytes(b'image')

    def call(method, fields, files):
        assert method == 'sendMediaGroup'
        assert len(json.loads(fields['media'])) == 2
        assert files['image'][1].read() == b'image'
        assert json.loads(files['report'][1]) == report
        return [{'message_id': 1}, {'message_id': 2}]

    monkeypatch.setattr(service.api, 'call', call)
    service.send_image_review(101, job, 'still-001.png')


@pytest.mark.parametrize('message,auto,title',[
    ('Generate any type of video', True, 'Choose an original Hindi folk-horror topic'),
    ('Pick a topic yourself', True, 'Choose an original Hindi folk-horror topic'),
        ('pick any topic and start generating video', True, 'Choose an original Hindi folk-horror topic'),
    ('/generate', True, 'Choose an original Hindi folk-horror topic'),
    ('/generate The Old Well', False, 'The Old Well'),
    ('The Gate That Would Not Shut', False, 'The Gate That Would Not Shut'),
])
def test_raghu_distinguishes_topic_requests_and_titles(service, monkeypatch, message, auto, title):
    from app import raghu_pipeline

    monkeypatch.setenv('MEDIA_BACKEND', 'raghu')
    monkeypatch.setenv('TELEGRAM_PRODUCTION_MODE', 'live')
    monkeypatch.setenv('ALLOW_PAID_GENERATION', 'true')
    monkeypatch.setattr(raghu_pipeline, 'production_credentials', lambda: None)
    service.handle(update(message))
    assert store.all_jobs() == []
    service.handle(update('30 seconds', ident=2))
    request = store.all_jobs()[0]['request']
    assert request['title'] == title
    assert request.get('choose_topic', False) is auto


@pytest.mark.parametrize("label,seconds", [("2 minutes", 120), ("5 minutes", 300), ("10 minutes", 600),
                                           ("47 seconds", 47), ("3:20", 200)])
def test_raghu_asks_duration_before_queuing_full_production(service, monkeypatch, label, seconds):
    from app import raghu_pipeline
    monkeypatch.setenv("MEDIA_BACKEND", "raghu")
    monkeypatch.setenv("RAGHU_PLAN_ONLY", "false")
    monkeypatch.setenv("TELEGRAM_PRODUCTION_MODE", "live")
    monkeypatch.setenv("ALLOW_PAID_GENERATION", "true")
    monkeypatch.setattr(raghu_pipeline, "production_credentials", lambda: None)
    service.handle(update("/generate"))
    assert store.all_jobs() == []
    keyboard = service.api.calls[-1][1]["reply_markup"]["keyboard"][0]
    assert {item["text"] for item in keyboard} == set(tg.DURATION_CHOICES)
    # A different chat cannot consume the user's pending question.
    service.handle(update(label, uid=202, ident=2))
    assert store.all_jobs() == []
    if label not in tg.DURATION_CHOICES:
        service.handle(update("Other", ident=9))
        assert service.api.calls[-1][1]["text"] == tg.CUSTOM_DURATION_PROMPT
        assert store.all_jobs() == []
    service.handle(update(label, ident=3))
    service.handle(update(label, ident=3))
    job, = store.all_jobs()
    assert job["request"]["duration_seconds"] == seconds
    assert job["request"]["choose_topic"]
    assert job["request"]["mode"] == "live"
    assert not job["request"].get("raghu_plan_only")


def test_script_choices_owned_selection_and_full_delivery(service, monkeypatch):
    from app import script_options
    service.handle(update())
    ident = tg.latest(service.bot, 101)["id"]
    store.save(store.folder(ident) / "script-options.json", {"options": [
        {"speech": [{"line": "First complete story"}]},
        {"speech": [{"line": "Second complete story"}]}]})
    store.update(ident, status="awaiting_script", stage="Choose script 1 or 2", progress=12)
    queued = []
    monkeypatch.setattr(pipeline, "enqueue", lambda ident: queued.append(ident) or True)
    service.notify()
    assert any("First complete story" in (fields or {}).get("text", "") for _, fields, _ in service.api.calls)
    assert any("Second complete story" in (fields or {}).get("text", "") for _, fields, _ in service.api.calls)
    def pick(uid, update_id):
        service.handle({"update_id": update_id, "callback_query": {
            "id": str(update_id), "data": f"script2:{ident}", "from": {"id": uid},
            "message": {"chat": {"id": uid, "type": "private"}}}})
    pick(202, 2)
    assert not queued
    pick(101, 3)
    assert queued == [ident]
    assert store.get(ident)['script_choice'] == 2
