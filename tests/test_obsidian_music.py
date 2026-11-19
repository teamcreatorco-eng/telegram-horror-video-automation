import wave
import pytest
from app import media, raghu_music


def test_music_crossfade_reaches_requested_length(tmp_path):
    if not media.available():
        pytest.skip("FFmpeg required")
    track = tmp_path / "source.wav"
    media.tone(track, 1)
    out = tmp_path / "bed.wav"
    raghu_music.render(out, 3.2, track)
    assert abs(media.duration(out) - 3.2) < .03
    with wave.open(str(out)) as w:
        assert w.getnchannels() == 2


def test_private_track_and_override(monkeypatch, tmp_path):
    monkeypatch.setattr(raghu_music.store, "DATA", tmp_path)
    monkeypatch.delenv("BGM_FILE", raising=False)
    assert raghu_music.source() is None
    p = tmp_path / "_music" / "obsidian.mp3"
    p.parent.mkdir(); p.write_bytes(b"fixture")
    assert raghu_music.source() == p
    custom = tmp_path / "custom.wav"; custom.write_bytes(b"fixture")
    monkeypatch.setenv("BGM_FILE", str(custom))
    assert raghu_music.source() == custom
