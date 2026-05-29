"""
Reusable state machine for workflows whose work is a single Databricks Job.

Many of our workflows boil down to "run some code on a Databricks cluster and
react to the result": asset deduplication, scheduled governance scans, and
control-plane providers like email/SES and LDAP. The state transitions
(pending -> job_submitted -> job_complete -> notifying -> completed) are
identical; only the *spec* of the job and the post-run handling differ.

``BaseDatabricksJobStateMachine`` factors that lifecycle out. A subclass
declares:

  * ``NOTEBOOK_PATH`` (a local .py notebook to upload) **or** overrides
    ``build_python_code()`` to return inline Python source.
  * ``build_job_parameters(self)`` — values the job needs (dict of widgets
    for notebooks, list of CLI args for python scripts).
  * ``build_run_name(self)`` — label shown in the Databricks UI.
  * Optional: ``build_compute(self) -> ComputeSpec | None`` to override the
    default compute target. Default = serverless. Set
    ``USE_CLASSIC_COMPUTE = True`` on the subclass to get the configured
    classic compute spec for control-plane workloads (email/LDAP).
  * Optional: ``on_job_completed_async(self)`` — custom result handling.
    Default writes a ``results_fetched`` fact with task values / notebook
    output and proceeds to notification.
  * Optional: ``send_notification_async(self)`` — custom notification.
    Default sends a small completion email when ``email`` is in
    ``state_context``.

### Single-step variant

If your workflow is mostly app-side and you only need *one* step to dispatch
to a Databricks job, mix ``DatabricksJobStepMixin`` into your own state
machine instead of subclassing this base. See its module docstring for
examples. This base class is built on top of that mixin — they share the
underlying submit/poll/idempotency primitives.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from statemachine import State

from app.core.config import settings
from app.models.request import RequestStatus
from app.providers.databricks.compute import ComputeSpec, default_classic_compute
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.databricks_job_step import DatabricksJobStepMixin
from app.state_machines.facts import add_fact, has_fact

logger = logging.getLogger(__name__)


# The single job in this base class always uses this step id. Subclasses
# never need to reference it directly — backwards-compat properties below
# wrap the mixin's step_* helpers.
_MAIN_STEP = "main"


class BaseDatabricksJobStateMachine(DatabricksJobStepMixin, BaseRequestStateMachine):
    """Lifecycle: ``pending -> job_submitted -> job_complete -> notifying -> completed/failed``.

    Subclasses customise *what* runs and *what to do with the result*. The
    transition graph, polling cadence, fact taxonomy, and error handling are
    fixed so every consumer of this base looks identical from an operator's
    perspective.
    """

    # --- States (shared across all subclasses) ---
    pending = State("pending", initial=True)
    job_submitted = State("job_submitted")
    job_complete = State("job_complete")
    notifying = State("notifying")
    completed = State("completed", final=True)
    failed = State("failed", final=True)

    # --- Status mapping ---
    STATUS_MAPPING = {
        "pending": RequestStatus.PENDING,
        "job_submitted": RequestStatus.PROVISIONING,
        "job_complete": RequestStatus.PROVISIONING,
        "notifying": RequestStatus.PROVISIONING,
        "completed": RequestStatus.COMPLETED,
        "failed": RequestStatus.FAILED,
    }

    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        # Consider the submitted state "provisioned" once we hold a run_id;
        # the actual finish gates on step:main:completed below.
        "job_submitted": f"step:{_MAIN_STEP}:submitted",
        "job_complete": f"step:{_MAIN_STEP}:completed",
        "notifying": "notification_sent",
    }

    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "job_submitted": [f"step:{_MAIN_STEP}:submitted", f"step:{_MAIN_STEP}:failed"],
        "job_complete": [
            f"step:{_MAIN_STEP}:completed",
            "results_fetched",
            "results_summary",
            "results_fetch_failed",
        ],
        "notifying": ["notification_sent", "notification_skipped", "notification_failed"],
    }

    # --- Transitions ---
    submit = pending.to(job_submitted, cond="has_request_submitted")
    finish_job = job_submitted.to(job_complete, cond="has_job_completed")
    notify = job_complete.to(notifying, cond="has_results_fetched")
    complete_request = notifying.to(completed)

    mark_failed = (
        pending.to(failed)
        | job_submitted.to(failed)
        | job_complete.to(failed)
        | notifying.to(failed)
    )

    # ------------------------------------------------------------------
    # Subclass extension points
    # ------------------------------------------------------------------

    #: Optional path to a local notebook (relative to the subclass's module).
    #: Mutually exclusive with overriding ``build_python_code()``.
    NOTEBOOK_PATH: Optional[str] = None

    #: Set to True to run on classic compute (uses ``default_classic_compute``
    #: with the ``DATABRICKS_JOB_*`` settings). Default = serverless.
    USE_CLASSIC_COMPUTE: bool = False

    def build_python_code(self) -> Optional[str]:
        """Return inline Python source for a ``spark_python_task``.

        Override this *instead of* setting ``NOTEBOOK_PATH`` if you want to
        ship code as a string rather than as a notebook file in the repo.
        """
        return None

    def build_job_parameters(self):
        """Return the parameters this job needs.

        Notebook jobs expect a ``Dict[str, str]`` (widget name -> value).
        Python script jobs expect a ``List[str]`` (positional CLI args).
        """
        return {} if self.NOTEBOOK_PATH else []

    def build_run_name(self) -> str:
        """Return the display name shown in the Databricks Runs UI."""
        return f"{self.__class__.__name__}: {self.request.id}"

    def build_compute(self) -> Optional[ComputeSpec]:
        """Return the compute target. Default honours ``USE_CLASSIC_COMPUTE``.

        Override when a workflow needs cluster overrides (libraries, larger
        nodes, etc.).
        """
        if not self.USE_CLASSIC_COMPUTE:
            return None
        return default_classic_compute(
            spark_version=settings.DATABRICKS_JOB_SPARK_VERSION,
            node_type_id=settings.DATABRICKS_JOB_NODE_TYPE_ID,
            num_workers=settings.DATABRICKS_JOB_NUM_WORKERS,
            existing_cluster_id=settings.DATABRICKS_JOB_CLUSTER_ID or None,
            instance_pool_id=settings.DATABRICKS_JOB_INSTANCE_POOL_ID or None,
        )

    async def on_job_completed_async(self) -> None:
        """Handle a successful job run.

        Default behaviour: read the captured task output (notebook return
        value, logs, task values) from the ``step:main:completed`` fact and
        write a ``results_fetched`` fact mirroring it. Override to query a
        Delta table, parse structured output, etc.

        Implementations MUST write a ``results_fetched`` fact to advance the
        state machine to ``notifying``.
        """
        output = self.get_step_output(_MAIN_STEP) or {}
        add_fact(
            self.db, self.request.id, "results_fetched",
            {"run_id": self.get_step_run_id(_MAIN_STEP), "output": output},
            actor="system",
        )

    async def send_notification_async(self) -> None:
        """Send a notification after the job completes.

        Default: send a short completion email to ``state_context['email']``
        when present; otherwise record a ``notification_skipped`` fact.
        Subclasses can override to render a richer message.
        """
        ctx = self.request.state_context or {}
        recipient = ctx.get("email") or self.request.requester_email

        if not recipient:
            add_fact(
                self.db, self.request.id, "notification_skipped",
                {"reason": "no_email_provided"}, actor="system",
            )
            return

        subject = f"{self.build_run_name()} — Completed"
        body = (
            f"<p>Request <code>{self.request.id}</code> completed.</p>"
            f"<p>See the request detail page for full results.</p>"
        )
        try:
            await self._send_notification(subject=subject, body=body, to_email=recipient)
            add_fact(
                self.db, self.request.id, "notification_sent",
                {"recipient": recipient, "sent_at": datetime.now(timezone.utc).isoformat()},
                actor="system",
            )
        except Exception as e:
            logger.error(f"[{self.request.id}] Notification failed: {e}")
            add_fact(
                self.db, self.request.id, "notification_failed",
                {"error": str(e)}, actor="system",
            )

    # ------------------------------------------------------------------
    # Lifecycle implementation (subclasses generally don't touch these)
    # ------------------------------------------------------------------

    def _process_current_state(self) -> bool:
        """Drive fact-based transitions for the job lifecycle."""
        changed = super()._process_current_state()

        state = self.current_state_value
        if state == "job_submitted":
            if self.step_completed(_MAIN_STEP):
                self.finish_job()
                changed = True
            elif self.step_failed(_MAIN_STEP):
                self.mark_failed()
                changed = True
        elif state == "job_complete" and self.has_results_fetched:
            self.notify()
            changed = True
        elif state == "notifying" and self._notification_terminated():
            # Auto-advance once notification has resolved — sent, explicitly
            # skipped, or failed. Notification failure is logged but is not
            # fatal to the request itself.
            self.complete_request()
            changed = True

        return changed

    def _notification_terminated(self) -> bool:
        return (
            has_fact(self.db, self.request.id, "notification_sent")
            or has_fact(self.db, self.request.id, "notification_skipped")
            or has_fact(self.db, self.request.id, "notification_failed")
        )

    async def on_enter_job_submitted_async(self) -> None:
        """Submit the single job using the mixin's idempotent step runner."""
        notebook_path = self.NOTEBOOK_PATH
        python_code = self.build_python_code()
        if notebook_path and python_code:
            raise ValueError(
                f"{self.__class__.__name__} declares both NOTEBOOK_PATH and "
                "build_python_code() — pick one."
            )
        if not notebook_path and not python_code:
            raise NotImplementedError(
                f"{self.__class__.__name__} must set NOTEBOOK_PATH or override "
                "build_python_code()."
            )

        await self.run_databricks_job_step(
            step_id=_MAIN_STEP,
            notebook_path=notebook_path,
            python_code=python_code,
            parameters=self.build_job_parameters(),
            compute=self.build_compute(),
            run_name=self.build_run_name(),
        )

    async def on_enter_job_complete_async(self) -> None:
        """Delegate to the subclass result handler."""
        try:
            await self.on_job_completed_async()
        except Exception as e:
            logger.error(f"[{self.request.id}] Result handling failed: {e}")
            add_fact(
                self.db, self.request.id, "results_fetch_failed",
                {"error": str(e)}, actor="system",
            )
            self.mark_failed()

    async def on_enter_notifying_async(self) -> None:
        await self.send_notification_async()

    # ------------------------------------------------------------------
    # Backwards-compat fact properties
    # ------------------------------------------------------------------

    @property
    def has_job_completed(self) -> bool:
        """Kept for subclasses / older transition guards. Reads the new fact."""
        return self.step_completed(_MAIN_STEP)

    @property
    def has_results_fetched(self) -> bool:
        return has_fact(self.db, self.request.id, "results_fetched")
