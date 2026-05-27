"""
Embedded OPA server lifecycle manager.

We spawn `opa run --server` as a child of the FastAPI process at startup
and shut it down on app exit. The motivation: each invocation of
`opa eval` (the previous per-call code path) costs ~25 ms in subprocess
spawn + bundle load. At thousands of resources that's minutes of pure
overhead. With a long-running server we keep policies loaded in memory
and answer evaluations over localhost HTTP in ~1 ms.

This module deliberately keeps no FastAPI dependency — it can be
started/stopped from any process or test harness. The provider picks
up the URL via `get_opa_url()` (see `core.config.opa_provider_config`).

Behaviour notes:
- Uses an ephemeral port (port=0) so concurrent restarts under
  `uvicorn --reload` don't collide.
- If the OPA binary cannot be resolved we fail soft: log a warning and
  leave `url` as None so the provider falls back to per-call CLI mode.
- Policies are loaded once at server start; .rego edits require an app
  restart to take effect. Acceptable for an internal governance tool.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from threading import Lock
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class EmbeddedOpaServer:
    """Owns the lifecycle of an in-process `opa run --server` subprocess."""

    def __init__(self, policies_dir: str, opa_binary: str):
        self.policies_dir = policies_dir
        self.opa_binary = opa_binary
        self.process: Optional[subprocess.Popen] = None
        self.url: Optional[str] = None
        self._lock = Lock()

    @staticmethod
    def _pick_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def start(self, wait_seconds: float = 8.0) -> Optional[str]:
        """Spawn the OPA server and block until it's ready (or fail)."""
        with self._lock:
            if self.process and self.process.poll() is None:
                logger.info("Embedded OPA server already running at %s", self.url)
                return self.url

            port = self._pick_free_port()
            addr = f"127.0.0.1:{port}"
            url = f"http://{addr}"

            cmd = [
                self.opa_binary,
                "run",
                "--server",
                "--addr",
                addr,
                "--log-level",
                "error",
                self.policies_dir,
            ]

            logger.info("Starting embedded OPA server: %s", " ".join(cmd))
            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    # New session so we can kill the whole group if needed
                    # and so OPA isn't accidentally killed by terminal SIGINT
                    # before our shutdown handler runs.
                    start_new_session=True,
                )
            except FileNotFoundError:
                logger.warning(
                    "Embedded OPA binary not found at %s; falling back to per-call CLI mode.",
                    self.opa_binary,
                )
                self.process = None
                return None
            except Exception as e:
                logger.error("Failed to spawn OPA server: %s", e)
                self.process = None
                return None

            # Health-poll until ready or process dies.
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                try:
                    r = httpx.get(f"{url}/health", timeout=0.5)
                    if r.status_code == 200:
                        self.url = url
                        logger.info(
                            "Embedded OPA server ready at %s (pid=%s, policies=%s)",
                            url,
                            self.process.pid,
                            self.policies_dir,
                        )
                        return url
                except httpx.HTTPError:
                    pass

                # Did OPA crash on startup? Surface stderr and bail out.
                if self.process.poll() is not None:
                    err_bytes = b""
                    try:
                        if self.process.stderr:
                            err_bytes = self.process.stderr.read() or b""
                    except Exception:
                        pass
                    logger.error(
                        "OPA server exited during startup (rc=%s): %s",
                        self.process.returncode,
                        err_bytes.decode("utf-8", errors="replace")[:500],
                    )
                    self.process = None
                    return None

                time.sleep(0.05)

            logger.error(
                "Embedded OPA server did not become ready within %ss",
                wait_seconds,
            )
            self._terminate_locked()
            return None

    def stop(self) -> None:
        with self._lock:
            self._terminate_locked()

    def _terminate_locked(self) -> None:
        if not self.process:
            return
        logger.info("Stopping embedded OPA server (pid=%s)", self.process.pid)
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("OPA did not terminate cleanly; sending SIGKILL")
                self.process.kill()
                self.process.wait(timeout=2)
        except Exception as e:
            logger.warning("Error stopping OPA: %s", e)
        finally:
            self.process = None
            self.url = None


# Module-level singleton accessed by the provider config layer.
_server: Optional[EmbeddedOpaServer] = None


def start_embedded_opa(policies_dir: str, opa_binary: str) -> Optional[str]:
    """Start (or reuse) the singleton embedded OPA server.

    Returns the base URL (e.g. ``http://127.0.0.1:51234``) on success, or
    ``None`` if startup failed. Callers should treat ``None`` as a signal
    to fall back to per-call CLI evaluation.
    """
    global _server
    if _server is None:
        _server = EmbeddedOpaServer(policies_dir, opa_binary)
    return _server.start()


def stop_embedded_opa() -> None:
    """Terminate the embedded OPA server if it's running. Safe to call
    multiple times and from any thread."""
    global _server
    if _server is not None:
        _server.stop()


def get_opa_url() -> Optional[str]:
    """Return the URL of the embedded OPA server if it's running, else None.

    The provider config consults this to decide between the long-running
    HTTP path (`_evaluate_remote`) and the per-call CLI path
    (`_evaluate_local`).
    """
    return _server.url if _server is not None else None
