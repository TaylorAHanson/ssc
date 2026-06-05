"""
Tag Change state machine (GitOps for Unity Catalog tags).

Git is the source of truth for governed UC tags: this workflow never runs
``ALTER ... SET TAGS`` directly. Instead it generates the SQL, opens a pull
request against the configured governance tags repo, and tracks the PR until a
governance admin merges it. A GitHub Action in that repo applies the SQL per
environment on merge.

States: ``pending -> pr_open -> completed`` (PR merged / applied), with
``rejected`` (PR closed unmerged or request rejected) and ``failed`` terminals.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from statemachine import State

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.models.request import RequestStatus, RequestType
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.decorators import workflow
from app.state_machines.facts import add_fact, get_latest_fact, has_fact
import logging
import traceback

logger = logging.getLogger(__name__)


def _escape_sql_literal(value: Optional[str]) -> str:
    """Escape a string for safe inclusion inside a single-quoted SQL literal."""
    if value is None:
        return ""
    return str(value).replace("'", "''")


def build_tag_sql(changes: List[Dict[str, Any]]) -> str:
    """Build ``ALTER TABLE ... SET/UNSET TAGS`` SQL from a list of changes.

    Each change is ``{"table": "<fqn>", "set": {k: v, ...}, "unset": [k, ...]}``.
    Returns the full SQL script (one statement per line). Returns an empty
    string when there is nothing to apply.
    """
    lines: List[str] = []
    for change in changes:
        table = change.get("table")
        if not table:
            continue
        set_tags = change.get("set") or {}
        unset_tags = change.get("unset") or []

        if set_tags:
            pairs = ", ".join(
                f"'{_escape_sql_literal(k)}' = '{_escape_sql_literal(v)}'"
                for k, v in set_tags.items()
            )
            lines.append(f"ALTER TABLE {table} SET TAGS ({pairs});")
        if unset_tags:
            keys = ", ".join(f"'{_escape_sql_literal(k)}'" for k in unset_tags)
            lines.append(f"ALTER TABLE {table} UNSET TAGS ({keys});")

    return "\n".join(lines)


@workflow(request_types=RequestType.TAG_CHANGE, feature_flag="governance")
class TagChangeStateMachine(BaseRequestStateMachine):

    pending = State("pending", initial=True)
    pr_open = State("pr_open")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)
    failed = State("failed", final=True)

    submit = pending.to(pr_open, cond="has_request_submitted")
    finish = pr_open.to(completed, cond="has_pr_merged")
    decline = pr_open.to(rejected, cond="has_pr_closed_unmerged")

    reject = (
        pending.to(rejected, cond="has_request_rejected")
        | pr_open.to(rejected, cond="has_request_rejected")
    )

    mark_failed = pending.to(failed) | pr_open.to(failed)

    APPROVAL_NODES = {}

    STATE_COMPLETION_FACTS = {
        **BaseRequestStateMachine.STATE_COMPLETION_FACTS,
        "pr_open": "pr_merged",
    }

    STATE_LOG_FACTS = {
        **BaseRequestStateMachine.STATE_LOG_FACTS,
        "pr_open": ["pr_created", "pr_merged", "pr_closed_unmerged", "pr_failed"],
    }

    STATUS_MAPPING = {
        **BaseRequestStateMachine.STATUS_MAPPING,
        "pr_open": RequestStatus.PROVISIONING,
        "completed": RequestStatus.COMPLETED,
    }

    # ------------------------------------------------------------------
    # Fact guards
    # ------------------------------------------------------------------

    @property
    def has_pr_created(self) -> bool:
        return has_fact(self.db, self.request.id, "pr_created")

    @property
    def has_pr_merged(self) -> bool:
        return has_fact(self.db, self.request.id, "pr_merged")

    @property
    def has_pr_closed_unmerged(self) -> bool:
        return has_fact(self.db, self.request.id, "pr_closed_unmerged")

    # ------------------------------------------------------------------
    # UI labels
    # ------------------------------------------------------------------

    def _get_state_display_name(self, state_id: str) -> str:
        overrides = {"pr_open": "PR Open", "completed": "Applied"}
        if state_id in overrides:
            return overrides[state_id]
        return super()._get_state_display_name(state_id)

    # ------------------------------------------------------------------
    # PR creation + polling (runs every tick while in pr_open)
    # ------------------------------------------------------------------

    async def on_enter_pr_open_async(self):
        if not self.has_pr_created:
            await self._create_pull_request()
        else:
            await self._poll_pull_request()

    async def _create_pull_request(self):
        from app.providers.github.client import GitHubProvider

        ctx = self.request.state_context or {}
        repo = settings.GOVERNANCE_TAGS_REPO
        base_branch = settings.GOVERNANCE_TAGS_BASE_BRANCH

        if not repo:
            raise RetryableError(
                "GOVERNANCE_TAGS_REPO is not configured; cannot open tag-change PR"
            )

        changes = ctx.get("changes") or []
        sql = ctx.get("tags_sql") or build_tag_sql(changes)
        if not sql.strip():
            logger.warning(f"[{self.request.id}] No tag changes to apply; failing request")
            add_fact(
                self.db,
                self.request.id,
                "pr_failed",
                {"error": "No tag changes to apply"},
                actor="system",
            )
            self.db.commit()
            # Raise so the worker persists the failed status; calling a
            # transition from within on_enter_* would not be saved by the poller.
            raise PermanentError("No tag changes to apply")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        file_path = f"{settings.GOVERNANCE_TAGS_PATH}/{timestamp}-{self.request.id}.sql"
        branch = f"tag-change/{self.request.id}"

        dataset_name = ctx.get("dataset_name") or ctx.get("dataset_id") or "datasets"
        requested_by = ctx.get("requested_by_email") or ctx.get("requested_by") or "unknown"

        header = (
            f"-- Tag change request {self.request.id}\n"
            f"-- Dataset: {dataset_name}\n"
            f"-- Requested by: {requested_by}\n"
            f"-- Generated: {datetime.now(timezone.utc).isoformat()}\n\n"
        )
        file_content = header + sql + "\n"

        pr_title = ctx.get("pr_title") or f"Tag change: {dataset_name}"
        pr_body = ctx.get("pr_body") or (
            f"Automated tag change requested via the governance Tag Management tab.\n\n"
            f"- Request: `{self.request.id}`\n"
            f"- Dataset: `{dataset_name}`\n"
            f"- Requested by: {requested_by}\n\n"
            f"Merging this PR triggers the tag-apply GitHub Action which runs the "
            f"generated `ALTER TABLE ... SET/UNSET TAGS` SQL across environments."
        )

        try:
            async with GitHubProvider(
                token=settings.GITHUB_TOKEN or settings.get_git_token(),
                org=settings.GITHUB_ORG,
            ) as github:
                await github.create_branch(repo, branch, base_branch)
                await github.create_or_update_file(
                    repo=repo,
                    path=file_path,
                    content=file_content,
                    branch=branch,
                    message=f"Tag change: {dataset_name} ({self.request.id})",
                )
                pr = await github.create_pull_request(
                    repo=repo,
                    title=pr_title,
                    head=branch,
                    base=base_branch,
                    body=pr_body,
                )

            add_fact(
                self.db,
                self.request.id,
                "pr_created",
                {
                    "pr_url": pr.get("html_url"),
                    "pr_number": pr.get("number"),
                    "repo": repo,
                    "branch": branch,
                    "file_path": file_path,
                },
                actor="system",
            )
            self.db.commit()
            logger.info(
                f"[{self.request.id}] Opened tag-change PR {pr.get('html_url')}"
            )
        except Exception as e:
            logger.error(f"[{self.request.id}] Failed to open tag-change PR: {e}")
            logger.error(traceback.format_exc())
            # Retryable: let the poller try again next cycle. Permanent errors
            # raised by the provider will be classified by the worker.
            raise

    async def _poll_pull_request(self):
        if self.has_pr_merged or self.has_pr_closed_unmerged:
            return

        from app.providers.github.client import GitHubProvider

        fact = get_latest_fact(self.db, self.request.id, "pr_created")
        if not fact or not fact.event_data:
            return
        repo = fact.event_data.get("repo")
        number = fact.event_data.get("pr_number")
        if not repo or not number:
            return

        async with GitHubProvider(
            token=settings.GITHUB_TOKEN or settings.get_git_token(),
            org=settings.GITHUB_ORG,
        ) as github:
            pr = await github.get_pull_request(repo, int(number))

        if pr.get("merged"):
            add_fact(
                self.db,
                self.request.id,
                "pr_merged",
                {"merged_at": pr.get("merged_at")},
                actor="system",
            )
            # Mirror the base-class completion fact so UI timestamps render.
            add_fact(
                self.db,
                self.request.id,
                "provisioning_completed",
                {"merged_at": pr.get("merged_at")},
                actor="system",
            )
            self.db.commit()
            logger.info(f"[{self.request.id}] Tag-change PR merged; marking applied")
        elif pr.get("state") == "closed":
            add_fact(
                self.db,
                self.request.id,
                "pr_closed_unmerged",
                {"closed_at": pr.get("closed_at")},
                actor="system",
            )
            self.db.commit()
            logger.info(
                f"[{self.request.id}] Tag-change PR closed without merge; marking rejected"
            )
