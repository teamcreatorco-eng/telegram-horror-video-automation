import json
import pytest
from app import raghu_pipeline as pipeline, clone_chat_planner as adapter, youtube_clone_writer as legacy, store
from app.youtube_clone.pipeline import planning_writer as writer


@pytest.mark.parametrize('seconds', [30, 120, 300, 600])
@pytest.mark.parametrize('legacy_checkpoint', [False, True])
def test_every_duration_routes_to_approved_planner(tmp_path, monkeypatch, seconds, legacy_checkpoint):
    if legacy_checkpoint:
        store.save(tmp_path / 'youtube-clone-script.json', {'beats': []})
    monkeypatch.setattr(legacy, 'enabled', lambda _: True)
    calls = []
    monkeypatch.setattr(adapter, 'plan', lambda *a, **k: calls.append(k['seconds']) or {'script': 'test', 'shots': []})
    monkeypatch.setattr(legacy, 'plan', lambda *a, **k: pytest.fail('Older planner selected'))
    pipeline.work('a'*32, tmp_path, dict(title='Test', duration_seconds=seconds, raghu_plan_only=True), lambda: None, lambda *a: None)
    assert calls == [seconds]


def test_ten_minute_canonical_plan_uses_120_scenes_without_review(tmp_path):
    story = dict(entity='shadow', characters=[dict(tag='@CHAR-01-ASHA', name='आशा')],
                 speech=[dict(speaker='NARRATOR', line='वह दरवाज़े की ओर धीरे चली।') for _ in range(120)])
    assets = dict(characters=[dict(**story['characters'][0], look_summary='Adult woman in yellow', voice_description='Hindi woman')],
                  backgrounds=[dict(tag='#BGD-01-ROOM', location_name='Room', look_summary='Empty room')])
    record = dict(location='#BGD-01-ROOM', view='inside room', camera='locked medium',
                  visible=[dict(tag='@CHAR-01-ASHA', pose='standing', end_pose='one hand raised', face_visible=True)],
                  offscreen=[], objects=[], static_surfaces=[],
                  action=dict(kind='reaction', actor='@CHAR-01-ASHA', target=None, movement='raises one hand', reveal_face=False))
    scenes = dict(scenes=[dict(scene_number=i, title='कमरा', sound_design='quiet', scene_record=record) for i in range(1,121)])
    class Model:
        def __init__(self): self.answers=iter([story, assets, scenes]);self.calls=[]
        def complete(self, prompt, **kwargs):
            self.calls.append(prompt)
            return json.dumps(next(self.answers), ensure_ascii=False)
    model=Model()
    result=writer.plan_title('खाली कमरा',600,project_id='test',model=model)
    assert len(result['scenes'])==120 and len(model.calls)==3
    assert result['planning_review']==[]


def test_selected_legacy_script_is_carried_into_latest_planner(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter.raghu_services,'required',lambda _: 'fixture')
    store.save(tmp_path/'youtube-clone-script.json',dict(entity='shadow',beats=[dict(speaker='NARRATOR',speech='वह रुका।')]))
    model=adapter.DeepSeekWriter(tmp_path,lambda:None,lambda *a:None)
    prompts=[]
    answer=dict(entity='shadow',characters=[dict(tag='@CHAR-01-ASHA',name='आशा')],speech=[dict(speaker='NARRATOR',line='वह रुका।')])
    monkeypatch.setattr(model,'complete',lambda prompt,**kw: prompts.append(prompt) or json.dumps(answer,ensure_ascii=False))
    assert model.select_story('story',lambda v: None,1000)['speech']==answer['speech']
    assert 'वह रुका।' in prompts[0]
