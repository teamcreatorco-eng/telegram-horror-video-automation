import math
import pytest
from app.raghu_cost import estimate_cost, summarize_events, workload_key


def events():
    return [dict(stage='video_model_ready'), *[
        row for i, seconds in enumerate([62, 53, 55, 54]) for row in
        [dict(stage='video_start', id=str(i), num_frames=121, fps=24),
         dict(stage='video_done', id=str(i), seconds=seconds)]
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
