"""
Inline "run one Databricks job as a single step" mixin.

``BaseDatabricksJobStateMachine`` covers the case where the *entire* workflow
is a Databricks job. Many real workflows are mostly app-side but need one
discrete step to run on a Databricks cluster — sending an email via SES,
looking a user up in LDAP, calling a control-plane-only API, etc.

``DatabricksJobStepMixin`` provides ``run_databricks_job_step(step_id=..., ...)``
for that case. Call it from any ``on_enter_<state>_async`` hook; it handles
idempotent submit, polling, and fact-writes scoped to the step. The caller
controls its own transitions by checking ``step_completed(step_id)`` /
``step_failed(step_id)`` in transition guards.

### Facts written (prefix-style, scoped by step_id)

* ``step:<id>:submitted`` — first visit, after the job is in flight.
  ``event_data``: ``{run_id, remote_path, compute, submitted_at}``
* ``step:<id>:completed`` — terminal success.
  ``event_data``: ``{run_id, output, completed_at}``
* ``step:<id>:failed`` — terminal failure (job error OR submit error).
  ``event_data``: ``{run_id?, error, failed_at}``

Step failure does *not* automatically call ``self.mark_failed()`` — the caller
decides whether a failed step is fatal to the whole workflow or whether to
take an alternate path (e.g. fall back to a sync provider, skip optional
notification, etc.).

### Example

::

    class ProvisionWorkspaceStateMachine(BaseRequestStateMachine, DatabricksJobStepMixin):
        provisioning = State("provisioning")
        notifying_owner = State("notifying_owner")
        completed = State("completed", final=True)

        send = provisioning.to(notifying_owner, cond="has_workspace_created")
        finish = notifying_owner.to(completed, cond="step_email_completed")

        async def on_enter_notifying_owner_async(self):
            await self.run_databricks_job_step(
                step_id="email",
                notebook_path="send_email_job.py",   # relative to this file
                parameters={"to": ..., "subject": ..., "body": ...},
                compute=default_classic_compute(...),
            )

        @property
        def step_email_completed(self) -> bool:
            return self.step_completed("email")
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.providers.databricks.client import DatabricksProvider
from app.providers.databricks.compute import ComputeSpec
from app.state_machines.facts import add_fact, get_latest_fact, has_fact

logger = logging.getLogger(__name__)


REMOTE_NOTEBOOK_DIR = "/Shared/agents"


class DatabricksJobStepMixin:
    """Mixin granting a state machine the ability to run a single Databricks job step.

    Designed to be mixed into any subclass of ``BaseRequestStateMachine``.
    Expects the host class to expose ``self.request`` and ``self.db`` (which
    every ``BaseRequestStateMachine`` does).
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_databricks_job_step(
        self,
        *,
        step_id: str,
        notebook_path: Optional[str] = None,
        python_code: Optional[str] = None,
        parameters: Union[Dict[str, str], List[str], None] = None,
        compute: Optional[ComputeSpec] = None,
        run_name: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> None:
        """Idempotently submit + poll a Databricks job for ``step_id``.

        First call (when no ``step:<id>:submitted`` fact exists) uploads the
        code, submits the job, and writes the ``submitted`` fact. Subsequent
        calls (same step, same request, later poller cycles) poll the run and
        write ``completed`` or ``failed`` once terminal.

        This method always returns without raising on submit/poll errors —
        those become ``step:<id>:failed`` facts. Callers detect failures via
        ``self.step_failed(step_id)`` and decide policy.

        Args:
            step_id: Short identifier unique within this workflow. Used to
                namespace facts (``step:<step_id>:submitted`` etc.).
            notebook_path: Local path to a notebook file. Resolved relative to
                the *subclass's* module dir unless absolute.
            python_code: Inline Python source for a SparkPythonTask. Mutually
                exclusive with ``notebook_path``.
            parameters: ``dict`` for notebook widget values, ``list[str]`` for
                python CLI args. Required if the task needs inputs.
            compute: Where to run; ``None`` = serverless.
            run_name: Display name for the Databricks UI. Defaults to a
                generated name based on the request id and step_id.
            timeout_seconds: Optional job-level timeout passed to the SDK.
        """
        if (notebook_path and python_code) or (not notebook_path and not python_code):
            raise ValueError(
                f"step '{step_id}' requires exactly one of notebook_path / python_code."
            )

        if self.step_completed(step_id) or self.step_failed(step_id):
            return  # terminal, nothing to do

        if not self.step_submitted(step_id):
            await self._submit_step(
                step_id=step_id,
                notebook_path=notebook_path,
                python_code=python_code,
                parameters=parameters,
                compute=compute,
                run_name=run_name or self._default_step_run_name(step_id),
                timeout_seconds=timeout_seconds,
            )
            return

        await self._poll_step(step_id)

    # ------------------------------------------------------------------
    # Inspection helpers (use these in transition guards / on_enter hooks)
    # ------------------------------------------------------------------

    def step_submitted(self, step_id: str) -> bool:
        return has_fact(self.db, self.request.id, self._fact_name(step_id, "submitted"))

    def step_completed(self, step_id: str) -> bool:
        return has_fact(self.db, self.request.id, self._fact_name(step_id, "completed"))

    def step_failed(self, step_id: str) -> bool:
        return has_fact(self.db, self.request.id, self._fact_name(step_id, "failed"))

    def step_terminal(self, step_id: str) -> bool:
        """True if the step has either completed or failed."""
        return self.step_completed(step_id) or self.step_failed(step_id)

    def get_step_run_id(self, step_id: str) -> Optional[str]:
        fact = get_latest_fact(self.db, self.request.id, self._fact_name(step_id, "submitted"))
        return fact.event_data.get("run_id") if fact else None

    def get_step_output(self, step_id: str) -> Optional[Dict[str, Any]]:
        """Returns the task output payload captured at completion, or None."""
        fact = get_latest_fact(self.db, self.request.id, self._fact_name(step_id, "completed"))
        return fact.event_data.get("output") if fact else None

    def get_step_error(self, step_id: str) -> Optional[str]:
        fact = get_latest_fact(self.db, self.request.id, self._fact_name(step_id, "failed"))
        return fact.event_data.get("error") if fact else None

    # ------------------------------------------------------------------
    # Overridable hooks
    # ------------------------------------------------------------------

    def build_databricks_provider(self) -> DatabricksProvider:
        """Build a Databricks provider. Override for per-workspace targeting."""
        return DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID},
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _fact_name(step_id: str, status: str) -> str:
        return f"step:{step_id}:{status}"

    def _default_step_run_name(self, step_id: str) -> str:
        return f"{self.__class__.__name__}[{step_id}]: {self.request.id}"

    def _resolve_local_notebook_path(self, notebook_path: str) -> str:
        """Resolve ``notebook_path`` against the subclass's module dir.

        Absolute paths are returned unchanged.
        """
        if os.path.isabs(notebook_path):
            return notebook_path
        module = sys.modules.get(self.__class__.__module__)
        if module is None or not getattr(module, "__file__", None):
            raise RuntimeError(
                f"Cannot resolve relative notebook path for {self.__class__.__name__}: "
                "module has no __file__."
            )
        module_dir = os.path.dirname(os.path.abspath(module.__file__))
        return os.path.join(module_dir, notebook_path)

    async def _submit_step(
        self,
        *,
        step_id: str,
        notebook_path: Optional[str],
        python_code: Optional[str],
        parameters: Union[Dict[str, str], List[str], None],
        compute: Optional[ComputeSpec],
        run_name: str,
        timeout_seconds: Optional[int],
    ) -> None:
        provider = self.build_databricks_provider()
        try:
            if notebook_path:
                if parameters is not None and not isinstance(parameters, dict):
                    raise TypeError(
                        f"step '{step_id}': notebook job parameters must be a dict."
                    )
                local_path = self._resolve_local_notebook_path(notebook_path)
                remote_path = (
                    f"{REMOTE_NOTEBOOK_DIR}/{self._workflow_slug()}_{self.request.id}_{step_id}"
                )
                logger.info(
                    f"[{self.request.id}] step '{step_id}': uploading {local_path} -> {remote_path}"
                )
                await provider.import_notebook(local_path, remote_path)
                run_id = await provider.submit_job(
                    notebook_task={
                        "notebook_path": remote_path,
                        "base_parameters": parameters or {},
                    },
                    run_name=run_name,
                    compute=compute,
                    timeout_seconds=timeout_seconds,
                )
                remote_location = remote_path
            else:
                if parameters is not None and not isinstance(parameters, list):
                    raise TypeError(
                        f"step '{step_id}': python job parameters must be a list[str]."
                    )
                workspace_path = await provider.upload_python_script(python_code)
                run_id = await provider.submit_job(
                    spark_python_task={
                        "python_file": workspace_path,
                        "parameters": parameters or [],
                    },
                    run_name=run_name,
                    compute=compute,
                    timeout_seconds=timeout_seconds,
                )
                remote_location = workspace_path

            add_fact(
                self.db, self.request.id, self._fact_name(step_id, "submitted"),
                {
                    "run_id": run_id,
                    "remote_path": remote_location,
                    "compute": "classic" if compute and not compute.is_serverless else "serverless",
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                },
                actor="system",
            )
            logger.info(f"[{self.request.id}] step '{step_id}': submitted run_id={run_id}")

        except Exception as e:
            logger.error(f"[{self.request.id}] step '{step_id}': submit failed: {e}")
            add_fact(
                self.db, self.request.id, self._fact_name(step_id, "failed"),
                {"error": str(e), "failed_at": datetime.now(timezone.utc).isoformat()},
                actor="system",
            )

    async def _poll_step(self, step_id: str) -> None:
        provider = self.build_databricks_provider()
        run_id = self.get_step_run_id(step_id)
        if not run_id:
            return  # defensive: submitted fact present but no run_id

        try:
            status = await provider.get_run_status(run_id)
        except PermanentError as e:
            logger.error(f"[{self.request.id}] step '{step_id}': permanent poll error: {e}")
            add_fact(
                self.db, self.request.id, self._fact_name(step_id, "failed"),
                {"run_id": run_id, "error": str(e),
                 "failed_at": datetime.now(timezone.utc).isoformat()},
                actor="system",
            )
            return
        except RetryableError:
            # Let the poller retry; do not mark failed.
            raise
        except Exception as e:
            logger.warning(f"[{self.request.id}] step '{step_id}': transient poll error: {e}")
            return

        if not status["is_completed"]:
            return  # still running; check again next tick

        if status["is_successful"]:
            output: Dict[str, Any] = {}
            try:
                output = await provider.get_run_output(run_id)
            except Exception as e:
                logger.warning(
                    f"[{self.request.id}] step '{step_id}': could not fetch run output: {e}"
                )
            add_fact(
                self.db, self.request.id, self._fact_name(step_id, "completed"),
                {"run_id": run_id, "output": output,
                 "completed_at": datetime.now(timezone.utc).isoformat()},
                actor="system",
            )
            logger.info(f"[{self.request.id}] step '{step_id}': completed run_id={run_id}")
        else:
            add_fact(
                self.db, self.request.id, self._fact_name(step_id, "failed"),
                {"run_id": run_id, "error": status.get("state_message"),
                 "failed_at": datetime.now(timezone.utc).isoformat()},
                actor="system",
            )
            logger.warning(
                f"[{self.request.id}] step '{step_id}': failed run_id={run_id}: "
                f"{status.get('state_message')}"
            )

    def _workflow_slug(self) -> str:
        """Short identifier used in the remote notebook path."""
        parts = self.__class__.__module__.split(".")
        # For "app.state_machines.foo.state_machine" → "foo".
        # For ad-hoc modules, fall back to the leaf module name.
        return parts[-2] if len(parts) >= 2 else parts[-1]
