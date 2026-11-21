import importlib.util
import json
from pathlib import Path
import pytest
from app import raghu_pipeline as pipeline, raghu_vast as vast, store


def recovery():
    path=pipeline.ASSETS/'motion_recovery.py'
    spec=importlib.util.spec_from_file_location('test_recovery',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_remote_result_reuses_matching_input_and_invalidates_changed_input(tmp_path, monkeypatch):
    worker=recovery();results=tmp_path/'results';results.mkdir()
    target=results/'shot-001.mp4';target.write_bytes(b'completed video')
    shot=dict(id='001',recovery_key='a'*64)
    worker.mark_complete(results,shot)
    monkeypatch.setattr(worker,'video_readable',lambda path:True)
    assert worker.reusable(results,shot)
    request=tmp_path/'motion-request.staged';request.write_text(json.dumps({'shots':[shot]}))
    worker.prepare(tmp_path,request)
    assert target.read_bytes()==b'completed video'
    request.write_text(json.dumps({'shots':[dict(shot,recovery_key='b'*64)]}))
    worker.prepare(tmp_path,request)
    assert not target.exists()


def test_corrupt_remote_clip_is_not_reused(tmp_path, monkeypatch):
    worker=recovery();target=tmp_path/'shot-001.mp4';target.write_bytes(b'clip')
    shot=dict(id='001',recovery_key='a'*64);worker.mark_complete(tmp_path,shot)
    target.write_bytes(b'truncated')
    monkeypatch.setattr(worker,'video_readable',lambda path:True)
    assert not worker.reusable(tmp_path,shot)


def test_old_queue_requests_are_parked_without_deleting_results(tmp_path):
    worker=recovery();(tmp_path/'results').mkdir()
    (tmp_path/'results/shot-001.mp4').write_bytes(b'completed')
    (tmp_path/'motion-request-old.json').write_text('{}')
    worker.park_requests(tmp_path)
    assert not (tmp_path/'motion-request-old.json').exists()
    assert (tmp_path/'completed-motion-requests/motion-request-old.json').is_file()
    assert (tmp_path/'results/shot-001.mp4').is_file()


def test_completed_local_sources_reconcile_preserved_rental(tmp_path, monkeypatch):
    monkeypatch.setenv('RAGHU_DESTROY_ON_SUCCESS','true')
    monkeypatch.setattr(pipeline,'motion_model',lambda:'ltx')
    monkeypatch.setattr(pipeline,'uses_ending_image',lambda:False)
    monkeypatch.setattr(pipeline,'assess_still',lambda *a: {'pass':True})
    monkeypatch.setattr(pipeline,'validate_motion',lambda *a: {'pass':True})
    monkeypatch.setattr(pipeline,'still_pair_checks',lambda *a:{})
    monkeypatch.setattr(pipeline.raghu_narration,'pending_clone',lambda *a:[])
    monkeypatch.setattr(pipeline,'still_batches',lambda *a:([],[]))
    (tmp_path/'shot-001.mp4').write_bytes(b'validated by fake')
    journal=tmp_path/'raghu-rental.json';store.save(journal,dict(id=42,destroyed=False,recovery_stop=True))
    calls=[];monkeypatch.setattr(vast,'destroy',lambda path:calls.append(path))
    pipeline.generate(tmp_path,'a'*32,{'shots':[dict(id='001')],'cast':[],'locations':[]},lambda:None,lambda *a:None)
    assert calls==[journal]


def test_resume_refreshes_hf_credential(tmp_path,monkeypatch):
    monkeypatch.setenv('VAST_SSH_KEY_PATH',str(tmp_path/'key'))
    monkeypatch.setenv('HF_TOKEN','new-placeholder')
    state=dict(id=42,label='job',recovery_stop=True,quantization='nvfp4')
    store.save(tmp_path/'raghu-rental.json',state)
    monkeypatch.setattr(vast,'motion_model',lambda:'ltx');monkeypatch.setattr(vast,'image_model',lambda:'flux')
    monkeypatch.setattr(vast,'instances',lambda:[dict(id=42,label='job',actual_status='stopped')])
    monkeypatch.setattr(vast,'Remote',type('Remote',(),dict(__init__=lambda self,*a,**k:None,
        restart_preserved=lambda *a:None,push=lambda self,paths:uploaded.extend((p.name,p.read_text(encoding='utf-8')) for p in paths),phase=lambda *a:None)))
    monkeypatch.setattr(vast,'finish_rental',lambda *a:None)
    uploaded=[]
    with vast._rent_once(tmp_path,'job',lambda:None,progress=lambda *a:None):pass
    assert any(name=='.env' and 'new-placeholder' in data for name,data in uploaded)
