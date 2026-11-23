from app import raghu_narration, raghu_music


def test_every_character_line_uses_the_narrator():
    shots = [dict(id='001', narration='वह बोली, यहाँ मत आना।', speaker='GIRL', dialogue_voice='female')]
    assert raghu_narration.narrator_line(shots[0])
    assert raghu_narration.narration_lines(shots)[0]['speaker'] == 'NARRATOR'


def test_catalog_is_private_and_selection_does_not_leak_to_next_job(tmp_path, monkeypatch):
    monkeypatch.setattr(raghu_music.store, 'DATA', tmp_path)
    folder = tmp_path / '_music/catalog'
    folder.mkdir(parents=True)
    (folder / 'ghostly-piano.mp3').write_bytes(b'music')
    previews = folder / 'previews'
    previews.mkdir()
    (previews / 'ghostly-piano.mp4').write_bytes(b'preview')
    choices = raghu_music.choices()
    assert choices[0]['id'] == 'ghostly-piano'
    assert raghu_music.source('ghostly-piano') == folder / 'ghostly-piano.mp3'
    assert raghu_music.source('none') is None


def test_character_quote_has_attribution_without_a_voice_switch():
    import json
    from app.clone_chat_planner import narrator_only_story
    raw = json.dumps(dict(characters=[dict(tag='GIRL', name='मीरा')], speech=[
        dict(speaker='GIRL', line='दरवाज़ा मत खोलना।')]), ensure_ascii=False)
    value = json.loads(narrator_only_story(raw))
    assert value['speech'][0] == dict(speaker='NARRATOR', line='मीरा ने कहा, दरवाज़ा मत खोलना।')
    assert json.loads(narrator_only_story(json.dumps(value))) == value


def test_invalid_story_structure_is_left_to_existing_repair():
    import json
    from app.clone_chat_planner import narrator_only_story
    for value in [dict(characters=None,speech=[]), dict(characters=[{}],speech=[]),
                  dict(characters=[],speech=[dict(line='bad')])]:
        raw=json.dumps(value)
        assert narrator_only_story(raw)==raw


def test_explicit_no_music_ignores_unrelated_missing_default(tmp_path, monkeypatch):
    from app.raghu_pipeline import validate_music_for_request
    monkeypatch.setenv('RAGHU_USER_BGM_FILE', str(tmp_path/'missing.wav'))
    validate_music_for_request({'music_track':'none'})


def test_locked_legacy_speech_is_not_rewritten(tmp_path, monkeypatch):
    import json
    from app import clone_chat_planner as adapter
    value=dict(characters=[dict(tag='GIRL', name='मीरा')], speech=[dict(speaker='GIRL', line='कौन है?')])
    monkeypatch.setattr(adapter.raghu_services,'required',lambda _: 'fixture')
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'choices':[{'message':{'content':json.dumps(value)},'finish_reason':'stop'}]}
    monkeypatch.setattr(adapter.httpx,'post',lambda *a,**kw:Response())
    writer=adapter.DeepSeekWriter(tmp_path,lambda:None,lambda *a:None)
    content=writer.complete('the exact title is the only story input\nThis is a saved, approved script.',
                            max_tokens=100,temperature=0)
    assert json.loads(content)==value
