from contextlib import contextmanager

import pytest

from app import raghu_vast as vast, store


@pytest.mark.parametrize(
    "reason,destroyed,tries",
    [
        ("SSH port did not respond", True, 2),
        ("SSH port did not respond", False, 1),
        ("SSH key authentication failed", True, 1),
        ("SSH host identity check failed", True, 1),
    ],
)
def test_startup_replacement_requires_retryable_error_and_verified_cleanup(
    monkeypatch, tmp_path, reason, destroyed, tries
):
    calls = []
    progress = []

    @contextmanager
    def attempt(folder, ident, check, **options):
        calls.append(options)
        if len(calls) == 1:
            store.save(folder / "raghu-rental.json", {
                "id": 42, "destroyed": destroyed, "started": 900, "rate": 0.72,
            })
            raise vast.VastStartupError(reason)
        yield "replacement"

    monkeypatch.setattr(vast, "_rent_once", attempt)
    monkeypatch.setattr(vast.time, "time", lambda: 1000)
    operation = vast.rent(tmp_path, "job", lambda: None, progress=lambda *event: progress.append(event))
    if tries == 2:
        with operation as rental:
            assert rental == "replacement"
        assert calls[1]["startup_spent"] == pytest.approx(0.02)
        assert progress and "First rental released" in progress[0][0]
    else:
        with pytest.raises(vast.VastStartupError, match=reason), operation:
            pytest.fail("A rental was unexpectedly opened")
    assert len(calls) == tries


def test_distinct_vast_ssh_endpoints(tmp_path):
    row = {"public_ipaddr": "192.0.2.10", "ports": {"22/tcp": [{"HostPort": "30022"}]},
           "ssh_host": "ssh.example.com", "ssh_port": "50022"}
    direct = vast.Remote(row, tmp_path, tmp_path, 0, lambda: None)
    reported = vast.Remote(row, tmp_path, tmp_path, 0, lambda: None, reported_endpoint=True)
    assert (direct.host, direct.port) == ("root@192.0.2.10", 30022)
    assert (reported.host, reported.port) == ("root@ssh.example.com", 50022)


def test_failed_container_requires_stopped_contract_to_switch_hosts():
    row = dict(actual_status="loading", cur_state="stopped", next_state="stopped",
               intended_status="stopped", status_msg="dial /run/containerd/containerd.sock: refused")
    assert vast.stopped_failed_start(row)
    assert not vast.stopped_failed_start({**row, "next_state": "running"})
    assert not vast.stopped_failed_start({**row, "uptime_mins": 1})
    assert not vast.stopped_failed_start({**row, "actual_status": "running"})


def test_preservation_stops_without_destroying(monkeypatch, tmp_path):
    journal = tmp_path / "raghu-rental.json"
    store.save(journal, {"id": 42, "label": "job", "destroyed": False})
    row = {"id": 42, "label": "job", "actual_status": "running"}
    monkeypatch.setattr(vast, "instances", lambda: [row])
    calls = []

    def stop(action, ident):
        calls.append((action, ident))
        row["actual_status"] = "stopped"

    monkeypatch.setattr(vast, "request_instance_state", stop)
    vast.close_rental(journal, preserve=True)
    assert calls == [("stop", 42)]
    assert store.read(journal)["recovery_stop"]
    assert not store.read(journal)["destroyed"]


def test_production_can_resume_an_expired_legacy_rental(monkeypatch, tmp_path):
    journal = tmp_path / "raghu-rental.json"
    state = {"id": 42, "label": "job", "recovery_stop": True, "quantization": "fp8", "expires": 1}
    store.save(journal, state)
    row = {"id": 42, "label": "job", "actual_status": "stopped",
           "public_ipaddr": "192.0.2.10", "ports": {"22/tcp": [{"HostPort": "30022"}]}}
    monkeypatch.setenv("VAST_SSH_KEY_PATH", str(tmp_path / "key"))
    monkeypatch.delenv("RAGHU_MAX_RENTAL_MINUTES", raising=False)
    monkeypatch.setattr(vast, "motion_model", lambda: "ltx")
    monkeypatch.setattr(vast, "instances", lambda: [row])
    monkeypatch.setattr(vast.Remote, "restart_preserved", lambda self, state: None)
    monkeypatch.setattr(vast.Remote, "push", lambda self, paths: None)
    monkeypatch.setattr(vast, "close_rental", lambda journal, preserve: None)

    with vast.rent(tmp_path, "job", lambda: None) as remote:
        assert remote.deadline == float("inf")
        assert "expires" not in store.read(journal)


def test_cleanup_failure_does_not_hide_original_generation_error(monkeypatch, tmp_path):
    journal = tmp_path / "raghu-rental.json"
    store.save(journal, {"id": 42})
    def fail(*args):
        raise RuntimeError("Stop confirmation unavailable")
    monkeypatch.setattr(vast, "close_rental", fail)
    with pytest.raises(vast.VastStartupError, match="authentication"):
        try:
            raise vast.VastStartupError("SSH key authentication failed")
        finally:
            vast.finish_rental(journal, True)
    saved = store.read(journal)
    assert "authentication" in saved["original_error"]
    assert saved["cleanup_error"] == "Stop confirmation unavailable"


def test_missing_contract_does_not_report_unconfirmed_stop(monkeypatch, tmp_path):
    journal = tmp_path / "raghu-rental.json"
    store.save(journal, {"id": 42, "label": "job", "destroyed": False})
    monkeypatch.setattr(vast, "instances", lambda: [])
    vast.close_rental(journal, True)
    assert store.read(journal)["destroyed"]
    assert not store.read(journal)["recovery_stop"]


def test_key_registration_is_verified_before_rental(monkeypatch):
    calls = []
    registered = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return registered.copy()
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url):
            calls.append("get")
            return Response()
        def post(self, url, json):
            calls.append("register")
            registered.append({"public_key": json["ssh_key"]})
            return Response()
    monkeypatch.setattr(vast, "required", lambda name: "test")
    monkeypatch.setattr(vast.httpx, "Client", Client)
    vast.ensure_ssh_key("ssh-ed25519 public")
    vast.ensure_ssh_key("ssh-ed25519 public")
    assert calls == ["get", "register", "get", "get"]


def test_failed_creation_without_contract_is_reconciled_after_status_checks(monkeypatch, tmp_path):
    journal = tmp_path / "raghu-rental.json"
    store.save(journal, {"id": None, "label": "job", "destroyed": False})
    reads = []
    monkeypatch.setattr(vast, "instances", lambda: reads.append(1) or [])
    monkeypatch.setattr(vast.time, "sleep", lambda seconds: None)
    vast.destroy(journal)
    assert len(reads) == 5
    assert store.read(journal)["destroyed"]


def test_vast_rejection_exposes_reason_and_redacts_api_key(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(vast, "required", lambda name: "private-test-token")
    monkeypatch.setattr(vast.subprocess, "run", lambda *args, **kw: SimpleNamespace(
        returncode=1, stderr="Insufficient credit private-test-token", stdout=""))
    with pytest.raises(RuntimeError, match="Insufficient credit") as error:
        vast.vast("create", "instance", "42")
    assert "private-test-token" not in str(error.value)
    assert "[redacted]" in str(error.value)


def test_empty_balance_explains_connection_loss_without_starting_gpu(monkeypatch):
    monkeypatch.setattr(vast, "vast", lambda *args: {"credit": 0})
    with pytest.raises(RuntimeError, match=r"Vast balance is \$0"):
        vast.explain_connection_loss(vast.VastConnectionLost("Connection closed"))


def test_funded_connection_loss_remains_a_transport_error(monkeypatch):
    monkeypatch.setattr(vast, "vast", lambda *args: {"user": {"credit": 2}})
    error = vast.VastConnectionLost("Connection closed")
    assert vast.explain_connection_loss(error) is error


def test_empty_credit_blocks_preserved_restart_before_any_mutation(monkeypatch, tmp_path):
    monkeypatch.setattr(vast, "vast", lambda *args: {"credit": 0})
    calls = []
    monkeypatch.setattr(vast, "request_instance_state", lambda *args: calls.append(args))
    remote = vast.Remote({}, tmp_path / "key", tmp_path, float("inf"), lambda: None, connecting=False)
    with pytest.raises(RuntimeError, match="Vast balance is"):
        remote.restart_preserved({"id": 42})
    assert calls == []


@pytest.mark.parametrize("disappears", [False, True])
def test_startup_release_waits_for_delayed_stop(monkeypatch, tmp_path, disappears):
    state = {"id": 42, "label": "job", "recovery_stop": True}
    loading = {"id": 42, "label": "job", "actual_status": "loading", "intended_status": "stopped"}
    stopped = {**loading, "actual_status": "stopped"}
    listings = iter([[loading], [], [loading], [], []] if disappears else [[loading], [loading], [stopped]])
    monkeypatch.setattr(vast, "instances", lambda: next(listings))
    monkeypatch.setattr(vast.time, "sleep", lambda _: None)
    events = []
    result = vast.wait_for_startup_release(state, tmp_path / "rental.json", lambda: None,
                                          lambda *args: events.append(args))
    assert result["stop_confirmed_at"] > 0
    assert bool(result.get("destroyed")) == disappears
    assert events


def test_startup_release_does_not_assume_stop_intent_is_confirmation(monkeypatch, tmp_path):
    state = {"id": 42, "label": "job", "recovery_stop": True}
    monkeypatch.setattr(vast, "instances", lambda: [{"id": 42, "label": "job",
        "actual_status": "loading", "intended_status": "stopped"}])
    monkeypatch.setattr(vast.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="No second GPU was rented"):
        vast.wait_for_startup_release(state, tmp_path / "rental.json", lambda: None, lambda *args: None)


def test_startup_release_never_replaces_host_with_saved_work(tmp_path):
    with pytest.raises(RuntimeError, match="saved work"):
        vast.wait_for_startup_release({"ssh_ready_seconds": 5}, tmp_path / "rental.json",
                                      lambda: None, lambda *args: None)


def test_startup_progress_distinguishes_package_install_from_rendering():
    message = vast.startup_progress({"actual_status": "loading", "status_msg": "Get:5 Packages"}, 600)
    assert "installing container packages" in message
    assert "600s" in message
    assert "generation has not started" in message
