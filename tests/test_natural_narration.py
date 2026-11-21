import hashlib
import json
import shutil
import wave
from pathlib import Path

import pytest

from app import media, raghu_pipeline, raghu_narration, store


@pytest.mark.parametrize('music', [False, True])
def test_narrated_intro_export_has_compatible_audio_streams(tmp_path, monkeypatch, music):
    if not media.available(): pytest.skip('FFmpeg required')
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER', 'edge')
    monkeypatch.setenv('RAGHU_MOTION_MODEL', 'ltx')
    monkeypatch.setenv('RAGHU_LTX_END_FRAME', 'false')
    intro=tmp_path/'intro.mp4'
    media.ff('-f','lavfi','-i','color=c=black:s=64x64:r=24','-f','lavfi','-i','anullsrc=r=48000:cl=stereo',
             '-t','6','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',intro)
    media.ff('-f','lavfi','-i','color=c=blue:s=64x64:r=24','-t','1','-an','-c:v','libx264',
             '-pix_fmt','yuv420p',tmp_path/'shot-001.mp4')
    voice=raghu_narration.narrator_folder(tmp_path);voice.mkdir()
    media.ff('-f','lavfi','-i','sine=frequency=440:sample_rate=24000','-t','1',voice/'narration-001.wav')
    monkeypatch.setattr(raghu_pipeline,'opening_path',lambda:intro)
    monkeypatch.setattr(raghu_pipeline.raghu_music,'source',lambda:None)
    raghu_pipeline.edit(tmp_path,{'shots':[dict(id='001',narration='वह रुका।')]},lambda:None,music=music,opening=True)
    final=tmp_path/'final.mp4'
    audio=next(s for s in media.probe(final)['streams'] if s['codec_type']=='audio')
    assert audio['sample_rate']=='48000' and audio['channels']==2
    assert abs(media.duration(final)-7)<.15


def test_editor_follows_speech_without_retiming_or_removing_breaths(tmp_path, monkeypatch):
    if not media.available():
        pytest.skip('FFmpeg required')
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER', 'edge')
    monkeypatch.setenv('RAGHU_MOTION_MODEL', 'ltx')
    monkeypatch.setenv('RAGHU_LTX_END_FRAME', 'false')
    voice = raghu_narration.narrator_folder(tmp_path)
    voice.mkdir()
    for ident, duration in [('001', .6), ('002', 1.4)]:
        media.ff('-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24', '-t', '1',
                 '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', tmp_path / f'shot-{ident}.mp4')
        media.ff('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=24000', '-t', duration,
                 voice / f'narration-{ident}.wav')
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in voice.glob('*.wav')}
    calls = []
    original = media.ff
    def ff(*args, **kwargs):
        calls.append(' '.join(map(str, args)))
        return original(*args, **kwargs)
    monkeypatch.setattr(media, 'ff', ff)
    shots = [{'id': ident, 'narration': 'एक पूरी पंक्ति।'} for ident in ('001', '002')]
    raghu_pipeline.edit(tmp_path, {'shots': shots, 'requested_seconds': 10}, lambda: None,
                        music=False, opening=False)
    timeline = store.read(tmp_path / 'raghu-timeline.json')['shots']
    assert abs(timeline[0]['seconds'] - .6) <= 1/24
    assert abs(timeline[1]['seconds'] - 1.4) <= 1/24
    assert abs(media.duration(tmp_path / 'final.mp4') - 2) < .12
    assert not any('atempo' in call or 'silenceremove' in call for call in calls)
    assert any('tpad' in call for call in calls)
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in voice.glob('*.wav')}


def test_pending_clone_requires_continuous_manifest_not_legacy_per_shot_files(tmp_path, monkeypatch):
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER', 'chatterbox')
    folder = raghu_narration.clone_folder(tmp_path)
    folder.mkdir()
    (folder / 'narration-001.wav').write_bytes(b'legacy')
    shots = [{'id': '001', 'narration': 'पहली पंक्ति।'}]
    assert raghu_narration.pending_clone(tmp_path, shots) == shots


def test_paragraph_groups_span_shots_but_stop_for_dialogue():
    from app.narration_layout import paragraph_groups
    lines = [{'id': f'{i:03}', 'text': 'वह कमरे की ओर धीरे से चला।', 'speaker': 'NARRATOR'} for i in range(1, 7)]
    lines.insert(3, {'id': '099', 'text': 'कौन है?', 'speaker': 'CHAR'})
    groups = paragraph_groups(lines)
    assert len(groups) < 6
    assert [line['id'] for group in groups for line in group] == ['001', '002', '003', '004', '005', '006']
    assert not any('003' in [x['id'] for x in group] and '004' in [x['id'] for x in group] for group in groups)


def test_word_alignment_cuts_between_spoken_lines_and_keeps_all_audio():
    from app.narration_layout import line_spans
    lines = [{'id': '001', 'text': 'वह रुका।'}, {'id': '002', 'text': 'दरवाज़ा खुला।'}]
    words = [{'word': 'वह', 'start': .2, 'end': .4}, {'word': 'रुका।', 'start': .5, 'end': .9},
             {'word': 'दरवाजा', 'start': 1.3, 'end': 1.6}, {'word': 'खुला।', 'start': 1.7, 'end': 2.2}]
    spans = line_spans(lines, words, 2.5)
    assert spans[0]['start'] == 0 and spans[-1]['end'] == 2.5
    assert .9 <= spans[0]['end'] <= 1.3
    assert spans[0]['end'] == spans[1]['start']


def test_alignment_rejects_missing_words_instead_of_inventing_timing():
    from app.narration_layout import line_spans
    with pytest.raises(ValueError, match='timestamp'):
        line_spans([{'id': '001', 'text': 'पहली पंक्ति।'}, {'id': '002', 'text': 'दूसरी पंक्ति।'}], [], 3)


def test_corrupt_timestamps_are_replaced_without_resynthesizing_speech(tmp_path):
    from app.narration_layout import aligned_spans
    path = tmp_path / 'paragraph.words.json'
    path.write_text('{broken', encoding='utf-8')
    calls = []
    def transcribe():
        calls.append(True)
        return [dict(word='वह', start=.1, end=.4), dict(word='रुका।', start=.5, end=.8)]
    lines = [dict(id='001', text='वह रुका।')]
    assert aligned_spans(path, lines, 1, transcribe)[0]['end'] == 1
    assert aligned_spans(path, lines, 1, transcribe)[0]['end'] == 1
    assert len(calls) == 1


def test_alignment_cannot_mark_a_truncated_ending_complete():
    from app.narration_layout import line_spans
    lines = [dict(id='001', text='one two'), dict(id='002', text='three four')]
    words = [dict(word=w, start=i, end=i+.5) for i, w in enumerate(['one', 'two', 'three'])]
    with pytest.raises(ValueError, match='timestamp'):
        line_spans(lines, words, 3)


def test_missing_split_is_restored_from_master_without_a_gpu(tmp_path, monkeypatch):
    from app.narration_layout import VERSION, request_key
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER', 'chatterbox')
    reference = tmp_path / 'reference.wav'
    media.tone(reference, 7, 'none')
    monkeypatch.setenv('RAGHU_NARRATOR_REFERENCE', str(reference))
    output = raghu_narration.clone_folder(tmp_path)
    output.mkdir()
    media.tone(output / 'narration-master.wav', 2, 'none')
    shots = [{'id': '001', 'narration': 'पहली पंक्ति।'}, {'id': '002', 'narration': 'दूसरी पंक्ति।'}]
    lines = raghu_narration.narration_lines(shots)
    store.save(output / 'narration-timing.json', dict(version=VERSION,
        key=request_key(lines, hashlib.sha256(reference.read_bytes()).hexdigest()),
        spans=[dict(id='001', start=0, end=.8), dict(id='002', start=.8, end=2)]))
    raghu_narration.produce_clone(None, tmp_path, shots, lambda: None, lambda *_: None, raghu_pipeline.ASSETS)
    assert raghu_narration.pending_clone(tmp_path, shots) == []
    assert abs(media.duration(output / 'narration-001.wav') - .8) < .01
    assert abs(media.duration(output / 'narration-002.wav') - 1.2) < .01


def test_continuous_job_sends_full_script_and_pulls_completion_last(tmp_path, monkeypatch):
    from app.narration_layout import VERSION
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER', 'chatterbox')
    folder = tmp_path / ('a' * 32)
    folder.mkdir()
    reference = tmp_path / 'reference.wav'
    media.tone(reference, 7, 'none')
    monkeypatch.setenv('RAGHU_NARRATOR_REFERENCE', str(reference))
    shots = [{'id': '001', 'narration': 'एक रात वह रुका।'},
             {'id': '002', 'narration': 'कौन है?', 'speaker': 'CHAR', 'dialogue_voice': 'female'},
             {'id': '003', 'narration': 'किसी ने जवाब नहीं दिया।'}]
    remote_files = tmp_path / 'remote'
    remote_files.mkdir()
    pulls = []
    class Remote:
        def push(self, files):
            assert any(p.name == 'narration_layout.py' for p in files)
            assert all(p.is_file() for p in files)
        def phase(self, command, timeout):
            manifest = store.read(folder / 'narrator-input.json')
            assert len(manifest['lines']) == 3
            assert manifest['lines'][1]['speaker'] == 'DIALOGUE'
            for name, seconds in [('narration-001.wav', 1), ('narration-003.wav', 1), ('narration-master.wav', 2)]:
                media.tone(remote_files / name, seconds, 'none')
            store.save(remote_files / 'narration-timing.json', dict(version=VERSION, key=manifest['key'],
                spans=[dict(id='001', start=0, end=1), dict(id='003', start=1, end=2)]))
        def pull(self, files, destination, **kwargs):
            pulls.append(files)
            for name in files:
                shutil.copyfile(remote_files / name, destination / name)
    raghu_narration.produce_clone(Remote(), folder, shots, lambda: None, lambda *_: None, raghu_pipeline.ASSETS)
    assert pulls[-1] == ['narration-timing.json']
    assert raghu_narration.pending_clone(folder, shots) == []
    raghu_narration.produce_clone(None, folder, shots, lambda: None, lambda *_: None, raghu_pipeline.ASSETS)
