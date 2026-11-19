"""Budgeted Vast lease and SSH asset transport for the Raghu worker."""
import base64
import json
import logging
import ssl
import sys
import os
import shlex
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import httpx

from . import store
from .raghu_services import ASSETS, required

REMOTE = "/workspace/raghu"
RUNTIME_IMAGE = "pytorch/pytorch:2.10.0-cuda13.0-cudnn9-devel"
NVFP4_CHECKPOINT = "diffusion_models/ltx-2.5-22b-distilled-transformer-nvfp4.safetensors"


def motion_model():
    model = os.getenv("RAGHU_MOTION_MODEL", "ltx")
    if model not in ("wan22", "ltx", "hunyuan15"):
        raise RuntimeError("RAGHU_MOTION_MODEL must be wan22, ltx or hunyuan15.")
    return model


def check_available_credit(credit, estimated_cost):
    """An explicit operator override may start a test without its full estimate."""
    if credit <= 0:
        raise RuntimeError("Vast credit is empty. Add credit before renting a GPU.")
    if credit < estimated_cost:
        if os.getenv("RAGHU_ALLOW_LOW_BALANCE", "").lower() != "true":
            raise RuntimeError(f"Vast has ${credit:.2f} credit. The lowest eligible rental needs about ${estimated_cost:.2f}.")
        logging.warning("Starting with operator low-balance override: credit $%.2f, estimate $%.2f", credit, estimated_cost)


def require_resume_credit():
    user = vast("show", "user")
    user = user.get("user", user)
    if float(user.get("credit", 0)) <= 0:
        raise RuntimeError("Vast balance is $0. Add credit, then retry this step to reuse the saved images "
                           "and preserved model disk. The GPU has not been restarted.")


def explain_connection_loss(error):
    """Check funding after an established connection drops, without retrying a rental."""
    try:
        require_resume_credit()
    except RuntimeError as funding_error:
        if "Vast balance is $0" in str(funding_error):
            raise funding_error from error
        logging.warning("Could not check Vast credit after connection loss")
    return error


def image_model():
    model = os.getenv("RAGHU_IMAGE_MODEL", "flux")
    if model not in ("flux", "qwen2511"):
        raise RuntimeError("RAGHU_IMAGE_MODEL must be flux or qwen2511.")
    return model


def uses_ending_image():
    """Keep paired LTX available for explicitly configured legacy jobs."""
    return motion_model() == "ltx" and os.getenv("RAGHU_LTX_END_FRAME", "").lower() == "true"


def model_manifest(quantization):
    models = store.read(ASSETS / "model-manifest.json")
    if quantization == "nvfp4":
        models[0].update(file=NVFP4_CHECKPOINT, bytes=18721548408,
                         sha256="4b94231e734c1950f8f6826cb8bd8715d94be5b3e04f8256ee060c5bc3886c30")
    return models


def check_model_access(models, token):
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            for item in models:
                url = f"https://huggingface.co/{item['repo']}/resolve/{item['revision']}/{item['file']}"
                response = client.head(url, headers={"Authorization": f"Bearer {token}"})
                if response.status_code not in (200, 302, 307):
                    raise RuntimeError(f"Hugging Face model preflight returned HTTP {response.status_code}.")
    except httpx.HTTPError:
        raise RuntimeError("Hugging Face could not be reached before rental.") from None

def vast(*args):
    key = required("VAST_API_KEY")
    def detail(value):
        return str(value).replace(key, "[redacted]")[:600]
    try:
        result = subprocess.run(["vastai", "--raw", "--retry", "1", *map(str, args)],
                                capture_output=True, text=True, timeout=45, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Vast command timed out: " + " ".join(args[:2])) from exc
    except OSError as exc:
        raise RuntimeError("Vast command could not start: " + detail(exc)) from exc
    if result.returncode:
        raise RuntimeError("Vast operation failed: " + " ".join(args[:2]) + ". "
                           + detail(result.stderr or result.stdout or "No provider details returned"))
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("Vast returned unreadable JSON for " + " ".join(args[:2]) + ". "
                           + detail(result.stderr or result.stdout or "Empty response")) from exc
    if isinstance(value, dict) and (value.get("error") or value.get("success") is False):
        raise RuntimeError("Vast operation failed: " + " ".join(args[:2]) + ". "
                           + detail(value.get("msg") or value.get("message") or value.get("error") or "Request rejected"))
    return value


def create_rental(offers, state, journal, onstart, check, progress):
    """Single-submit HTTP creation. Retry only explicit availability rejections."""
    if state.get("creation_uncertain"):
        raise RuntimeError("Previous rental outcome is uncertain. Check Vast instances before starting another rental.")
    key = required("VAST_API_KEY")
    def redact(value):
        return str(value).replace(key, "[redacted]")[:600]
    for number, offer in enumerate(offers[:3], 1):
        check()
        # Never submit if a previous request with this production label already exists.
        matches = [row for row in instances() if row.get("label") == state["label"]]
        if matches:
            if len(matches) != 1:
                raise RuntimeError("Multiple rentals match this production; reconcile before retrying.")
            return int(matches[0]["id"])
        state.update(offer_id=offer["id"], machine_id=offer.get("machine_id"), rate=offer["dph_total"])
        store.save(journal, state)
        payload = dict(client_id="me", image=RUNTIME_IMAGE, disk=150,
                       label=state["label"], runtype="ssh_direct", cancel_unavail=True,
                       onstart=Path(onstart).read_text(), env={})
        failure = None
        unavailable = False
        try:
            with httpx.Client(verify=ssl.create_default_context(), timeout=45) as client:
                response = client.put(f"https://console.vast.ai/api/v0/asks/{offer['id']}/",
                                      headers={"Authorization": "Bearer " + key}, json=payload)
            try:
                value = response.json()
            except ValueError:
                value = {}
            if response.is_success and value.get("success") is not False and value.get("new_contract"):
                return int(value["new_contract"])
            message = value.get("msg") or value.get("message") or value.get("error") or response.text or "Empty response"
            failure = f"Vast rental HTTP {response.status_code}: {redact(message)}"
            unavailable = response.status_code in (400, 404, 409, 410) and any(
                word in str(message).lower() for word in ("unavailable", "not available", "already rented", "not rentable", "no such offer", "scheduling failed"))
            # Only an explicit non-success result proves the request was rejected.
            explicit = response.status_code in (400, 401, 402, 403, 404, 409, 410, 422) or value.get("success") is False
        except httpx.HTTPError as exc:
            failure = "Vast rental connection failed: " + redact(exc)
            explicit = False
        state["creation_error"] = failure
        store.save(journal, state)
        if not explicit:
            # A lost reply can hide a successful paid rental. Reconcile before any retry.
            for _ in range(5):
                check()
                matches = [row for row in instances() if row.get("label") == state["label"]]
                if len(matches) == 1:
                    return int(matches[0]["id"])
                time.sleep(2)
            state["creation_uncertain"] = True
            store.save(journal, state)
            raise RuntimeError(failure + ". Rental outcome is uncertain; no duplicate request was submitted.")
        if unavailable and number < min(3, len(offers)):
            progress("Selected GPU became unavailable; trying another eligible offer", 27)
            continue
        raise RuntimeError(failure)
    raise RuntimeError("Eligible Vast offers became unavailable. Retry later with the saved plan.")


def instances():
    try:
        with httpx.Client(verify=ssl.create_default_context(), timeout=20) as client:
            response = client.get("https://console.vast.ai/api/v1/instances/",
                                  headers={"Authorization": "Bearer " + required("VAST_API_KEY")})
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        raise RuntimeError("Vast instance status could not be read.") from None
    return result if isinstance(result, list) else result["instances"]

def instance_stopped(row):
    return row.get("actual_status") in {"stopped", "inactive"} or (
        row.get("actual_status") == "exited" and row.get("intended_status") == "stopped")


def ensure_ssh_key(public_key):
    """Register the derived public key before billing starts, independently of onstart."""
    def matches(rows):
        if isinstance(rows, dict):
            rows = rows.get("ssh_keys", rows.get("keys", []))
        if isinstance(rows, str):
            rows = json.loads(rows)
        return any(" ".join(row.get("public_key", row.get("ssh_key", "")).split()[:2]) == public_key
                   for row in rows)
    try:
        with httpx.Client(verify=ssl.create_default_context(), timeout=20,
                          headers={"Authorization": "Bearer " + required("VAST_API_KEY")}) as client:
            url = "https://console.vast.ai/api/v0/ssh/"
            response = client.get(url)
            response.raise_for_status()
            if matches(response.json()):
                return
            response = client.post(url, json={"ssh_key": public_key})
            response.raise_for_status()
            response = client.get(url)
            response.raise_for_status()
            if not matches(response.json()):
                raise ValueError("Registered key was not returned")
    except (httpx.HTTPError, ValueError, TypeError):
        raise RuntimeError("Vast SSH key registration could not be verified. No GPU was rented.") from None


def finish_rental(journal, preserve):
    """Keep the generation failure visible when the provider also fails cleanup."""
    original = sys.exc_info()[1]
    try:
        close_rental(journal, preserve)
    except Exception as cleanup_error:
        if original is None:
            raise
        state = store.read(journal)
        store.save(journal, {**state, "cleanup_error": str(cleanup_error), "original_error": str(original)})
        logging.exception("Rental cleanup also failed; preserving original production error")
        original.add_note("Rental cleanup also failed: " + str(cleanup_error))


def stopped_failed_start(row):
    return (all(row.get(key) == "stopped" for key in ("cur_state", "next_state", "intended_status"))
            and row.get("actual_status") in ("loading", "exited", "offline", "stopped", "inactive")
            and not row.get("uptime_mins")
            and "containerd.sock" in (row.get("status_msg") or ""))


def close_rental(journal, preserve):
    state = store.read(journal)
    if not preserve:
        if not state.get("recovery_stop"):
            destroy(journal)
        return
    state = {**state, "recovery_stop": True, "stopped_at": time.time()}
    store.save(journal, state)
    row = next((r for r in instances() if r.get("label") == state["label"]
                and (not state.get("id") or r["id"] == state["id"])), None)
    if row is None:
        store.save(journal, {**state, "destroyed": True, "recovery_stop": False,
                             "missing_at": time.time()})
        return
    if not state.get("id"):
        store.save(journal, {**state, "id": row["id"], "recovery_stop": True})
    if not (instance_stopped(row) or stopped_failed_start(row)):
        request_instance_state("stop", row["id"])
        for _ in range(12):
            row = next((r for r in instances() if r["id"] == row["id"]), None)
            if row and (instance_stopped(row) or stopped_failed_start(row)):
                return
            if not row:
                store.save(journal, {**state, "destroyed": True, "recovery_stop": False,
                                     "missing_at": time.time()})
                return
            time.sleep(3)
        raise RuntimeError("Vast received the stop request but has not confirmed the rental is stopped.")


def request_instance_state(action, ident):
    # State-changing CLI operations can emit a human-readable acknowledgement even with --raw.
    required("VAST_API_KEY")
    try:
        subprocess.run(["vastai", "--raw", "--retry", "1", action, "instance", str(ident)],
                       capture_output=True, timeout=45, check=True)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(f"Vast {action} request failed for instance {ident}.") from None

def destroy(journal):
    state = store.read(journal)
    if state.get("destroyed"):
        return
    for attempt in range(5):
        try:
            rows = [r for r in instances() if r.get("label") == state["label"]
                    or r["id"] == state.get("id")]
            if not rows and not state.get("id"):
                if attempt < 4:
                    time.sleep(4)
                    continue
                store.save(journal, {**state, "destroyed": True, "recovery_stop": False,
                                     "missing_at": time.time()})
                return
            for row in rows:
                vast("destroy", "instance", row["id"], "--yes")
            if not any(r.get("label") == state["label"] or r["id"] == state.get("id") for r in instances()):
                store.save(journal, {**state, "destroyed": True})
                return
        except RuntimeError:
            pass
        time.sleep(4)
    raise RuntimeError(f"Vast cleanup could not be confirmed for {state['label']}.")

def startup_cleanup():
    for journal in store.DATA.glob("*/raghu-rental.json"):
        state = store.read(journal)
        if state.get("destroyed"):
            continue
        if state.get("recovery_stop") or (journal.parent / "vast-preserve-and-replace.json").is_file():
            if not state.get("recovery_stop"):
                close_rental(journal, preserve=True)
            else:
                row = next((r for r in instances() if r["id"] == state.get("id")
                            or r.get("label") == state["label"]), None)
                if row and not (instance_stopped(row) or stopped_failed_start(row)):
                    request_instance_state("stop", row["id"])
            continue
        destroy(journal)

class VastConnectionLost(RuntimeError):
    """SSH transport failed before it could report the remote command result."""


class VastStartupError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(f"Vast could not establish SSH before generation: {reason}.")


def ssh_failure(stderr):
    message = stderr.lower()
    if "permission denied" in message or "invalid format" in message or "unprotected private key" in message:
        return "SSH key authentication failed"
    if "host key verification failed" in message or "remote host identification has changed" in message:
        return "SSH host identity check failed"
    if "connection refused" in message:
        return "SSH port refused the connection"
    if "timed out" in message or "no route to host" in message or "network is unreachable" in message:
        return "SSH port did not respond"
    if "could not resolve hostname" in message:
        return "SSH hostname did not resolve"
    return "SSH connection closed before it was ready"


class Remote:
    def __init__(self, row, key, temp, deadline, check, *, connecting=True, reported_endpoint=False):
        self.key, self.temp, self.deadline, self.check = key, temp, deadline, check
        ports = (row.get("ports") or {}).get("22/tcp") or []
        direct = bool(not reported_endpoint and ports and row.get("public_ipaddr"))
        host = row.get("public_ipaddr") if direct else row.get("ssh_host")
        self.port = int(ports[0]["HostPort"] if direct else row.get("ssh_port") or 0)
        if connecting and (not host or not self.port):
            raise RuntimeError("Vast SSH endpoint is not ready.")
        self.host = f"root@{host}"
        self.quantization = "fp8"
        self.recovery_attempted = False
        self.journal = None

    def command(self, program, args, seconds=60, source=None):
        options = ["-F", "none", "-i", str(self.key), "-o", "BatchMode=yes",
                   "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=8",
                   "-o", "ServerAliveInterval=8", "-o", "ServerAliveCountMax=3",
                   "-o", "StrictHostKeyChecking=accept-new",
                   "-o", f"UserKnownHostsFile={self.temp / 'known_hosts'}"]
        port_flag = "-P" if program == "scp" else "-p"
        until = min(time.monotonic() + seconds, self.deadline)
        with tempfile.TemporaryFile() as errors, subprocess.Popen(
            [program, *options, port_flag, str(self.port), *args],
            stdin=source if source is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=errors,
        ) as process:
            try:
                while process.poll() is None:
                    self.check()
                    if time.monotonic() >= until:
                        raise TimeoutError("Vast operation reached its deadline.")
                    time.sleep(.4)
                if process.returncode == 255:
                    errors.seek(0)
                    reason = ssh_failure(errors.read(16384).decode("utf-8", errors="replace"))
                    raise VastConnectionLost(reason)
                if process.returncode:
                    raise RuntimeError(f"Vast {program} operation failed.")
            finally:
                if process.poll() is None:
                    process.kill()

    def stop_for_recovery(self):
        state = store.read(self.journal)
        ident = state["id"]
        store.save(self.journal, {**state, "recovery_stop": True})
        try:
            request_instance_state("stop", ident)
        except RuntimeError:
            if not any(row["id"] == ident and instance_stopped(row) for row in instances()):
                raise
        until = time.monotonic() + 75
        while time.monotonic() < until:
            self.check()
            row = next((r for r in instances() if r["id"] == ident), None)
            if row and instance_stopped(row):
                store.save(self.journal, {**state, "recovery_stop": True, "stopped_at": time.time()})
                return
            time.sleep(3)
        raise RuntimeError(f"Vast did not confirm that instance {ident} stopped. Check its billing status in Vast.")

    def pause_and_restart(self, progress, percent):
        state = store.read(self.journal)
        ident = state["id"]
        self.stop_for_recovery()
        if self.recovery_attempted or self.deadline - time.monotonic() < 120:
            raise RuntimeError(f"Vast instance {ident} is stopped and its disk is preserved. "
                               "Automatic recovery has reached its retry or time limit. Check the instance in Vast.")
        self.recovery_attempted = True
        progress("Vast GPU stopped. Keeping files on disk and retrying in one minute", percent)
        wake = time.monotonic() + 60
        while time.monotonic() < wake:
            self.check()
            time.sleep(min(3, max(0, wake - time.monotonic())))
        self.restart_preserved(state)
        progress("Same Vast rental is back. Resuming from saved checks and clips", percent)

    def resume_after_review(self, paused_seconds):
        self.deadline += paused_seconds
        state = store.read(self.journal)
        if self.deadline - time.monotonic() < 120:
            raise RuntimeError(f"Vast instance {state['id']} remains stopped. The original rental window expired.")
        if "expires" in state:
            state["expires"] += paused_seconds
            store.save(self.journal, state)
        self.restart_preserved(state)

    def restart_preserved(self, state):
        require_resume_credit()
        ident = state["id"]
        try:
            request_instance_state("start", ident)
            until = min(self.deadline, time.monotonic() + 120)
            while time.monotonic() < until:
                self.check()
                row = next((r for r in instances() if r["id"] == ident), None)
                if row and row.get("actual_status") == "running":
                    for reported in (False, True):
                        try:
                            endpoint = Remote(row, self.key, self.temp, self.deadline, self.check,
                                              reported_endpoint=reported)
                            endpoint.phase("rm -f -- motion-request-*.json motion-request-*.staged motion.pid setup-motion.pid", 10)
                        except (RuntimeError, TimeoutError):
                            continue
                        self.host, self.port = endpoint.host, endpoint.port
                        store.save(self.journal, {key: value for key, value in state.items()
                                                  if key not in ("recovery_stop", "stopped_at")})
                        return
                time.sleep(3)
            raise RuntimeError(f"Vast instance {ident} could not restart. Its disk has been preserved.")
        except Exception:
            self.stop_for_recovery()
            raise

    def ssh(self, command, seconds=60):
        self.command("ssh", [self.host, command], seconds)

    def push(self, files):
        self.command("scp", [*map(str, files), f"{self.host}:{REMOTE}/"], 240)

    def pull(self, names, destination, subdir=None):
        source = f"{REMOTE}/results" + (f"/{subdir}" if subdir else "")
        for i in range(0, len(names), 12):
            batch = names[i:i+12]
            with tempfile.TemporaryDirectory(dir=destination) as staging:
                self.command("scp", [*[f"{self.host}:{source}/{n}" for n in batch],
                                     staging], 240)
                for name in batch:
                    path = Path(staging) / name
                    if not path.is_file() or path.stat().st_size == 0:
                        raise RuntimeError("Vast returned a missing or empty artifact.")
                    path.replace(destination / name)

    def stream_stills(self, manifest_name, names, destination):
        """Copy each atomically completed still while the same FLUX process renders the next."""
        batch = "stills-" + uuid.uuid4().hex
        command = f"bash worker.sh stills {shlex.quote(manifest_name)} {batch} >results/{batch}.log 2>&1"
        with ThreadPoolExecutor(max_workers=1) as worker:
            rendering = worker.submit(self.phase, command, 2400)
            for name in names:
                ready = shlex.quote(f"results/{batch}/{name}")
                while True:
                    self.check()
                    if time.monotonic() >= self.deadline:
                        raise TimeoutError("Vast stills reached their deadline.")
                    if rendering.done():
                        rendering.result()
                        break
                    try:
                        self.phase(f"test -s {ready}", 10)
                        break
                    except RuntimeError:
                        time.sleep(.5)
                self.pull([name], destination, subdir=batch)
                yield name
            rendering.result()

    def phase(self, command, seconds):
        self.ssh(f"cd {REMOTE} && {command}", seconds)

    def check_model_network(self):
        try:
            self.phase("curl -fsS --connect-timeout 6 --max-time 12 -o /dev/null "
                       "https://huggingface.co/api/models/black-forest-labs/FLUX.2-klein-4B", 16)
        except (RuntimeError, TimeoutError):
            raise RuntimeError("The Vast host cannot reach Hugging Face to download the models.") from None

    def start_motion_setup(self):
        self.phase("{ nohup bash worker.sh prepare-motion >results/setup-motion.log 2>&1 </dev/null "
                   "& echo $! > setup-motion.pid; }", 30)

    def wait_motion_setup(self):
        marker = {"wan22": "wan-env/.ready", "hunyuan15": "qwen-env/.hunyuan-ready", "ltx": "video-env/.ready"}[motion_model()]
        self.phase(f"while [ ! -f {marker} ]; do "
                   "ps -p $(cat setup-motion.pid) -o stat= | grep -q '^[^Z]' || exit 1; sleep 2; done", 1800)

def wait_for_startup_release(previous, journal, check, progress):
    """Reconcile an asynchronous stop before creating another paid contract."""
    if previous.get("ssh_ready_seconds") or not previous.get("recovery_stop"):
        raise RuntimeError("This rental has saved work. Resume it instead of replacing its host.")
    missing = 0
    for attempt in range(25):
        check()
        row = next((r for r in instances() if r["id"] == previous["id"]), None)
        if row is not None and row.get("label") != previous.get("label"):
            raise RuntimeError("Vast rental identity changed. No replacement was rented.")
        missing = missing + 1 if row is None else 0
        if missing >= 2 or (row and (instance_stopped(row) or stopped_failed_start(row))):
            confirmed = {**previous, "stop_confirmed_at": time.time()}
            if missing >= 2:
                confirmed.update(destroyed=True, missing_at=time.time())
            store.save(journal, confirmed)
            return confirmed
        if attempt % 6 == 0:
            progress("Startup failed · waiting for Vast to confirm the old rental stopped; saved script retained", 27)
        if attempt < 24:
            time.sleep(5)
    raise RuntimeError(
        "Vast container startup failed and its stop is still pending. Your script is saved. "
        "No second GPU was rented. Use Retry this step to recheck the stop and continue; "
        "you do not need to start a new video.")


def startup_progress(row, elapsed):
    status = (row or {}).get("actual_status", "pending")
    detail = ((row or {}).get("status_msg") or "").lower()
    if any(token in detail for token in ("get:", "apt-get", "packages", "unpacking")):
        phase = "installing container packages"
    elif any(token in detail for token in ("pulling", "downloading", "extracting")):
        phase = "downloading container image"
    else:
        phase = "waiting for container startup"
    return f"Vast {status} · {phase} · {int(elapsed)}s elapsed (video generation has not started)"


@contextmanager
def rent(folder, ident, check, *, progress=None, **options):
    """Try at most one replacement, only when the first host failed before setup."""
    journal = folder / "raghu-rental.json"
    recovery = folder / "vast-preserve-and-replace.json"
    recover = store.read(recovery) if recovery.is_file() else {}
    if recover.get("user_recovery") and journal.is_file():
        previous = store.read(journal)
        if previous.get("startup_failure") and previous.get("recovery_stop") and not previous.get("destroyed") and not previous.get("ssh_ready_seconds"):
            if previous["startup_failure"] in {"SSH key authentication failed", "SSH host identity check failed"}:
                raise RuntimeError("Vast SSH settings need an administrator to fix the key or host identity.")
            store.save(recovery, {**recover, "replace_instance": previous["id"]})
    report = progress or (lambda *_: None)
    try:
        with _rent_once(folder, ident, check, progress=report, **options) as remote:
            yield remote
        return
    except VastStartupError as error:
        if error.reason in {"SSH key authentication failed", "SSH host identity check failed"}:
            raise
        previous = store.read(journal)
        if recover.get("user_recovery") and previous.get("recovery_stop") and not previous.get("ssh_ready_seconds"):
            store.save(recovery, {**recover, "replace_instance": previous["id"]})
            report(f"Vast startup failed ({error.reason}). Checking another host within budget", 27)
            with _rent_once(folder, ident, check, progress=report, **options) as remote:
                yield remote
            return
        if not previous.get("destroyed") or previous.get("ssh_ready_seconds"):
            raise
        # Count all elapsed time conservatively as billed. No model files were transferred.
        spent = max(60, time.time() - previous["started"]) * float(previous["rate"]) / 3600
        store.save(folder / "raghu-first-startup-failure.json", {**previous, "estimated_cost": spent})
        report(f"Vast startup failed ({error.reason}). First rental released; checking another host", 27)
    with _rent_once(folder, ident, check, progress=report, startup_spent=spent, **options) as remote:
        yield remote


@contextmanager
def _rent_once(folder, ident, check, *, progress, image_only=False, pending_shots=None,
               resume_seconds=None, resume_window=True, startup_spent=0):
    journal = folder / "raghu-rental.json"
    previous = store.read(journal) if journal.exists() else {}
    model = motion_model()
    still_model = image_model()
    recovery = folder / "vast-preserve-and-replace.json"
    recovery_state = store.read(recovery) if recovery.is_file() else {}
    replacement_for = recovery_state.get("replace_instance")
    replace_failed_host = bool(replacement_for and previous.get("id") == replacement_for)
    if replace_failed_host:
        if resume_seconds:
            raise RuntimeError("A saved-frame continuation cannot replace its rental.")
        previous = wait_for_startup_release(previous, journal, check, progress)
        store.save(folder / f"vast-preserved-instance-{replacement_for}.json", previous)
        for path in folder.glob("vast-preserved-instance-*.json"):
            failed = store.read(path)
            startup_spent += float(failed["rate"]) * max(
                60, failed.get("stop_confirmed_at", time.time()) - failed["started"]) / 3600
    if resume_seconds and not previous.get("recovery_stop"):
        raise RuntimeError("No stopped Vast rental is available for this saved-frame continuation.")
    if previous.get("recovery_stop") and previous.get("id") and not replace_failed_host:
        if previous.get("motion_model", "ltx") != model:
            raise RuntimeError("The preserved rental uses a different motion model. Its disk is still saved. "
                               "Start a new Telegram production to use the deployed model.")
        if previous.get("image_model", "flux") != still_model:
            raise RuntimeError("The saved rental uses a different image model. Start a new Telegram production.")
        with tempfile.TemporaryDirectory(prefix="raghu-ssh-") as directory:
            temp = Path(directory)
            key = Path(os.environ["VAST_SSH_KEY_PATH"]).resolve() if os.getenv("VAST_SSH_KEY_PATH") else temp / "id_ed25519"
            if key.parent == temp:
                key.write_text(required("VAST_SSH_PRIVATE_KEY").replace("\\n", "\n").strip() + "\n",
                               encoding="ascii", newline="\n")
                key.chmod(0o600)
            remaining = float("inf")
            if resume_seconds:
                if type(resume_seconds) is not int or not 120 <= resume_seconds <= 3600:
                    raise RuntimeError("The saved-frame rental window is invalid.")
                if previous.get("saved_frame_window") != resume_window or "expires" not in previous:
                    previous.update(expires=time.time() + resume_seconds, saved_frame_window=resume_window)
                    store.save(journal, previous)
                remaining = previous["expires"] - time.time()
            elif "expires" in previous:
                # Normal production resumes ignore deadlines saved by older deployments.
                previous.pop("expires")
                previous.pop("saved_frame_window", None)
                store.save(journal, previous)
            if remaining < 120:
                raise RuntimeError(f"Vast instance {previous['id']} remains stopped. The original rental window expired.")
            rows = instances()
            row = next((row for row in rows if row["id"] == previous["id"]
                        and row.get("label") == previous["label"]), None)
            if not row or not instance_stopped(row):
                raise RuntimeError(f"Vast did not confirm preserved instance {previous['id']} is stopped.")
            remote = Remote(row, key, temp, time.monotonic() + remaining, check, connecting=False)
            remote.journal = journal
            remote.quantization = previous["quantization"]
            remote.restart_preserved(previous)
            try:
                remote.push([ASSETS / "stills.py"])
                yield remote
            finally:
                finish_rental(journal, recovery.is_file())
        return
    if previous and not previous.get("destroyed") and not replace_failed_host:
        destroy(journal)
    gpu = os.getenv("RAGHU_GPU", "RTX_4090")
    quantization = os.getenv("RAGHU_QUANTIZATION", "fp8")
    if quantization not in ("nvfp4", "fp8") or (quantization == "nvfp4" and gpu != "RTX_5090"):
        raise RuntimeError("Use RTX_5090 for the approved NVFP4 configuration.")
    models = [] if model != "ltx" else model_manifest(quantization)
    hf_token = None if image_only or model != "ltx" else required("HF_TOKEN")
    if hf_token:
        check_model_access(models, hf_token)
    # This estimate only screens offers. It is never a deadline for a running job.
    shots = 0 if image_only else (1 if pending_shots is None else pending_shots)
    estimate_minutes = 25 + (shots * 8 if model != "ltx" else (shots * 7 + 5) // 6)
    image_gb = (sum(item["bytes"] for item in store.read(ASSETS / "qwen-model-manifest.json")) / 1e9
                if still_model == "qwen2511" else 20)
    if model == "hunyuan15":
        image_files = store.read(ASSETS / "qwen-model-manifest.json") if still_model == "qwen2511" else []
        shared = {item["sha256"] for item in image_files}
        motion_gb = sum(item["bytes"] for item in store.read(ASSETS / "hunyuan-model-manifest.json")
                        if item["sha256"] not in shared) / 1e9
    else:
        motion_gb = 65 if model == "wan22" else sum(item["bytes"] for item in models) / 1e9
    download_gb = image_gb + (0 if image_only else motion_gb)
    if not image_only and os.getenv("RAGHU_NARRATION_PROVIDER", "").lower() == "chatterbox":
        download_gb += 4
    max_hourly = float(required("RAGHU_MAX_HOURLY_USD"))
    cap = float(required("RAGHU_MAX_JOB_USD"))
    min_bandwidth = float(os.getenv("RAGHU_MIN_DOWNLOAD_MBPS", "500"))
    excluded_regions = {region.strip().upper() for region in
                        os.getenv("RAGHU_EXCLUDED_GPU_REGIONS", "").split(",") if region.strip()}
    if not 0 < max_hourly <= 10 or not 0 < cap <= 100 or not 0 < min_bandwidth <= 10000:
        raise RuntimeError("Raghu rental budget variables are invalid.")
    with tempfile.TemporaryDirectory(prefix="raghu-ssh-") as temp_name:
        temp = Path(temp_name)
        key_path = os.getenv("VAST_SSH_KEY_PATH")
        key = Path(key_path).resolve() if key_path else temp / "id_ed25519"
        if not key_path:
            key.write_text(required("VAST_SSH_PRIVATE_KEY").replace("\\n", "\n").strip() + "\n", encoding="ascii", newline="\n")
            key.chmod(0o600)
        pub = subprocess.check_output(["ssh-keygen", "-y", "-f", str(key)],
                                      stderr=subprocess.DEVNULL, timeout=10).split()
        ensure_ssh_key(b" ".join(pub[:2]).decode("ascii"))
        encoded = base64.b64encode(b" ".join(pub[:2]) + b"\n").decode()
        start = temp / "onstart.sh"
        start.write_text(f"install -d -m 700 /root/.ssh\nprintf %s {encoded} | base64 -d >> /root/.ssh/authorized_keys\nchmod 600 /root/.ssh/authorized_keys\n", encoding="ascii")
        label = "railway-raghu-" + ident
        if replace_failed_host:
            label += f"-replacement-{replacement_for}" if recovery_state.get("user_recovery") else "-replacement"
        if journal.is_file() and store.read(journal).get("creation_uncertain"):
            raise RuntimeError("Previous rental outcome is uncertain. Check Vast instances before retrying; no new rental submitted.")
        state = {"label": label, "id": None, "destroyed": False}
        if recovery.is_file():
            state["recovery_stop"] = True
        if any(row.get("label") == state["label"] for row in instances()):
            raise RuntimeError("A rental already exists for this job. Reconcile it before retrying.")
        balance = vast("show", "user")
        balance = balance.get("user", balance)
        credit = float(balance.get("credit", 0))
        query = f"gpu_name={gpu} num_gpus=1 verified=true rentable=true rented=false reliability>=0.98 disk_space>=150"
        def quote(offer):
            return (float(offer["dph_total"]) * estimate_minutes / 60
                    + float(offer["inet_down_cost"]) * download_gb
                    + float(offer["inet_up_cost"]) * 4)

        failed_start = previous.get("id") and not previous.get("ssh_ready_seconds")
        excluded = previous.get("offer_id") if failed_start else None
        excluded_machine = previous.get("machine_id") if failed_start else None
        wait_until = time.monotonic() + (10 * 60 if previous.get("destroyed") and not startup_spent else 0)
        progress("Looking for a verified Vast GPU within budget", 27)
        while True:
            check()
            rows = vast("search", "offers", query, "--type", "on-demand", "--storage", "150",
                        "--order", "dph_total", "--limit", "100")
            offers = rows if isinstance(rows, list) else rows["offers"]
            eligible = [o for o in offers if o["id"] != excluded
                        and (not excluded_machine or o.get("machine_id") != excluded_machine)
                        and all(o.get(k) is not None for k in
                        ("dph_total", "inet_down_cost", "inet_up_cost"))
                        and (o.get("geolocation") or "").rsplit(",", 1)[-1].strip().upper() not in excluded_regions
                        and float(o["dph_total"]) <= max_hourly and float(o.get("inet_down", 0)) >= min_bandwidth
                        and float(o.get("cpu_ram", 0)) >= 60000 and float(o.get("cuda_max_good", 0)) >= 13
                        and quote(o) + startup_spent <= cap]
            if eligible or time.monotonic() >= wait_until:
                break
            for _ in range(6):
                check()
                time.sleep(5)
        if not eligible:
            if replace_failed_host:
                raise RuntimeError("The original Vast rental is preserved. No different host fits the remaining job budget.")
            if startup_spent:
                raise RuntimeError("The failed Vast rental was released. No replacement host fits the remaining job budget.")
            raise RuntimeError("No Vast offer fits the GPU, bandwidth and job budget settings.")
        offer = min(eligible, key=quote)
        check_available_credit(credit, quote(offer))
        started = time.time()
        state.update(rate=offer["dph_total"], gpu=gpu, quantization=quantization, motion_model=model,
                     image_model=still_model, offer_id=offer["id"], machine_id=offer.get("machine_id"), started=started)
        check()
        store.save(journal, state)
        try:
            state["id"] = create_rental(sorted(eligible, key=quote), state, journal, start, check, progress)
            store.save(journal, state)
            ready_until = time.monotonic() + 720
            running_since = None
            last_startup_report = 0
            last_reason = "container did not become ready"
            progress("Vast rental created · waiting for container startup", 27)
            while time.monotonic() < ready_until:
                check()
                row = next((r for r in instances() if r["id"] == state["id"]), None)
                if time.monotonic() - last_startup_report >= 30:
                    if not row or row.get("actual_status") != "running":
                        progress(startup_progress(row, time.time() - started), 27)
                    last_startup_report = time.monotonic()
                if row and row.get("actual_status") == "running":
                    if running_since is None:
                        running_since = time.monotonic()
                        progress("Vast container running · checking secure connection", 27)
                    endpoints, failures = set(), []
                    remote = None
                    for reported in (False, True):
                        try:
                            candidate = Remote(row, key, temp, float("inf"), check, reported_endpoint=reported)
                        except RuntimeError:
                            continue
                        endpoint = (candidate.host, candidate.port)
                        if endpoint in endpoints:
                            continue
                        endpoints.add(endpoint)
                        try:
                            candidate.ssh(f"umask 077; mkdir -p {REMOTE}/results", 12)
                        except VastConnectionLost as error:
                            failures.append(str(error))
                        except TimeoutError:
                            failures.append("SSH port did not respond")
                        else:
                            remote = candidate
                            break
                    if remote:
                        break
                    last_reason = (next((reason for reason in failures if "identity check" in reason), None)
                                   or next((reason for reason in failures if "authentication" in reason), None)
                                   or (failures[-1] if failures else "Vast did not publish an SSH endpoint"))
                    if time.monotonic() - running_since >= 75 or "identity check" in last_reason:
                        store.save(journal, {**state, "startup_failure": last_reason})
                        raise VastStartupError(last_reason)
                elif row and row.get("actual_status") in {"exited", "offline"}:
                    last_reason = f"container went {row['actual_status']} during startup"
                    store.save(journal, {**state, "startup_failure": last_reason})
                    raise VastStartupError(last_reason)
                time.sleep(4)
            else:
                store.save(journal, {**state, "startup_failure": last_reason})
                raise VastStartupError(last_reason)
            state["ssh_ready_seconds"] = round(time.time() - state["started"], 1)
            store.save(journal, state)
            progress("Vast SSH connected · preparing generation tools", 27)
            remote.check_model_network()
            worker = temp / "worker.sh"
            worker.write_text((ASSETS / "worker.sh").read_text(encoding="utf-8-sig"),
                              encoding="utf-8", newline="\n")
            model_file = temp / "model-manifest.json"
            store.save(model_file, models)
            runtime = temp / "runtime.sh"
            runtime.write_text(f"export LTX_QUANTIZATION={quantization}\nexport RAGHU_MOTION_MODEL={model}\n"
                               f"export RAGHU_IMAGE_MODEL={still_model}\n",
                               encoding="ascii", newline="\n")
            remote.push([worker, model_file, runtime, *[ASSETS / name for name in ("common.py", "stills.py", "videos.py",
                "download_video_only.py", "wan_worker.py", "wan_setup.py",
                "qwen_stills.py", "qwen-model-manifest.json", "hunyuan_worker.py", "hunyuan-model-manifest.json",
                "style-wide.jpg", "style-interior.jpg")]])
            if hf_token:
                hf = temp / ".env"
                hf.write_text("HF_TOKEN=" + hf_token + "\n", encoding="ascii")
                hf.chmod(0o600)
                remote.push([hf])
                remote.phase("chmod 600 .env", 20)
            remote.journal = journal
            try:
                yield remote
            except Exception:
                for log in ("events.jsonl", "motion.log", "setup-motion.log", "kernels.log", "comfy.log"):
                    try:
                        remote.pull([log], folder)
                    except (RuntimeError, TimeoutError):
                        pass
                raise
        finally:
            finish_rental(journal, recovery.is_file())
