import json
import pytest

from app import media, raghu_music, raghu_pipeline


def test_legacy_obsidian_track_is_not_selected(tmp_path, monkeypatch):
    monkeypatch.setattr(raghu_music.store, 'DATA', tmp_path)
    monkeypatch.delenv('RAGHU_USER_BGM_FILE', raising=False)
    legacy = tmp_path / '_music' / 'obsidian.mp3'
    legacy.parent.mkdir()
    legacy.write_bytes(b'old music')
    monkeypatch.setenv('BGM_FILE', str(legacy))
    assert raghu_music.source() is None


def test_only_new_explicit_user_track_is_selected(tmp_path, monkeypatch):
    custom = tmp_path / 'my-music.wav'
    custom.write_bytes(b'custom track')
    monkeypatch.setenv('RAGHU_USER_BGM_FILE', str(custom))
    assert raghu_music.source() == custom


def test_old_sound_flags_and_plan_cannot_add_any_sound(tmp_path, monkeypatch):
    if not media.available():
        pytest.skip('FFmpeg required')
    monkeypatch.setenv('RAGHU_NARRATION_PROVIDER', 'none')
    monkeypatch.setenv('RAGHU_MOTION_MODEL', 'ltx')
    monkeypatch.setenv('RAGHU_SOUND_EFFECTS', 'true')
    monkeypatch.delenv('RAGHU_USER_BGM_FILE', raising=False)
    media.ff('-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24', '-t', '1', '-an',
             '-c:v', 'libx264', tmp_path / 'shot-001.mp4')
    shot = dict(id='001', sound_design='[score:dread] [effect:rain@0] [effect:knock@.5]')
    raghu_pipeline.edit(tmp_path, dict(requested_seconds=1, shots=[shot]),
                        lambda: None, music=True, sound_effects=True)
    report = json.loads((tmp_path / 'sound-effects-report.json').read_text())
    assert report['effects'] == []
    assert report['music_source'] is None
    decoded = tmp_path / 'decoded.wav'
    media.ff('-i', tmp_path / 'final.mp4', '-vn', '-c:a', 'pcm_s16le', decoded)
    import wave
    from array import array
    with wave.open(str(decoded)) as stream:
        data = array('h', stream.readframes(stream.getnframes()))
    assert not any(data)


def test_user_music_mix_keeps_picture_and_duration(tmp_path):
    if not media.available():
        pytest.skip('FFmpeg required')
    source, music, bed, final = [tmp_path / p for p in
                               ('source.mp4', 'music.wav', 'bed.wav', 'final.mp4')]
    media.ff('-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=24',
             '-f', 'lavfi', '-i', 'sine=frequency=400:sample_rate=48000',
             '-t', '2', '-c:v', 'libx264', '-c:a', 'aac', source)
    media.ff('-f', 'lavfi', '-i', 'sine=frequency=800:sample_rate=48000', '-t', '1', music)
    raghu_music.render(bed, 2, music)
    raghu_music.mix(source, final, bed)
    def picture_hash(path):
        return media.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map',
                          '0:v:0', '-c', 'copy', '-f', 'hash', '-'])
    assert picture_hash(source) == picture_hash(final)
    assert media.duration(final) == pytest.approx(media.duration(source), abs=.03)


def test_missing_music_fails_before_other_production_setup(tmp_path, monkeypatch):
    monkeypatch.setenv('RAGHU_USER_BGM_FILE', str(tmp_path / 'missing.wav'))
    monkeypatch.setenv('RAGHU_BACKGROUND_AUDIO', 'true')
    with pytest.raises(RuntimeError, match='background music'):
        raghu_pipeline.validate_music_for_request({})
