"""One path from "these targets should carry these tags" to an applied change.

``evaluate`` reads live Unity Catalog state and runs every check (policy,
hygiene/typo lint, deterministic risk); ``submit`` records the change as a
``TAG_CHANGE`` request and either applies it directly (Local Execution Mode) or
queues the GitOps pull request. Every caller that changes tags goes through
here, so they all get the same checks and the same audit trail.
"""
import asyncio
import concurrent.futures
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.request import RequestModel
from app.models.request import RequestType
from app.state_machines.facts import add_fact
from app.workflows.tag_apply import ApplyResult, apply_tag_plan
from app.workflows.tag_abac import abac_impacts, fetch_abac_references
from app.workflows.tag_governed import GovernedTags, check_governed_values, fetch_governed_tags
from app.workflows.tag_lint import LintFinding, run_lint_checks
from app.workflows.tag_plan import (
    CONTAINER_TYPES,
    DATASET_KEY,
    RESERVED_KEYS,
    TagPlan,
    TagVocabulary,
    build_tag_plan,
    container_type,
    fetch_live_state,
    fetch_tag_vocabulary,
    is_reserved_key,
    validate_target,
)
from app.workflows.tag_policy import TagPolicy, get_default_policy, load_policy
from app.workflows.tag_review import AgentReviewResult, request_agent_review
from app.workflows.tag_risk import RiskReport, calculate_risk_score
from app.workflows.tag_sql import build_tag_sql

logger = logging.getLogger(__name__)

# Tag keys the governance UI must never touch: ``system.certification_status``
# is set and removed by OmniGuard, and is excluded from both display-for-edit
# and the SET/UNSET diff. Other ``system.*`` tags are editable.
RESERVED_TAG_KEYS = RESERVED_KEYS

# Governance keys surfaced as suggestions in the editor (free-form keys allowed).
SUGGESTED_TAG_KEYS = [
    "access_group",
    "approver_group",
    "data_owner",
    "dataset",
    "reliability_window",
    "classification",
]

# Most objects a single change may touch. Keeps the live-state queries bounded
# and a fat-fingered "add everything" from turning into one enormous change.
MAX_TARGETS = 500

GITOPS_COLUMNS_UNSUPPORTED = (
    "Column tags can only be applied in Local Execution Mode. The governance "
    "repository's migration format covers table and view tags only."
)
GITOPS_CONTAINERS_UNSUPPORTED = (
    "Catalog and schema tags and descriptions can only be applied in Local Execution Mode. "
    "The governance repository's migration format covers table and view tags only."
)


class TagChangeError(ValueError):
    """A problem with the requested change that the requester can fix."""


@dataclass
class TagTarget:
    # A catalog, schema, table or view name (the field predates catalogs and schemas).
    table: str
    desired_tags: Dict[str, str]
    column: Optional[str] = None
    # Catalogs and schemas only. None leaves the description alone; "" clears it.
    desired_comment: Optional[str] = None


@dataclass
class TagChangeEvaluation:
    plan: TagPlan
    changes: List[Dict[str, Any]]
    violations: List[str]
    vocabulary: TagVocabulary
    lint_findings: List[LintFinding]
    risk: RiskReport
    local_mode: bool
    warnings: List[str] = field(default_factory=list)
    governed: GovernedTags = field(default_factory=GovernedTags)

    @property
    def valid(self) -> bool:
        return not self.violations

    def plan_dict(self) -> Dict[str, Any]:
        """The plan for display. In GitOps mode the statements shown are the ones
        committed to the governance repo, which use its own (contract) form."""
        data = self.plan.to_dict()
        if (
            not self.local_mode
            and self.changes
            and not self.plan.has_column_changes
            and not self.plan.has_container_changes
        ):
            data["statements"] = [s for s in build_tag_sql(self.changes).splitlines() if s]
        return data

    def lint_dict(self) -> Dict[str, Any]:
        return {"findings": [f.to_dict() for f in self.lint_findings]}


@dataclass
class SubmittedChange:
    request: RequestModel
    execution_mode: str
    change_count: int
    apply_result: Optional[ApplyResult] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def is_reserved(key: str) -> bool:
    return is_reserved_key(key)


def get_provider():
    from app.core.workspaces import get_governance_uc_provider

    return get_governance_uc_provider()


async def load_tag_policy_async() -> TagPolicy:
    """Load the governance policy, falling back safely to the canonical default policy."""
    if settings.GOVERNANCE_TAGS_LOCAL_MODE or not settings.GOVERNANCE_TAGS_REPO:
        return get_default_policy()
    try:
        from app.workflows.tools import _get_github_provider

        policy = await load_policy(
            _get_github_provider(),
            repo=settings.GOVERNANCE_TAGS_REPO,
            ref=settings.GOVERNANCE_TAGS_BASE_BRANCH or "dev",
        )
        return policy or get_default_policy()
    except Exception as e:
        logger.warning(f"Could not load tag policy from GitHub: {e}")
        return get_default_policy()


def load_tag_policy() -> TagPolicy:
    """Synchronous ``load_tag_policy_async``, safe to call with or without a running loop."""
    if settings.GOVERNANCE_TAGS_LOCAL_MODE or not settings.GOVERNANCE_TAGS_REPO:
        return get_default_policy()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(load_tag_policy_async())
    # Called from inside an event loop (e.g. a test): run the fetch on its own loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, load_tag_policy_async()).result()


def read_table_tags(provider, full_name: str) -> Dict[str, Optional[str]]:
    """Read current UC tag key->value pairs for a catalog, schema or table (excluding reserved keys)."""
    tags: Dict[str, Optional[str]] = {}
    entity_type = {"CATALOG": "catalogs", "SCHEMA": "schemas"}.get(container_type(full_name) or "", "tables")
    try:
        uc_tags = provider.client.entity_tag_assignments.list(
            entity_type=entity_type, entity_name=full_name
        )
        for t in uc_tags:
            key = getattr(t, "tag_key", None)
            if key and not is_reserved(key):
                tags[key] = getattr(t, "tag_value", None)
    except Exception as e:
        logger.warning(f"Could not list tags for {full_name}: {e}")
    return tags


def validate_targets(targets: List[TagTarget]) -> None:
    if not targets:
        raise TagChangeError("No objects specified in payload.")
    if len(targets) > MAX_TARGETS:
        raise TagChangeError(
            f"This change touches {len(targets)} objects; the limit is {MAX_TARGETS}. "
            f"Split it into smaller changes."
        )
    for t in targets:
        try:
            validate_target(t.table, t.column)
        except ValueError as e:
            raise TagChangeError(str(e)) from e
        if t.desired_comment is not None and (t.column or not container_type(t.table)):
            raise TagChangeError(
                f"'{t.table}': descriptions can only be changed here for catalogs and schemas."
            )


def scope_label(
    targets: List[TagTarget], dataset_id: Optional[str] = None, dataset_name: Optional[str] = None
) -> str:
    """What a change is "about", for titles, PR headers and the AI review."""
    if dataset_name or dataset_id:
        return dataset_name or dataset_id or ""
    tables = sorted({t.table for t in targets})
    if not tables:
        return "no objects"
    if len(tables) == 1:
        return tables[0]
    return f"{tables[0]} + {len(tables) - 1} more"


def evaluate(
    provider,
    policy: TagPolicy,
    targets: List[TagTarget],
    dataset_values: Optional[List[Optional[str]]] = None,
    local_mode: Optional[bool] = None,
) -> TagChangeEvaluation:
    """Plan the change against live state and run every deterministic check."""
    validate_targets(targets)
    local = bool(settings.GOVERNANCE_TAGS_LOCAL_MODE) if local_mode is None else local_mode

    table_names = [t.table for t in targets if not t.column]
    column_targets = [(t.table, t.column) for t in targets if t.column]
    live_state = fetch_live_state(provider, table_names, column_targets)
    payload = [
        {"table": t.table, "column": t.column, "desired_tags": t.desired_tags, "desired_comment": t.desired_comment}
        for t in targets
    ]
    plan = build_tag_plan(payload, live_state)

    changes: List[Dict[str, Any]] = []
    resulting_counts: Dict[str, int] = {}
    for diff in plan.diffs.values():
        if diff.has_changes:
            change: Dict[str, Any] = {
                "table": diff.table,
                "set": {k: diff.after[k] for k in diff.after if diff.before.get(k) != diff.after[k]},
                "unset": diff.removed_keys,
            }
            # Table-level changes keep the exact shape the GitOps contract expects.
            if diff.column:
                change["column"] = diff.column
            if diff.object_type in CONTAINER_TYPES:
                change["kind"] = diff.object_type.lower()
            if diff.comment_changed:
                change["comment"] = diff.comment_after
            changes.append(change)
            resulting_counts[diff.label] = len(diff.after)

    violations = policy.check(changes, resulting_counts) if changes else []
    # Reserved keys never reach the plan — the planner drops them so OmniGuard's
    # certification tag can't be overwritten — so refuse them here rather than
    # letting the request look like a silent no-op.
    for t in targets:
        label = f"{t.table}.{t.column}" if t.column else t.table
        for key in t.desired_tags:
            if policy.is_reserved(key) or is_reserved(key):
                violations.append(
                    f"{label}: '{key}' is a reserved tag. OmniGuard sets it when a dataset passes Data "
                    f"Certification and removes it when certification lapses, so it can't be changed here."
                )
    if not local and plan.has_column_changes:
        violations.append(GITOPS_COLUMNS_UNSUPPORTED)
    if not local and plan.has_container_changes:
        violations.append(GITOPS_CONTAINERS_UNSUPPORTED)

    # Unity Catalog's own governed tags (allowed values) — otherwise only caught
    # when the statement fails, possibly after earlier statements have applied.
    warnings: List[str] = []
    governed = fetch_governed_tags(provider, {k for c in changes for k in (c.get("set") or {})})
    violations.extend(check_governed_values(changes, governed))
    if governed.unchecked:
        warnings.append(
            "Couldn't read the Unity Catalog tag policy for "
            + ", ".join(f"'{k}'" for k in governed.unchecked)
            + "; any allowed-value rules on those keys will only be checked when the change is applied."
        )
    if governed.tags:
        # There is no API to read who holds ASSIGN on a governed tag, so say so up front.
        warnings.append(
            "Governed tag(s) "
            + ", ".join(f"'{k}'" for k in sorted(governed.tags))
            + " can only be set by an identity with the ASSIGN permission on them. Changes are applied "
            "as the governance service principal; if it lacks ASSIGN, Unity Catalog will reject those statements."
        )

    # ABAC column masks / row filters keyed on the tags being changed.
    changed_tables = sorted({c["table"] for c in changes})
    abac_refs = fetch_abac_references(provider, changed_tables) if changed_tables else None
    impacts = abac_impacts(plan, abac_refs) if abac_refs else []
    if impacts:
        warnings.append(
            "This change touches tags that access policies (column masks or row filters) use, so it can "
            "change who can see the data: " + "; ".join(i.replace("`", "") for i in impacts[:5])
            + (f"; and {len(impacts) - 5} more" if len(impacts) > 5 else "")
            + "."
        )
    if abac_refs and abac_refs.unchecked:
        n = len(abac_refs.unchecked)
        warnings.append(
            f"Couldn't read the access policies (ABAC) for {n} object{'s' if n != 1 else ''} "
            f"({', '.join(abac_refs.unchecked[:3])}{', …' if n > 3 else ''}), so masking or row-filter "
            "impact wasn't checked there. The governance service principal needs READ METADATA on them."
        )

    keys_of_interest = set(SUGGESTED_TAG_KEYS)
    for diff in plan.diffs.values():
        keys_of_interest.update(diff.changed_keys)
    vocabulary = fetch_tag_vocabulary(
        provider=provider,
        table_names=sorted({t.table for t in targets}),
        keys_of_interest=sorted(keys_of_interest),
        dataset_values=[v for v in (dataset_values or []) if v],
        dataset_key=DATASET_KEY,
        include_columns=bool(column_targets),
        include_containers=any(container_type(t.table) for t in targets),
    )
    lint_findings = run_lint_checks(plan, vocabulary, policy)
    risk = calculate_risk_score(
        plan=plan,
        environment=settings.ENVIRONMENT or "dev",
        findings=lint_findings,
        vocabulary=vocabulary,
        policy=policy,
        abac_impacts=impacts,
    )
    return TagChangeEvaluation(
        plan=plan,
        changes=changes,
        violations=violations,
        vocabulary=vocabulary,
        lint_findings=lint_findings,
        risk=risk,
        local_mode=local,
        warnings=warnings,
        governed=governed,
    )


async def review(evaluation: TagChangeEvaluation, scope: str) -> AgentReviewResult:
    """Advisory, non-blocking LLM review of an evaluated change."""
    return await request_agent_review(
        dataset_name=scope,
        plan=evaluation.plan,
        risk_report=evaluation.risk,
        lint_findings=evaluation.lint_findings,
    )


def submit(
    db: Session,
    provider,
    evaluation: TagChangeEvaluation,
    *,
    scope: str,
    dataset_id: Optional[str],
    actor_email: str,
    actor_name: Optional[str] = None,
    pr_title: Optional[str] = None,
) -> SubmittedChange:
    """Record the change and apply it (local) or queue its pull request (GitOps)."""
    changes = evaluation.changes
    plan = evaluation.plan
    if not changes and not plan.actionable:
        raise TagChangeError("No tag changes detected.")
    if evaluation.violations:
        raise TagChangeError(
            "This change would be rejected by the tag policy:\n- " + "\n- ".join(evaluation.violations)
        )

    request_id = f"req-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    common = {
        "dataset_id": dataset_id,
        "dataset_name": scope,
        "requested_by": actor_name or actor_email,
        "requested_by_email": actor_email,
        "changes": changes,
        "plan": evaluation.plan_dict(),
        "risk": evaluation.risk.to_dict(),
        "lint": evaluation.lint_dict(),
        "submitted_at": now.isoformat(),
    }

    if evaluation.local_mode:
        logger.info(f"[{request_id}] Executing tag changes locally for '{scope}' ({len(changes)} object(s))")
        apply_res = apply_tag_plan(
            provider=provider,
            plan=plan,
            request_id=request_id,
            actor=actor_email,
            environment=settings.ENVIRONMENT or "dev",
        )
        final_status = "completed" if apply_res.status in ("applied", "noop") else "failed"
        request = RequestModel(
            id=request_id,
            type=RequestType.TAG_CHANGE.value,
            title=f"Tag change: {scope} (Local)",
            status=final_status,
            current_state=final_status,
            requester_email=actor_email,
            created_at=now,
            updated_at=now,
            state_context={
                **common,
                "execution_mode": "local",
                "tags_sql": "\n".join(plan.statements),
                "apply_result": apply_res.to_dict(),
                "statements_applied": apply_res.applied_count,
                "statements_noop": apply_res.noop_count,
                "statements_failed": apply_res.failed_count,
                "error": apply_res.error,
            },
        )
        db.add(request)
        add_fact(db, request_id, "tag_change_applied", apply_res.to_dict(), actor=actor_email)
        db.commit()
        if apply_res.status == "failed":
            logger.error(f"[{request_id}] Local tag application failed: {apply_res.error}")
        return SubmittedChange(
            request=request, execution_mode="local", change_count=len(changes), apply_result=apply_res
        )

    if not settings.GOVERNANCE_TAGS_REPO:
        raise TagChangeError(
            "GOVERNANCE_TAGS_REPO is not configured. Configure it in Admin -> Settings or enable Local Execution Mode."
        )
    if not settings.GOVERNANCE_TAGS_BASE_BRANCH:
        raise TagChangeError(
            "GOVERNANCE_TAGS_BASE_BRANCH is not configured. Configure it in Admin -> Settings or enable Local Execution Mode."
        )

    request = RequestModel(
        id=request_id,
        type=RequestType.TAG_CHANGE.value,
        title=f"Tag change: {scope}",
        status="pending",
        current_state="pending",
        requester_email=actor_email,
        created_at=now,
        updated_at=now,
        state_context={
            **common,
            "execution_mode": "gitops",
            "tags_sql": build_tag_sql(changes),
            "pr_title": pr_title or f"Tag change: {scope}",
        },
    )
    db.add(request)
    add_fact(db, request_id, "request_submitted", {}, actor=actor_email)
    db.commit()
    logger.info(f"[{request_id}] Created GitOps tag-change request for '{scope}'")
    return SubmittedChange(request=request, execution_mode="gitops", change_count=len(changes))
