import json
import pytest
from app.youtube_clone.pipeline import planning_writer as writer


def story(count):
    return dict(entity='छाया',characters=[dict(tag='@CHAR-01-MEERA',name='मीरा')],
                speech=[dict(speaker='NARRATOR',line='उसने दरवाज़े पर परछाईं देखी।') for _ in range(count)])


def test_count_failure_reports_actual_returned_count():
    with pytest.raises(ValueError,match=r'120 ordered speech beats; received 118'):
        writer.check_story(story(118),120,1300,1550,editorial_checks=False)


def test_retry_receives_rejected_draft_and_repairs_its_structure():
    broken=story(118)
    good=story(120)
    prompts=[]
    class Model:
        def complete(self,prompt,**kwargs):
            prompts.append(prompt)
            if len(prompts)==1:return json.dumps(broken,ensure_ascii=False)
            assert 'received 118' in prompt
            assert 'Rejected JSON draft' in prompt
            assert json.dumps(broken,ensure_ascii=False) in prompt
            return json.dumps(good,ensure_ascii=False)
    result=writer.ask(Model(),'Write a complete story.',lambda v:writer.check_story(
        v,120,1300,1550,editorial_checks=False),22000)
    assert result==good
    assert len(prompts)==2


def test_scene_repair_receives_the_actual_rejected_scene_data():
    broken = {"scenes":[{"character":"UNKNOWN"}]}
    fixed = {"scenes":[{"character":"MEERA"}]}
    prompts = []
    class Model:
        def complete(self, prompt, **kwargs):
            prompts.append(prompt)
            return json.dumps(broken if len(prompts)==1 else fixed)
    def validate(value):
        if value != fixed:
            raise ValueError("Unknown character UNKNOWN")
    assert writer.ask(Model(), "Stage saved script", validate, 1000) == fixed
    assert json.dumps(broken) in prompts[1]
