import json
import os
from pathlib import Path
from app import local_launcher as launcher


def test_local_settings_force_controller_and_never_resume_paid_work(tmp_path):
    (tmp_path/'.env').write_text('PORT=8123\nDATA_DIR=private-data\nHOST=0.0.0.0\nRAGHU_RESUME_ONCE=old\n')
    config=launcher.settings(tmp_path,{'APP_ROLE':'worker','RAGHU_PRODUCE_PLAN_ONCE':'old'})
    assert config['url']=='http://127.0.0.1:8123'
    assert config['data']==tmp_path/'private-data'
    assert config['env']['HOST']=='127.0.0.1' and config['env']['APP_ROLE']=='web'
    assert config['env']['RAGHU_RESUME_ONCE']=='' and config['env']['RAGHU_PRODUCE_PLAN_ONCE']==''


def test_existing_healthy_instance_is_reused_without_second_process(tmp_path,monkeypatch):
    config=launcher.settings(tmp_path,{})
    monkeypatch.setattr(launcher,'health',lambda url:dict(ok=True,local_instance=config['identity']))
    monkeypatch.setattr(launcher.subprocess,'Popen',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Duplicate')))
    opened=[];monkeypatch.setattr(launcher.webbrowser,'open',lambda url:opened.append(url))
    assert launcher.start(config)==config['url']+'/healthz'
    assert opened==[config['url']+'/healthz']


def test_stale_state_starts_hidden_controller_with_private_log(tmp_path,monkeypatch):
    config=launcher.settings(tmp_path,{})
    folder=config['data']/'local-host';folder.mkdir(parents=True)
    (folder/'process.json').write_text(json.dumps(dict(pid=123)))
    monkeypatch.setattr(launcher,'process_alive',lambda pid:False)
    states=iter([None,dict(ok=True,local_instance=config['identity'])])
    monkeypatch.setattr(launcher,'health',lambda url:next(states))
    calls=[]
    class Child:
        pid=456
        def poll(self):return None
    def spawn(command,**kwargs):calls.append((command,kwargs));return Child()
    monkeypatch.setattr(launcher.subprocess,'Popen',spawn)
    monkeypatch.setattr(launcher.webbrowser,'open',lambda url:None)
    launcher.start(config)
    command,options=calls[0]
    assert command[1:]==['run.py'] and options['cwd']==str(tmp_path)
    assert options['stdout'].name.endswith('server.log')
    assert json.loads((folder/'process.json').read_text())['pid']==456


def test_foreign_service_on_port_is_not_treated_as_our_bot(tmp_path,monkeypatch):
    import pytest
    config=launcher.settings(tmp_path,{})
    monkeypatch.setattr(launcher,'health',lambda url:dict(ok=True,local_instance='different'))
    with pytest.raises(RuntimeError,match='another application'):launcher.start(config)


def test_process_check_is_read_only_and_does_not_use_kill(monkeypatch):
    def unsafe(*a):raise AssertionError('Process checks must never send a signal')
    monkeypatch.setattr(launcher.os,'kill',unsafe)
    assert launcher.process_alive(os.getpid())


def test_recycled_pid_is_not_mistaken_for_previous_controller(tmp_path,monkeypatch):
    config=launcher.settings(tmp_path,{})
    folder=config['data']/'local-host';folder.mkdir(parents=True)
    (folder/'process.json').write_text(json.dumps(dict(pid=123,identity=config['identity'],fingerprint='old-process')))
    monkeypatch.setattr(launcher,'process_fingerprint',lambda pid:'new-process')
    states=iter([None,dict(ok=True,local_instance=config['identity'])])
    monkeypatch.setattr(launcher,'health',lambda _:next(states))
    class Child:
        pid=456
        def poll(self):return None
    monkeypatch.setattr(launcher.subprocess,'Popen',lambda *a,**k:Child())
    monkeypatch.setattr(launcher.webbrowser,'open',lambda _:None)
    assert launcher.start(config)==config['url']+'/healthz'
