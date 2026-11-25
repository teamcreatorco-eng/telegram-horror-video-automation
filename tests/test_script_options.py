import pytest
from app import store, script_options
from app.clone_chat_planner import DeepSeekWriter
from app.youtube_clone.pipeline import planning_writer


def test_options_wait_and_resume_without_rewriting(tmp_path):
    calls = []
    def generate(number, extra):
        calls.append(number)
        if number == 2:
            assert "different complete story" in extra
        return {"speech": [{"line": f"Story {number}"}]}
    with pytest.raises(script_options.AwaitingScriptChoice):
        script_options.choose(tmp_path, generate)
    assert calls == [1, 2]
    with pytest.raises(script_options.AwaitingScriptChoice):
        script_options.choose(tmp_path, generate)
    assert calls == [1, 2]
    script_options.select(tmp_path, 2)
    assert script_options.choose(tmp_path, generate)["speech"][0]["line"] == "Story 2"
    assert calls == [1, 2]
    with pytest.raises(ValueError):
        script_options.select(tmp_path, 1)


def test_failed_second_option_keeps_first(tmp_path):
    def generate(number, extra):
        if number == 2:
            raise RuntimeError("Temporary provider failure")
        return {"speech": [{"line": "Saved first"}]}
    with pytest.raises(RuntimeError):
        script_options.choose(tmp_path, generate)
    assert len(store.read(tmp_path / "script-options.json")["options"]) == 1
    calls = []
    with pytest.raises(script_options.AwaitingScriptChoice):
        script_options.choose(tmp_path, lambda number, extra: calls.append(number) or {"speech": []})
    assert calls == [2]


def test_clone_planner_stops_before_assets_until_selection(tmp_path, monkeypatch):
    store.save(tmp_path / "script-choice-enabled.json", {"enabled": True})
    monkeypatch.setattr("app.raghu_services.required", lambda name: "fixture")
    monkeypatch.setattr(planning_writer, "check_story", lambda *a, **k: None)
    class Writer(DeepSeekWriter):
        def complete(self, prompt, **kwargs):
            assert "exact title is the only story input" in prompt, "Assets must not be requested"
            return '{"entity":"clue","characters":[],"speech":[{"speaker":"NARRATOR","line":"Complete story"}]}'
    writer = Writer(tmp_path, lambda: None, lambda *a: None)
    with pytest.raises(script_options.AwaitingScriptChoice):
        planning_writer.plan_title("A new story", 120, project_id="fixture", model=writer)
    assert len(store.read(tmp_path / "script-options.json")["options"]) == 2


def test_supplied_hindi_duration_uses_words_and_natural_narration_pace():
    from app.script_options import estimated_seconds
    assert estimated_seconds("कहानी " * 140) == 60
    assert estimated_seconds("कहानी " * 280) == 120
