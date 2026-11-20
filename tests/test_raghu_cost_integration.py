import json
import pytest
from app import raghu_vast as vast, raghu_cost as cost, store
from test_raghu_cost import events, offer


def prepare(folder):
    state=dict(rate=.7, started=1, workload_key='approved', host_signature=cost.host_signature(offer()),
               id=1, motion_model='ltx', quantization='nvfp4', label='test')
    store.save(folder/'raghu-rental.json',state)
    (folder/'events.jsonl').write_text('\n'.join(json.dumps(r) for r in events()))


def test_cleanup_records_compatible_events_first(tmp_path, monkeypatch):
    prepare(tmp_path)
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setenv('RAGHU_DESTROY_ON_SUCCESS','false')
    calls=[]
    def close(*args):
        assert (tmp_path/'rental-cost-observation.json').exists()
        calls.append(args)
    monkeypatch.setattr(vast,'close_rental',close)
    vast.finish_rental(tmp_path/'raghu-rental.json',False)
    assert len(calls)==1


def test_corrupt_telemetry_cannot_prevent_cleanup(tmp_path, monkeypatch):
    prepare(tmp_path)
    (tmp_path/'events.jsonl').write_text('broken json')
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setenv('RAGHU_DESTROY_ON_SUCCESS','false')
    calls=[]
    monkeypatch.setattr(vast,'close_rental',lambda *a:calls.append(a))
    vast.finish_rental(tmp_path/'raghu-rental.json',False)
    assert len(calls)==1


def test_observation_reports_elapsed_estimate_not_invoice(tmp_path):
    prepare(tmp_path)
    result=cost.record_rental_cost(tmp_path,tmp_path/'catalog',101)
    assert result['elapsed_compute_estimate_usd']==pytest.approx(.7*100/3600)
    assert result['invoice_verified'] is False
    assert result['qualifiable'] is True
    assert len(cost.load_observations(tmp_path/'catalog'))==1
    cost.record_rental_cost(tmp_path,tmp_path/'catalog',102)
    assert len(cost.load_observations(tmp_path/'catalog'))==1


def test_corrupt_catalog_falls_back_without_clobbering_files(tmp_path):
    (tmp_path/'broken.json').write_text('no json')
    assert cost.load_observations(tmp_path)==[]
    assert (tmp_path/'broken.json').read_text()=='no json'


def test_worker_workload_signature_accounts_for_runtime(monkeypatch):
    monkeypatch.setattr(vast,'model_manifest',lambda _:[])
    a=vast.cost_workload_key('nvfp4','ltx')
    monkeypatch.setattr(vast,'RUNTIME_IMAGE','another-image')
    assert vast.cost_workload_key('nvfp4','ltx') != a


def test_unavailable_offer_does_not_attach_first_hosts_speed_to_replacement(monkeypatch,tmp_path):
    from test_rental_creation import setup
    run, calls, state = setup(monkeypatch,tmp_path,[(409,{'success':False,'msg':'Offer already rented'}),
                                                  (200,{'success':True,'new_contract':99})])
    state.update(workload_key='approved',host_signature='first-host')
    assert run()==99
    # Fixture's replacement has no host fingerprint fields: it must remain unknown.
    assert state['host_signature'] is None


def test_host_telemetry_keeps_only_hardware_fields():
    result=cost.host_metadata({**offer(), 'api_key':'private', 'env':{'TOKEN':'private'}})
    assert result['cpu_cores_effective']==8
    assert 'api_key' not in result and 'env' not in result
