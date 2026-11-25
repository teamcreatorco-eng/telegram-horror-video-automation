import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import raghu_pipeline, raghu_vast, storage, store, worker


def test_one_broken_rental_record_does_not_block_other_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    store.init()
    broken, interrupted, active = [store.create({}, ident=n * 32) for n in "abc"]
    for ident in (broken, interrupted):
        store.update(ident, status="paused")
    (store.folder(broken) / "raghu-rental.json").write_text("{")
    for ident in (interrupted, active):
        store.save(store.folder(ident) / "raghu-rental.json", {"id": 1, "label": ident})
    stopped = []
    monkeypatch.setattr(raghu_vast, "close_rental", lambda journal, preserve: stopped.append((journal.parent.name, preserve)))
    monkeypatch.setattr(raghu_vast, "destroy", lambda *_: pytest.fail("unexpected destroy"))
    raghu_vast.startup_cleanup()
    assert stopped == [(interrupted, True)]
    assert "billing status" in store.get(broken)["error"]


def test_queue_creation_can_recover_folder_after_interruption(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store.init()
    ident = store.create({})
    store.folder(ident).rmdir()
    assert store.create({}, ident=ident) == ident
    assert store.folder(ident).is_dir()


def test_downloaded_checkpoint_must_match_before_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setenv("S3_BUCKET", "fixture")
    ident = "a" * 32
    folder = store.folder(ident)
    folder.mkdir()
    clip = folder / "clip.mp4"
    clip.write_bytes(b"older")
    record = {"name": clip.name, "digest": hashlib.sha256(b"new clip").hexdigest(), "object_key": "k"}
    monkeypatch.setattr(storage, "records", lambda _: [record])
    contents = iter([b"partial", b"new clip"])
    monkeypatch.setattr(storage, "client", lambda: SimpleNamespace(download_file=lambda bucket, key, path: Path(path).write_bytes(next(contents))))
    with pytest.raises(RuntimeError, match="checksum"):
        storage.hydrate(ident)
    assert clip.read_bytes() == b"older"
    assert list(folder.glob("*.tmp")) == []
    storage.hydrate(ident)
    assert clip.read_bytes() == b"new clip"


def test_unavailable_timing_file_does_not_block_motion(tmp_path):
    def missing(*args):
        raise RuntimeError("missing events")
    raghu_pipeline.collect_motion_events(SimpleNamespace(pull=missing), tmp_path)


def test_finished_film_survives_worker_cache_cleanup_error(tmp_path, monkeypatch):
    from app import pipeline
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    store.init()
    ident = store.create({})
    store.update(ident, status="running", worker="mine")
    monkeypatch.setattr(pipeline, "work", lambda ident, lease: store.update(ident, status="complete"))
    def locked(*args):
        raise PermissionError("cache file locked")
    monkeypatch.setattr(worker.shutil, "rmtree", locked)
    worker.execute((ident, "mine", SimpleNamespace(close=lambda: None)))
    assert store.get(ident)["status"] == "complete"


def test_resume_reconciles_existing_connected_rental_after_controller_crash(tmp_path, monkeypatch):
    journal = tmp_path / "raghu-rental.json"
    store.save(journal, {"id": 42, "label": "old", "motion_model": "ltx", "quantization": "nvfp4",
                         "ssh_ready_seconds": 2})
    row = {"id": 42, "label": "old", "actual_status": "running"}
    calls = []
    monkeypatch.setattr(raghu_vast, "instances", lambda: [row])
    monkeypatch.setenv("VAST_SSH_KEY_PATH", str(tmp_path / "key"))
    monkeypatch.setattr(raghu_vast, "motion_model", lambda: "ltx")
    def stop(action, ident):
        calls.append((action, ident))
        row["actual_status"] = "stopped"
    monkeypatch.setattr(raghu_vast, "request_instance_state", stop)
    monkeypatch.setattr(raghu_vast.Remote, "restart_preserved", lambda self, state: calls.append(("resume", state["id"])))
    monkeypatch.setattr(raghu_vast.Remote, "push", lambda *args: None)
    monkeypatch.setattr(raghu_vast.Remote, "phase", lambda *args: None)
    monkeypatch.setattr(raghu_vast, "finish_rental", lambda *args: None)
    with raghu_vast.rent(tmp_path, "old", lambda: None):
        assert calls == [("stop", 42), ("resume", 42)]


def test_failure_after_ssh_connected_preserves_unmarked_disk(tmp_path, monkeypatch):
    journal = tmp_path / "raghu-rental.json"
    store.save(journal, {"id": 42, "ssh_ready_seconds": 2})
    calls = []
    monkeypatch.setattr(raghu_vast, "close_rental", lambda journal, preserve: calls.append(preserve))
    with pytest.raises(ValueError, match="original"):
        try:
            raise ValueError("original")
        finally:
            raghu_vast.finish_rental(journal, False)
    assert calls == [True]


def test_uploads_only_this_batch_of_references(tmp_path):
    calls = []
    remote = SimpleNamespace(push=lambda paths: calls.append([p.name for p in paths]),
                             phase=lambda cmd, seconds: calls.append(cmd))
    raghu_pipeline.upload_saved_images(remote, [])
    raghu_pipeline.upload_saved_images(remote, [tmp_path / "background-HALL.png"])
    assert calls == [["background-HALL.png"], "mv -- background-HALL.png results/"]


def test_displaced_worker_cannot_mark_reassigned_job_failed_or_cancelled(tmp_path, monkeypatch):
    import threading
    from app import pipeline
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MEDIA_BACKEND", "raghu")
    store.init()
    ident = store.create({"mode":"live"})
    store.update(ident, status="running", worker="first")
    def take_over(_):
        store.update(ident, worker="second", stage="On second worker")
        raise RuntimeError("Old worker storage error")
    monkeypatch.setattr(storage, "sync", take_over)
    pipeline.work(ident, lease=("first", threading.Event()))
    job = store.get(ident)
    assert (job["worker"], job["status"], job["stage"], job["error"]) == ("second", "running", "On second worker", None)


def test_missing_worker_asset_is_reported_before_rental(tmp_path, monkeypatch):
    monkeypatch.setattr(raghu_pipeline, "ASSETS", tmp_path)
    monkeypatch.setenv("RAGHU_NARRATION_PROVIDER", "none")
    with pytest.raises(RuntimeError, match="worker.sh.*No GPU was rented"):
        raghu_pipeline.generation_preflight({"shots":[]})
