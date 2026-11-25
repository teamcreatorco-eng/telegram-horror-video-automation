import math
import pytest
from app.raghu_cost import estimate_cost, summarize_events, workload_key, host_signature, measured_seconds, offer_cost


def events():
    return [dict(stage='video_model_ready',time=2,resolution=[1280,768],steps=[8,3],quantization='nvfp4'), *[
        row for i, seconds in enumerate([62, 53, 55, 54]) for row in
        [dict(stage='video_start', time=3+i*2,id=str(i), num_frames=121, fps=24),
         dict(stage='video_done', time=4+i*2,id=str(i), seconds=seconds)]
    ]]


def test_complete_cost_includes_setup_transfer_and_external_cost():
    result = estimate_cost(.7, 120, 50, 600, 50, .001, 2, .01, other_usd=.05)
    assert result['motion_usd'] == pytest.approx(1.166666667)
    assert result['total_usd'] == pytest.approx(1.166666667 + .7 / 6 + .05 + .02 + .05)
    assert result['total_inr'] == pytest.approx(result['total_usd'] * 96)


@pytest.mark.parametrize('bad', [-1, math.nan, math.inf, True])
def test_invalid_cost_inputs_rejected(bad):
    with pytest.raises(ValueError):
        estimate_cost(bad, 120, 50, 600, 50, 0, 2, 0)


def test_download_overlap_uses_union_and_warm_excludes_first():
    rows = events() + [dict(stage='model_download', time=15, seconds=10),
                       dict(stage='model_download', time=20, seconds=10)]
    result = summarize_events(rows)
    assert result['download_wall_seconds'] == 15
    assert result['warm_samples'] == 3
    assert result['warm_p90_seconds'] == 55
    assert result['first_seconds'] == 62


def test_duplicate_or_restarted_sessions_cannot_qualify():
    assert not summarize_events(events() + [events()[-1]])['qualifiable']
    assert not summarize_events(events() + [dict(stage='video_model_ready')])['qualifiable']


def test_workload_key_pins_worker_and_model_precision():
    k = workload_key([{'sha256': 'abc'}], b'approved-worker', 'nvfp4', 'ltx')
    assert k != workload_key([{'sha256': 'abc'}], b'changed-worker', 'nvfp4', 'ltx')
    assert k != workload_key([{'sha256': 'abc'}], b'approved-worker', 'fp8', 'ltx')


def offer(machine=1, rate=.7):
    return dict(machine_id=machine, dph_total=rate, gpu_name='RTX_5090', cpu_name='CPU',
                cpu_cores_effective=8, cpu_ram=64000, gpu_ram=32000, pcie_bw=28,
                inet_down_cost=0, inet_up_cost=0)


def observation(o, seconds=55):
    return dict(host_signature=host_signature(o), workload_key='approved', measured_at=100,
                qualifiable=True,warm_samples=3, warm_p90_seconds=seconds, first_seconds=seconds+7)


def test_more_expensive_hourly_host_can_win_complete_job_cost():
    slow, fast = offer(1,.55), offer(2,.7)
    records=[observation(slow,100), observation(fast,55)]
    costs=[offer_cost(o, records, 'approved', 120, 600, 48, 4, 110) for o in [slow, fast]]
    assert costs[1]['total_usd'] < costs[0]['total_usd']
    assert costs[1]['speed_basis'] == 'measured_host_warm_p90'


def test_unknown_speed_is_explicit_estimate():
    result=offer_cost(offer(), [], 'approved', 120, 600, 48, 4, 110)
    assert result['seconds_per_clip'] == 77
    assert result['speed_basis'] == 'unmeasured_estimate'


@pytest.mark.parametrize('change', [dict(measured_at=-700000), dict(measured_at=200),
    dict(workload_key='other'), dict(warm_samples=2), dict(warm_p90_seconds=float('nan')),
    dict(first_seconds=None)])
def test_unusable_observations_do_not_qualify(change):
    o=offer(); r={**observation(o), **change}
    assert measured_seconds([r, {}, None], o, 'approved', 110) is None


def test_cpu_allocation_change_invalidates_observation():
    o=offer(); r=observation(o)
    assert measured_seconds([r], {**o,'cpu_cores_effective':4}, 'approved', 110) is None
    assert host_signature({}) is None


@pytest.mark.parametrize('change',[{'qualifiable':False},{'warm_p90_seconds':10**10000}])
def test_corrupt_catalog_never_becomes_measured_speed(change):
    o=offer()
    assert measured_seconds([{**observation(o),**change}],o,'approved',110) is None


def test_unordered_or_untimestamped_events_do_not_qualify():
    rows=events();rows[1],rows[2]=rows[2],rows[1]
    assert not summarize_events(rows)['qualifiable']
    assert not summarize_events([{k:v for k,v in r.items() if k!='time'} for r in events()])['qualifiable']
