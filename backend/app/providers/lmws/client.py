"""
LMWS / FWS-API group & user management provider.

Replaces ``EntraIdProvider``. Unlike Entra ID (which the app called directly
over Microsoft Graph), LMWS group/user operations cannot run in-process: they
require the Qualcomm FWS-API, a service account whose credentials live in the
``lmws`` Databricks secret scope, and a classic-compute cluster. So every
operation runs **as a Databricks job** against the vendored notebook
(``lmws_group_management_job.py``), reusing the same ``DatabricksProvider``
submit/poll primitives that back ``DatabricksJobStepMixin``.

Two execution paths share one action/parameter contract:

* **State machines (writes)** — the preferred, non-blocking path. A state
  machine calls ``self.run_databricks_job_step(**provider.build_step_kwargs(...))``
  so submission + polling happen across poller ticks (idempotent, fact-based).
  Use :meth:`build_step_kwargs` to build the kwargs and :meth:`parse_output`
  to read the result once the step completes.

* **Agent tools (reads)** — stateless tools aren't state machines and need a
  synchronous answer, so :meth:`run_action` submits the job and polls inline
  until terminal. Used by the ``list_retrieve`` / ``member_retrieve`` tools.

The notebook returns a JSON document via ``dbutils.notebook.exit(...)`` and
raises ``RuntimeError`` on failure; :meth:`parse_output` surfaces both.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Union

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.providers.base import BaseProvider
from app.providers.databricks.client import DatabricksProvider
from app.providers.databricks.compute import ComputeSpec, default_classic_compute

logger = logging.getLogger(__name__)

#: Vendored notebook filename (lives next to this module).
NOTEBOOK_FILENAME = "lmws_group_management_job.py"

#: Where inline (tool) runs upload the notebook before submitting.
REMOTE_NOTEBOOK_DIR = "/Shared/agents"


class LmwsAction:
    """Action names understood by the LMWS notebook (see notebook docstring)."""

    # --- Core (implemented now) ---
    LIST_RETRIEVE = "list_retrieve"
    MEMBER_RETRIEVE = "member_retrieve"
    LIST_MEMBERS_ADD = "list_members_add"
    LIST_MEMBERS_REMOVE = "list_members_remove"
    LIST_MEMBERS_UPDATE = "list_members_update"

    # --- Group/SPAC lifecycle (stubbed for now) ---
    LIST_CREATE_NEW = "list_create_new"
    CREATE_SP_GROUP = "create_sp_group"
    PROCESS_SPAC_POLICY = "process_spac_policy"
    GET_SPAC_POLICY = "get_spac_policy"
    REQUEST_CONFIRMATION = "request_confirmation"


CORE_ACTIONS = frozenset(
    {
        LmwsAction.LIST_RETRIEVE,
        LmwsAction.MEMBER_RETRIEVE,
        LmwsAction.LIST_MEMBERS_ADD,
        LmwsAction.LIST_MEMBERS_REMOVE,
        LmwsAction.LIST_MEMBERS_UPDATE,
    }
)

STUBBED_ACTIONS = frozenset(
    {
        LmwsAction.LIST_CREATE_NEW,
        LmwsAction.CREATE_SP_GROUP,
        LmwsAction.PROCESS_SPAC_POLICY,
        LmwsAction.GET_SPAC_POLICY,
        LmwsAction.REQUEST_CONFIRMATION,
    }
)


def _csv(value: Union[str, List[str], None]) -> str:
    """Normalize a value into the comma-separated string the notebook expects.

    Accepts a list/tuple/set or a (possibly already comma-joined) string and
    returns a trimmed, empty-free CSV string.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value]
    else:
        items = [part.strip() for part in str(value).split(",")]
    return ",".join(i for i in items if i)


class LmwsProvider(BaseProvider):
    """Group/user management via the LMWS notebook run as a Databricks job."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.secret_scope = self.get_config("secret_scope") or settings.LMWS_SECRET_SCOPE
        self.timeout_seconds = (
            self.get_config("timeout_seconds") or settings.LMWS_JOB_TIMEOUT_SECONDS
        )

    # ------------------------------------------------------------------
    # Notebook location & compute
    # ------------------------------------------------------------------

    @classmethod
    def notebook_path(cls) -> str:
        """Absolute local path to the vendored LMWS notebook."""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), NOTEBOOK_FILENAME)

    def compute(self) -> ComputeSpec:
        """Classic compute target (API-only, no Spark). Honors DATABRICKS_JOB_* settings."""
        return default_classic_compute(
            spark_version=settings.DATABRICKS_JOB_SPARK_VERSION,
            node_type_id=settings.DATABRICKS_JOB_NODE_TYPE_ID,
            num_workers=settings.DATABRICKS_JOB_NUM_WORKERS,
            existing_cluster_id=settings.DATABRICKS_JOB_CLUSTER_ID or None,
            instance_pool_id=settings.DATABRICKS_JOB_INSTANCE_POOL_ID or None,
        )

    # ------------------------------------------------------------------
    # Action / parameter contract
    # ------------------------------------------------------------------

    def build_parameters(
        self,
        action: str,
        *,
        list_name: Optional[str] = None,
        members: Union[str, List[str], None] = None,
        justification: Optional[str] = None,
        owner: Optional[str] = None,
        supervisors: Union[str, List[str], None] = None,
        description: Optional[str] = None,
        clone_source: Optional[str] = None,
        request_id: Optional[str] = None,
        spac_policies: Union[str, List[str], None] = None,
    ) -> Dict[str, str]:
        """Build the notebook ``base_parameters`` (all values are strings).

        Mirrors the documented LMWS parameter table. ``members``, ``supervisors``
        and ``spac_policies`` accept either a list or a pre-joined CSV string.
        The ``secret_scope`` is passed through so the notebook knows which
        Databricks secret scope holds the service-account credentials.
        """
        if action not in CORE_ACTIONS and action not in STUBBED_ACTIONS:
            raise ValueError(f"Unknown LMWS action: {action!r}")

        return {
            "action": action,
            "list_name": list_name or "",
            "members": _csv(members),
            "justification": justification or settings.LMWS_DEFAULT_JUSTIFICATION,
            "owner": owner or "",
            "supervisors": _csv(supervisors),
            "description": description or "",
            "clone_source": clone_source or settings.LMWS_DEFAULT_CLONE_SOURCE,
            "request_id": request_id or "",
            "spac_policies": _csv(spac_policies),
            "secret_scope": self.secret_scope,
        }

    @staticmethod
    def parse_output(step_output: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Parse the JSON document the notebook returned via ``dbutils.notebook.exit``.

        ``step_output`` is whatever ``DatabricksProvider.get_run_output`` produced
        (or the ``output`` captured in a ``step:<id>:completed`` fact). Raises
        ``PermanentError`` when the notebook errored or returned non-JSON.
        """
        if not step_output:
            raise PermanentError("LMWS job produced no output.")

        # Surface a notebook-level error captured by the Jobs API.
        if step_output.get("error"):
            raise PermanentError(f"LMWS job failed: {step_output['error']}")

        raw = step_output.get("notebook_result")
        if raw is None and isinstance(step_output.get("output"), dict):
            raw = step_output["output"].get("notebook_result")
        if raw is None:
            raise PermanentError(
                f"LMWS job output missing notebook_result: {step_output!r}"
            )

        if isinstance(raw, (dict, list)):
            result = raw
        else:
            try:
                result = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as e:
                raise PermanentError(f"LMWS job returned non-JSON output {raw!r}: {e}")

        # The notebook echoes failures as {"Result": "FAILED"/"ERROR", ...}.
        status = str(result.get("Result", result.get("result", ""))).upper()
        if status in {"FAILED", "ERROR"}:
            raise PermanentError(f"LMWS action reported failure: {result}")
        return result

    # ------------------------------------------------------------------
    # State-machine path (preferred): kwargs for DatabricksJobStepMixin
    # ------------------------------------------------------------------

    def build_step_kwargs(
        self,
        action: str,
        *,
        step_id: str,
        run_name: Optional[str] = None,
        **params: Any,
    ) -> Dict[str, Any]:
        """Build kwargs for ``DatabricksJobStepMixin.run_databricks_job_step``.

        Example::

            await self.run_databricks_job_step(
                **provider.build_step_kwargs(
                    LmwsAction.LIST_MEMBERS_ADD,
                    step_id="lmws_add",
                    list_name="edh_dbx_consultant",
                    members=["user1", "user2"],
                    justification="Project onboarding",
                )
            )
        """
        return {
            "step_id": step_id,
            "notebook_path": self.notebook_path(),
            "parameters": self.build_parameters(action, **params),
            "compute": self.compute(),
            "run_name": run_name or f"LMWS {action}",
            "timeout_seconds": self.timeout_seconds,
        }

    # ------------------------------------------------------------------
    # Inline path (agent tools): submit + poll until terminal
    # ------------------------------------------------------------------

    def _databricks_provider(self) -> DatabricksProvider:
        return DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID},
        )

    async def run_action(self, action: str, **params: Any) -> Dict[str, Any]:
        """Submit an LMWS action as a job and block until it finishes (inline).

        Intended for stateless callers (agent read tools). State machines should
        use :meth:`build_step_kwargs` with the step mixin instead so they don't
        block a poller tick on cluster cold-start.
        """
        db = self._databricks_provider()
        remote_path = f"{REMOTE_NOTEBOOK_DIR}/lmws_{action}_{uuid.uuid4().hex}"
        await db.import_notebook(self.notebook_path(), remote_path)

        run_id = await db.submit_job(
            notebook_task={
                "notebook_path": remote_path,
                "base_parameters": self.build_parameters(action, **params),
            },
            run_name=f"LMWS {action} (inline)",
            compute=self.compute(),
            timeout_seconds=self.timeout_seconds,
        )
        logger.info(f"LMWS inline run submitted: action={action} run_id={run_id}")

        interval = settings.LMWS_INLINE_POLL_INTERVAL_SECONDS
        max_wait = settings.LMWS_INLINE_MAX_WAIT_SECONDS
        waited = 0
        while True:
            status = await db.get_run_status(run_id)
            if status["is_completed"]:
                break
            if waited >= max_wait:
                raise RetryableError(
                    f"LMWS {action} (run {run_id}) did not finish within {max_wait}s"
                )
            await asyncio.sleep(interval)
            waited += interval

        if not status["is_successful"]:
            raise PermanentError(
                f"LMWS {action} (run {run_id}) failed: {status.get('state_message')}"
            )

        output = await db.get_run_output(run_id)
        return self.parse_output(output)

    # ------------------------------------------------------------------
    # Semantic convenience methods (inline). Mirror the core actions.
    # ------------------------------------------------------------------

    async def list_retrieve(self, list_name: str) -> Dict[str, Any]:
        """Get members, owner, and supervisors of a list."""
        return await self.run_action(LmwsAction.LIST_RETRIEVE, list_name=list_name)

    async def member_retrieve(self, member: str) -> Dict[str, Any]:
        """Get all group memberships for a user (CN)."""
        return await self.run_action(LmwsAction.MEMBER_RETRIEVE, members=member)

    async def add_members(
        self,
        list_name: str,
        members: Union[str, List[str]],
        justification: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add members to a list."""
        return await self.run_action(
            LmwsAction.LIST_MEMBERS_ADD,
            list_name=list_name,
            members=members,
            justification=justification,
        )

    async def remove_members(
        self,
        list_name: str,
        members: Union[str, List[str]],
        justification: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove members from a list."""
        return await self.run_action(
            LmwsAction.LIST_MEMBERS_REMOVE,
            list_name=list_name,
            members=members,
            justification=justification,
        )

    async def update_members(
        self,
        list_name: str,
        members: Union[str, List[str]],
        justification: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set list membership to exactly the specified members (add missing, remove extras)."""
        return await self.run_action(
            LmwsAction.LIST_MEMBERS_UPDATE,
            list_name=list_name,
            members=members,
            justification=justification,
        )

    async def health_check(self) -> bool:
        """LMWS reachability is only verifiable from the job cluster, so this is a no-op."""
        return True
