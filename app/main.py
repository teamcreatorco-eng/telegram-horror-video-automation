"""Health-only HTTP service; Telegram is the product interface."""
import asyncio,json,logging,os,threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from . import store,auth,telegram_bot


def queue_saved_story(ident):
    from . import pipeline, raghu_pipeline

    job = store.get(ident)
    if not job or job["status"] != "complete" or not telegram_bot.plan_only_job(job):
        return
    if os.getenv("ALLOW_PAID_GENERATION", "").lower() != "true":
        logging.warning("Saved story %s was not queued because paid generation is disabled", ident)
        return
    try:
        raghu_pipeline.production_credentials()
        saved = raghu_pipeline.saved_production_plan(store.folder(ident))
    except (RuntimeError, ValueError, OSError) as exc:
        logging.warning("Saved story %s was not queued: %s", ident, exc)
        return
    request = {**job["request"], "title": saved["title"],
               "raghu_plan_only": False, "raghu_produce_saved": True}
    with store.db() as connection:
        changed = connection.execute(
            "UPDATE jobs SET request=?,status='paused',stage='Approved storyboard saved; preparing production' "
            "WHERE id=? AND status='complete' AND stage=?",
            (json.dumps(request, ensure_ascii=False), ident, job["stage"])).rowcount
        if not changed:
            return
        connection.execute(
            "UPDATE telegram_jobs SET delivery='pending',message_id=NULL,last_text=NULL,notified=0 WHERE job_id=?",
            (ident,))
    if pipeline.enqueue(ident):
        logging.warning("Queued production from approved storyboard %s (%s shots)", ident, len(saved["shots"]))


@asynccontextmanager
async def lifespan(app):
    store.init();auth.init();telegram_bot.init()
    if os.getenv('MEDIA_BACKEND', '').lower() == 'raghu':
        from .raghu_vast import startup_cleanup
        await asyncio.to_thread(startup_cleanup)
        saved_id = os.getenv("RAGHU_PRODUCE_PLAN_ONCE", "").strip()
        if len(saved_id) == 32 and all(c in "0123456789abcdef" for c in saved_id):
            await asyncio.to_thread(queue_saved_story, saved_id)
        resume_id, _, retry = os.getenv("RAGHU_RESUME_ONCE", "").strip().partition(":")
        if len(resume_id) == 32 and all(c in "0123456789abcdef" for c in resume_id) and (not retry or retry.isdecimal()):
            from . import pipeline
            suffix = f"-{int(retry)}" if retry else ""
            marker = store.folder(resume_id) / f"operator-resume{suffix}.started"
            job = store.get(resume_id)
            if job and job["status"] in ("failed", "paused", "cancelled") and not marker.exists():
                marker.touch(exist_ok=False)
                with store.db() as connection:
                    connection.execute("UPDATE telegram_jobs SET delivery='pending',last_text=NULL WHERE job_id=?",
                                       (resume_id,))
                logging.warning("Resuming saved production from stage %s", job["stage"])
                pipeline.enqueue(resume_id)
                logging.warning("Resumed saved production %s", resume_id)
    stop=threading.Event()
    thread=threading.Thread(target=telegram_bot.supervise,args=(stop,),daemon=True,name='telegram')
    thread.start()
    yield
    stop.set();thread.join(timeout=15)

app=FastAPI(title='Nightfall Telegram Backend',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

@app.get('/healthz')
def health():return {'ok':True,'interface':'telegram','telegram_configured':bool(os.getenv('TELEGRAM_BOT_TOKEN')),'telegram_state':telegram_bot.STATE,
                     'local_instance':os.getenv('NIGHTFALL_LOCAL_INSTANCE')}
