import json
from pathlib import Path
from app import raghu_cost as cost


def test_setup_measurement_subtracts_inference_and_requires_complete_phases():
    events = [
        dict(stage='worker_phase_start', phase='stills', phase_id='a', time=10),
        dict(stage='still_done', seconds=4, time=19),
        dict(stage='worker_phase_done', phase='stills', phase_id='a', time=30, seconds=20, exit_code=0),
        dict(stage='worker_phase_start', phase='voice', phase_id='b', time=31),
        dict(stage='narrator_paragraph_ready', seconds=3, time=38),
        dict(stage='worker_phase_done', phase='voice', phase_id='b', time=41, seconds=10, exit_code=0),
        dict(stage='worker_phase_start', phase='prepare-motion', phase_id='c', time=42),
        dict(stage='worker_phase_done', phase='prepare-motion', phase_id='c', time=52, seconds=10, exit_code=0),
    ]
    assert cost.measured_setup(events) == 33
    assert cost.measured_setup(events[:-1]) is None
    events[-1]['exit_code'] = 1
    assert cost.measured_setup(events) is None


def test_completed_phase_metadata_is_written_by_worker():
    source=Path('app/raghu_assets/worker.sh').read_text()
    assert 'phase-timing.py' in source
    assert 'trap' in source and 'EXIT' in source


def test_identical_images_are_uploaded_once_per_session_and_changes_reupload(tmp_path):
    from types import SimpleNamespace
    from app.raghu_pipeline import upload_saved_images
    calls = []
    remote = SimpleNamespace(push=lambda files:calls.append([p.name for p in files]),
                             phase=lambda *a:None)
    image = tmp_path/'still-001.png'
    image.write_bytes(b'first')
    upload_saved_images(remote,[image])
    upload_saved_images(remote,[image])
    image.write_bytes(b'changed')
    upload_saved_images(remote,[image])
    assert calls == [['still-001.png'],['still-001.png']]


def test_offer_uses_qualified_complete_setup_measurement():
    import time
    from app.raghu_cost import offer_cost,host_signature
    now=time.time()
    offer=dict(machine_id=1,gpu_name='RTX 5090',cpu_name='cpu',cpu_cores_effective=8,
               cpu_ram=64000,gpu_ram=32000,pcie_bw=24,dph_total=.6,inet_down_cost=0,inet_up_cost=0)
    record=dict(qualifiable=True,host_signature=host_signature(offer),workload_key='k',
                measured_at=now,warm_samples=3,warm_p90_seconds=77,first_seconds=80,
                total_setup_seconds=3600, nonmotion_seconds_per_clip=2)
    result=offer_cost(offer,[record],'k',120,1500,60,1,now)
    assert result['overhead_usd'] == (3600+240+3)*.6/3600
    assert result['setup_basis'] == 'measured_complete_setup'
    record['total_setup_seconds']=-1
    assert offer_cost(offer,[record],'k',120,1500,60,1,now)['setup_basis']=='unmeasured_estimate'


def test_reference_transfer_failure_is_not_cached(tmp_path):
    from types import SimpleNamespace
    import pytest
    from app.raghu_pipeline import upload_saved_images
    image=tmp_path/'still-001.png'
    image.write_bytes(b'png')
    calls=[]
    def fail(*args):
        raise RuntimeError('move failed')
    remote=SimpleNamespace(push=lambda files:calls.append(files),phase=fail)
    with pytest.raises(RuntimeError):
        upload_saved_images(remote,[image])
    remote.phase=lambda *args:None
    upload_saved_images(remote,[image])
    assert len(calls)==2


def test_setup_history_is_not_shared_across_narration_or_image_models(monkeypatch):
    from app import raghu_vast as vast
    monkeypatch.setattr(vast,'model_manifest',lambda _:[])
    monkeypatch.setenv('RAGHU_IMAGE_MODEL','flux')
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER','none')
    silent=vast.cost_workload_key('nvfp4','ltx')
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER','chatterbox')
    voiced=vast.cost_workload_key('nvfp4','ltx')
    assert silent != voiced
    monkeypatch.setenv('RAGHU_IMAGE_MODEL','qwen2511')
    assert vast.cost_workload_key('nvfp4','ltx') != voiced


def test_setup_boundary_avoids_overlap_and_rejects_partial_asset_runs(tmp_path):
    from app import store
    from test_raghu_cost import events, offer
    phase_events = [
        dict(stage='worker_phase_start', phase='stills', phase_id='a', time=10),
        dict(stage='still_done', seconds=4, time=19),
        dict(stage='worker_phase_done', phase='stills', phase_id='a', time=30, seconds=20, exit_code=0),
        dict(stage='worker_phase_start', phase='voice', phase_id='b', time=31),
        dict(stage='narrator_paragraph_ready', seconds=3, time=38),
        dict(stage='worker_phase_done', phase='voice', phase_id='b', time=41, seconds=10, exit_code=0),
        dict(stage='worker_phase_start', phase='prepare-motion', phase_id='c', time=42),
        dict(stage='worker_phase_done', phase='prepare-motion', phase_id='c', time=52, seconds=10, exit_code=0)]
    rows=phase_events + [dict(r,time=r['time']+100) for r in events()]
    (tmp_path/'events.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    (tmp_path/'rental-operations.jsonl').write_text(json.dumps(dict(program='scp',time=25,seconds=10,status=0)))
    state=dict(started=1,rate=.7,workload_key='k',host_signature=cost.host_signature(offer()),
               motion_model='ltx',quantization='nvfp4',id=1,setup_scope='cold-assets',
               narration_provider='chatterbox',ssh_ready_seconds=3)
    store.save(tmp_path/'raghu-rental.json',state)
    result=cost.record_rental_cost(tmp_path,tmp_path/'catalog',111)
    assert result['total_setup_seconds'] == 95
    assert result['nonmotion_seconds_per_clip'] == 7/4
    state['setup_scope']='saved-assets'
    store.save(tmp_path/'raghu-rental.json',state)
    result=cost.record_rental_cost(tmp_path,tmp_path/'catalog',111)
    assert result['total_setup_seconds'] is None
    state['setup_scope']='cold-assets'
    state['resume_count']=1
    store.save(tmp_path/'raghu-rental.json',state)
    result=cost.record_rental_cost(tmp_path,tmp_path/'catalog',111)
    assert not result['qualifiable']
    assert result['total_setup_seconds'] is None
