import pytest
from pathlib import Path
from app import raghu_music, raghu_narration, raghu_pipeline, bundled_media


def test_fresh_clone_has_four_music_choices_without_installed_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(raghu_music.store, 'DATA', tmp_path)
    tracks = raghu_music.choices()
    assert len(tracks) == 4
    for track in tracks:
        assert track['file'].is_file() and track['preview'].is_file()
        assert track['file'].is_relative_to(bundled_media.ROOT)
        assert raghu_music.source(track['id']) == track['file']


def test_fresh_clone_has_narrator_and_opening(tmp_path, monkeypatch):
    monkeypatch.setattr(raghu_narration.store, 'DATA', tmp_path)
    monkeypatch.delenv('RAGHU_NARRATOR_REFERENCE', raising=False)
    monkeypatch.delenv('RAGHU_OPENING_PATH', raising=False)
    assert raghu_narration.clone_reference() == bundled_media.ROOT / 'narrator.wav'
    assert raghu_pipeline.opening_path() == bundled_media.ROOT / 'opening.mp4'


def test_explicit_custom_paths_are_not_silently_replaced(tmp_path, monkeypatch):
    custom = tmp_path / 'custom.wav'
    monkeypatch.setenv('RAGHU_NARRATOR_REFERENCE', str(custom))
    with pytest.raises(RuntimeError, match='missing'):
        raghu_narration.clone_reference()


def test_installed_music_override_keeps_matching_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(raghu_music.store, 'DATA', tmp_path)
    root = tmp_path / '_music/catalog'
    (root / 'previews').mkdir(parents=True)
    (root/'ghostly-piano.mp3').write_bytes(b'custom track')
    (root/'previews/ghostly-piano.mp4').write_bytes(b'custom preview')
    entry=next(t for t in raghu_music.choices() if t['id']=='ghostly-piano')
    assert entry['file'] == root/'ghostly-piano.mp3'
    assert entry['preview'] == root/'previews/ghostly-piano.mp4'


def test_every_bundled_binary_matches_manifest_and_decodes():
    import hashlib,json
    from app import media
    manifest=json.loads((bundled_media.ROOT/'manifest.json').read_text())
    assert len(manifest)==14
    for name,entry in manifest.items():
        path=bundled_media.ROOT/name
        assert path.stat().st_size==entry['bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
        if media.available():
            media.run(['ffmpeg','-v','error','-xerror','-i',str(path),'-f','null','-'])
