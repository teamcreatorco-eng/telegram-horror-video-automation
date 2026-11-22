import json
import math
import wave
from array import array

import pytest

from app import raghu_sound, media


def test_radio_breaking_and_heat_cues_are_not_silently_discarded():
    score, cues = raghu_sound.parse_sound_design(
        '[score:dread] [effect:radio_static@0] [effect:glass_break@0.6] [effect:sizzle@0.8]')
    assert score == 'dread'
    assert cues == [{'effect': 'radio', 'at': 0}, {'effect': 'break_glass', 'at': .6},
                    {'effect': 'sizzle', 'at': .8}]


def test_missing_compiled_cues_are_recovered_from_saved_sound_design():
    row = raghu_sound.resolve_shot_sound({'sound_cues': [],
        'sound_design': '[score:suspense] [effect:drip@0.4] [effect:paper@0.7]'})
    assert row['sound_cues'] == [{'effect': 'drip', 'at': .4}, {'effect': 'rustle', 'at': .7}]


def test_natural_sound_description_selects_effects_without_extra_ai_calls():
    row = raghu_sound.resolve_shot_sound({'sound_design': 'Rain outside. Radio static crackles. Glass shatters.'})
    assert {'rain', 'radio', 'break_glass'} <= {c['effect'] for c in row['sound_cues']}


def test_no_sound_is_invented_from_narration_or_negative_descriptions():
    row = raghu_sound.resolve_shot_sound({'narration': 'Fire and rain in a memory',
        'sound_design': 'No rain. Radio stays silent. No fire sound.'})
    assert row['sound_cues'] == []


def test_explicit_saved_cues_are_preserved_and_unknown_names_are_reported():
    row = raghu_sound.resolve_shot_sound({'sound_cues': [{'effect': 'knock', 'at': .25}],
        'sound_design': '[effect:knock@0.25] [effect:unknownthing@0.5]'})
    assert row['sound_cues'] == [{'effect': 'knock', 'at': .25}]
    assert row['unresolved_sound_cues'] == ['unknownthing']


def test_rendered_effect_is_audible_at_actual_edited_cue_time(tmp_path):
    output = tmp_path / 'cues.wav'
    timeline = [{'id': '001', 'start': 6, 'seconds': 2, 'score': 'none',
                 'sound_cues': [{'effect': 'knock', 'at': .5}]}]
    report = raghu_sound.render_cues(output, timeline, 6, 2)
    with wave.open(str(output)) as stream:
        data = array('h', stream.readframes(stream.getnframes()))
    assert max(abs(x) for x in data[:raghu_sound.RATE]) == 0
    assert max(abs(x) for x in data[raghu_sound.RATE:]) > 1000
    assert report[0]['at_seconds'] == 1


def test_effects_can_be_exported_without_music_or_narration(tmp_path, monkeypatch):
    if not media.available():
        pytest.skip('FFmpeg required')
    from app import raghu_pipeline
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER', 'none')
    monkeypatch.setenv('RAGHU_MOTION_MODEL', 'ltx')
    media.ff('-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24', '-t', '1', '-an',
             '-c:v', 'libx264', tmp_path / 'shot-001.mp4')
    plan = {'shots': [{'id': '001', 'sound_design': '[effect:radio_static@0]'}], 'requested_seconds': 1}
    raghu_pipeline.edit(tmp_path, plan, lambda: None, music=False, opening=False)
    report = json.loads((tmp_path / 'sound-effects-report.json').read_text())
    assert report['effects'] and report['effects'][0]['effect'] == 'radio'
    assert any(s['codec_type'] == 'audio' for s in media.probe(tmp_path / 'final.mp4')['streams'])


def test_mixer_keeps_video_packets_and_audible_effects(tmp_path):
    if not media.available():
        pytest.skip('FFmpeg required')
    source, output, cues = [tmp_path / x for x in ('source.mp4', 'result.mp4', 'cues.wav')]
    media.ff('-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24', '-f', 'lavfi', '-i',
             'anullsrc=r=48000:cl=stereo', '-t', '2', '-c:v', 'libx264', '-c:a', 'aac', source)
    raghu_sound.render_cues(cues, [{'id': '001', 'start': 0, 'seconds': 2, 'score': 'none',
                                  'sound_cues': [{'effect': 'break_glass', 'at': .5}]}], 0, 2)
    raghu_sound.mix_soundtrack(source, output, cues)
    def hash_video(p):
        return media.run(['ffmpeg', '-v', 'error', '-i', str(p), '-map', '0:v:0', '-c', 'copy', '-f', 'hash', '-'])
    assert hash_video(source) == hash_video(output)
    decoded = tmp_path / 'audio.wav'
    media.ff('-i', output, '-vn', '-ar', '24000', '-ac', '1', decoded)
    with wave.open(str(decoded)) as stream:
        data = array('h', stream.readframes(stream.getnframes()))
    assert max(abs(x) for x in data) > 1000


def test_every_bundled_sound_is_licensed_verified_and_decodable():
    manifest = json.loads((raghu_sound.ASSET_DIR / 'manifest.json').read_text())
    assert {'fire', 'rain', 'radio', 'break_glass', 'sizzle', 'rustle', 'creak'} <= set(manifest)
    for name, asset in manifest.items():
        assert name in raghu_sound.EFFECTS
        assert asset['license'] == 'CC0-1.0'
        assert asset['source'].startswith('https://opengameart.org/content/')
        assert raghu_sound.recorded_samples(name) is not None


def test_recorded_door_sound_is_not_normalized_to_inaudible_level(tmp_path):
    out = tmp_path / 'door.wav'
    raghu_sound.render_cues(out, [{'id': '001', 'start': 0, 'seconds': 1, 'score': 'none',
                                 'sound_cues': [{'effect': 'creak', 'at': 0}]}], 0, 1)
    with wave.open(str(out)) as stream:
        samples = array('h', stream.readframes(stream.getnframes()))
    rms_db = 20 * math.log10(math.sqrt(sum(x*x for x in samples)/len(samples))/32768)
    assert rms_db > -35


@pytest.mark.parametrize('text', ['Radio switched off. Rain has stopped.',
    'A recording of radio static is mentioned but never heard.', 'The radio dial glows silently.'])
def test_inactive_and_mentioned_sources_do_not_gain_sound(text):
    assert raghu_sound.resolve_shot_sound({'sound_design': text})['sound_cues'] == []


def test_no_music_instruction_does_not_cancel_requested_scene_effect():
    row = raghu_sound.resolve_shot_sound({'sound_design': 'Radio static crackles, no music.'})
    assert row['sound_cues'] == [{'effect': 'radio', 'at': 0}]


def test_synthesized_scene_score_is_routed_separately_from_effects(tmp_path, monkeypatch):
    if not media.available():
        pytest.skip('FFmpeg required')
    from app import raghu_pipeline
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER', 'none')
    monkeypatch.setenv('RAGHU_MOTION_MODEL', 'ltx')
    monkeypatch.setattr(raghu_pipeline.raghu_music, 'source', lambda: None)
    media.ff('-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24', '-t', '2', '-an',
             '-c:v', 'libx264', tmp_path / 'shot-001.mp4')
    calls = []
    original = raghu_pipeline.render_cues
    def render(path, timeline, *args):
        calls.append((path.name, timeline))
        return original(path, timeline, *args)
    monkeypatch.setattr(raghu_pipeline, 'render_cues', render)
    raghu_pipeline.edit(tmp_path, {'shots': [{'id': '001', 'sound_design': '[score:dread] [effect:knock@0.5]'}]},
                        lambda: None, music=True, opening=False)
    effects = next(rows for name, rows in calls if name == 'cues.wav')
    score = next(rows for name, rows in calls if name == 'scene-score.wav')
    assert all(row['score'] == 'none' for row in effects)
    assert all(not row['sound_cues'] for row in score)
