"""Real loopback regressions for total HTTP deadline, never GitHub/model calls."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from my_wfc import app_auth
from my_wfc.budget import cleanup_budget, dispatch_budget
from my_wfc.controller import DispatchLedger, run_once
from my_wfc.github import WorkflowError
from my_wfc.service import WorkflowService


@pytest.fixture
def loopback(monkeypatch):
    stop = threading.Event()
    started = threading.Event()
    headers = threading.Event()
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            paths.append(self.path)
            started.set()
            try:
                if self.path == "/slow_headers":
                    stop.wait(10)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/fast")
                    self.end_headers()
                    return
                body = b'{"ok":true}'
                if self.path == "/invalid":
                    body = b"synthetic-secret-not-json"
                if self.path == "/oversize":
                    body = b"x" * 2_000_001
                if self.path == "/empty":
                    body = b""
                if self.path == "/trickle":
                    body = b'{"value":"' + b"x" * 512 + b'"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                headers.set()
                if self.path == "/trickle":
                    for byte in body:
                        if stop.wait(0.03):
                            break
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.05), daemon=True
    )
    thread.start()
    worker = Path(app_auth.__file__).with_name("http_transport.py")
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    # Inject loopback only into the test child. Production origin is fixed HTTPS;
    # no configurable origin, credentials or local-server hook is shipped there.
    code = (
        "import importlib.util; "
        f"spec=importlib.util.spec_from_file_location('http_worker',{str(worker)!r}); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        f"module.API_ORIGIN={origin!r}; module.main()"
    )
    command = [sys.executable, "-c", code]
    monkeypatch.setattr(app_auth, "_http_worker_command", lambda: command)
    popen = app_auth.subprocess.Popen
    processes = []

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(app_auth.subprocess, "Popen", launch)
    try:
        yield SimpleNamespace(
            started=started,
            headers=headers,
            paths=paths,
            processes=processes,
            command=command,
        )
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("endpoint", ["/slow_headers", "/trickle"])
def test_http_total_deadline_kills_and_reaps_request_worker(
    loopback, endpoint
):
    started = time.monotonic()
    with dispatch_budget(time.time() + 1.5):
        with pytest.raises(WorkflowError) as error:
            app_auth.request_json("GET", endpoint, "synthetic-secret-only")
    assert time.monotonic() - started < 4.5
    assert loopback.started.is_set()
    if endpoint == "/trickle":
        assert loopback.headers.is_set()
    assert loopback.paths == [endpoint]
    assert len(loopback.processes) == 1
    assert loopback.processes[0].poll() is not None
    assert loopback.processes[0].returncode != 0
    assert "synthetic-secret-only" not in str(error.value)
    assert all("synthetic-secret-only" not in arg for arg in loopback.command)


def test_fast_response_and_redirect_refusal(loopback):
    with dispatch_budget(time.time() + 3):
        assert app_auth.request_json("GET", "/fast", "synthetic") == {
            "ok": True
        }
        with pytest.raises(WorkflowError):
            app_auth.request_json("GET", "/redirect", "synthetic")
    assert loopback.paths == ["/fast", "/redirect"]
    assert all(process.poll() is not None for process in loopback.processes)


def test_trickle_timeout_runs_cleanup_and_blocks_dispatch(loopback, tmp_path):
    service = WorkflowService(
        tmp_path / "workflow.sqlite3", lambda *args: None
    )
    service.provision_task(
        {
            "task_id": "HTTP-DEADLINE",
            "title": "Synthetic HTTP test",
            "description": "Loopback only",
            "acceptance_criteria": ["AC4"],
            "definition_of_done": "No unbounded IO",
            "repository": app_auth.REPOSITORY,
            "branch": "codex/http-fixture",
            "base_sha": "a" * 40,
            "requested_effort": "high",
            "risk_level": "high",
            "require_verified_effort": True,
            "context_paths": ["docs/handoff.md"],
            "synthetic": True,
        }
    )
    effects = []
    # A normal 60s dispatch whose remaining budget is deliberately 1.5s.
    ledger = DispatchLedger(service, clock=lambda: time.time() - 58.5)

    def execute(claim, context):
        try:
            app_auth.request_json("GET", "/trickle", "synthetic-only")
            effects.extend(["ref", "pr", "handoff"])
            return "b" * 40
        finally:
            with cleanup_budget():
                effects.append("cleanup")

    started = time.monotonic()
    with pytest.raises(WorkflowError):
        run_once(service, ledger, {"HTTP-DEADLINE"}, execute, timeout=60)
    assert time.monotonic() - started < 4.5
    assert loopback.headers.is_set()
    assert effects == ["cleanup"]
    assert all(process.poll() is not None for process in loopback.processes)
    with service.connection() as db:
        dispatch = dict(
            db.execute("SELECT * FROM coder_dispatches").fetchone()
        )
    assert dispatch["status"] == "BLOCKED" and dispatch["slot"] == 1
    context = service.get_review_context("HTTP-DEADLINE")
    assert context["status"] == "AWAITING_HANDOFF"
    assert context["completed_review_rounds"] == 0
    assert not run_once(service, ledger, {"HTTP-DEADLINE"}, execute)


@pytest.mark.parametrize("endpoint", ["/invalid", "/oversize"])
def test_worker_response_limits_and_errors_are_sanitized(loopback, endpoint):
    with dispatch_budget(time.time() + 3):
        with pytest.raises(WorkflowError) as error:
            app_auth.request_json("GET", endpoint, "synthetic-secret-only")
    assert "synthetic-secret" not in str(error.value)
    assert all(process.poll() is not None for process in loopback.processes)


def test_empty_worker_response_remains_none(loopback):
    with dispatch_budget(time.time() + 3):
        assert app_auth.request_json("GET", "/empty", "synthetic") is None
    assert loopback.processes[0].returncode == 0


@pytest.mark.parametrize(
    "endpoint", ["//external.example/path", "https://external.example"]
)
def test_invalid_endpoint_never_starts_http_worker(loopback, endpoint):
    with pytest.raises(WorkflowError):
        app_auth.request_json("GET", endpoint, "synthetic")
    assert loopback.processes == []
    assert loopback.paths == []
