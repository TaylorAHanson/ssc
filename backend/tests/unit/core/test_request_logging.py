"""Request-scoped log context: roles on every line, the request id header, job tags."""
import io
import json
import logging

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.logging_formatter import AppLoggingFormatter, job_log_context, note_request_identity
from app.middleware.auth import AuthMiddleware

log = logging.getLogger("tests.request_logging")


def _sync_dependency_with_roles() -> None:
    # Sync dependencies run in a worker thread on a copy of the context, as get_current_user does.
    note_request_identity(["User", "Data Steward"], role_override="Data Steward")


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping(_=Depends(_sync_dependency_with_roles)):
        log.info("inside the handler")
        return {"ok": True}

    @app.get("/assets/app.js")
    def asset():
        return {"ok": True}

    app.add_middleware(AuthMiddleware)
    return app


class _Capture:
    """Formats records as they are emitted, while the request's context is still live."""

    def __enter__(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(AppLoggingFormatter())
        root = logging.getLogger()
        self.old_level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        root = logging.getLogger()
        root.removeHandler(self.handler)
        root.setLevel(self.old_level)

    def records(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line.strip()]


def test_roles_set_in_a_dependency_reach_every_later_line_and_the_access_line():
    with _Capture() as cap:
        response = TestClient(_app()).get("/api/v1/ping")
    assert response.status_code == 200

    handler_line = next(r for r in cap.records() if r["message"] == "inside the handler")
    assert handler_line["roles"] == ["Data Steward", "User"]
    assert handler_line["role_override"] == "Data Steward"

    access = next(r for r in cap.records() if r["message"].startswith("HTTP GET /api/v1/ping"))
    assert access["level"] == "INFO"
    assert access["roles"] == ["Data Steward", "User"]
    assert access["data"]["status"] == 200
    assert access["data"]["duration_ms"] >= 0
    # The browser gets the id that names these lines.
    assert response.headers["x-request-id"] == access["req_id"]


def test_static_files_log_their_request_line_at_debug():
    with _Capture() as cap:
        TestClient(_app()).get("/assets/app.js")
    access = next(r for r in cap.records() if r["message"].startswith("HTTP GET /assets/app.js"))
    assert access["level"] == "DEBUG"
    assert "roles" not in access


def test_roles_do_not_leak_outside_a_request():
    note_request_identity(["Platform Admin"])  # no request in progress: ignored
    with _Capture() as cap:
        log.info("background line")
    (line,) = [r for r in cap.records() if r["message"] == "background line"]
    assert "roles" not in line


def test_job_context_tags_lines_with_the_job_and_a_run_id():
    with _Capture() as cap:
        with job_log_context("data_asset_sync", run_id="abc123"):
            log.info("syncing")
        log.info("after")
    syncing, after = [r for r in cap.records() if r["message"] in ("syncing", "after")]
    assert syncing["req_id"] == "data_asset_sync:abc123"
    assert syncing["path"] == "job:data_asset_sync"
    assert syncing["method"] == "JOB"
    assert after["req_id"] == "N/A" and after["path"] == "Background"
