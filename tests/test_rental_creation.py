import json
from types import SimpleNamespace
import httpx
import pytest
from app import raghu_vast as vast, store


def setup(monkeypatch, tmp_path, responses, listings=None):
    calls = []
    monkeypatch.setattr(vast, "required", lambda name: "secret-key")
    monkeypatch.setattr(vast, "instances", listings or (lambda: []))
    monkeypatch.setattr(vast.time, "sleep", lambda _: None)
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def put(self, url, **kwargs):
            calls.append((url, kwargs["json"]))
            value = responses.pop(0)
            if isinstance(value, Exception): raise value
            code, data = value
            return httpx.Response(code, json=data)
    monkeypatch.setattr(vast.httpx, "Client", Client)
    start = tmp_path / "start.sh"; start.write_text("echo ready")
    state = {"label": "job", "id": None}
    offers = [{"id": n, "dph_total": .6} for n in (1, 2, 3)]
    def run():
        return vast.create_rental(offers, state, tmp_path / "rental.json", start, lambda: None, lambda *args: None)
    return run, calls, state


def test_unavailable_offer_switches_but_only_once_per_request(monkeypatch, tmp_path):
    run, calls, state = setup(monkeypatch, tmp_path, [(409, {"success": False, "msg": "Offer already rented"}),
                                                   (200, {"success": True, "new_contract": 99})])
    assert run() == 99
    assert len(calls) == 2
    assert calls[0][1]["runtype"] == "ssh_direct"
    assert calls[0][1]["cancel_unavail"] is True
    assert state["offer_id"] == 2


def test_auth_failure_is_not_retried_and_key_is_redacted(monkeypatch, tmp_path):
    run, calls, state = setup(monkeypatch, tmp_path, [(403, {"msg": "Permission denied secret-key"})])
    with pytest.raises(RuntimeError, match="HTTP 403") as e: run()
    assert "secret-key" not in str(e.value)
    assert len(calls) == 1


def test_lost_response_reuses_existing_rental(monkeypatch, tmp_path):
    rows = iter([[], [{"id": 99, "label": "job"}]])
    run, calls, state = setup(monkeypatch, tmp_path, [httpx.ReadTimeout("lost reply")], lambda: next(rows))
    assert run() == 99
    assert len(calls) == 1


def test_unknown_outcome_blocks_duplicate_creation(monkeypatch, tmp_path):
    run, calls, state = setup(monkeypatch, tmp_path, [httpx.ReadTimeout("lost reply")])
    with pytest.raises(RuntimeError, match="outcome is uncertain"): run()
    assert state["creation_uncertain"]
    with pytest.raises(RuntimeError, match="outcome is uncertain"): run()
    assert len(calls) == 1


def test_invalid_cli_json_preserves_output(monkeypatch):
    monkeypatch.setattr(vast, "required", lambda _: "secret-key")
    monkeypatch.setattr(vast.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="Provider returned a proxy error secret-key", stderr=""))
    with pytest.raises(RuntimeError, match="unreadable JSON") as e: vast.vast("show", "user")
    assert "proxy error" in str(e.value)
    assert "secret-key" not in str(e.value)
