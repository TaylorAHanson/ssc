"""Workflow (Workflow) authoring tools for the agent.

Admin-only building blocks so the agent can co-author no-code workflows in chat:
discover the available step tools and gate types, inspect an existing workflow,
validate / preview (dry-run) a candidate graph, and — only on explicit admin
confirmation — save a draft or publish.

Design notes:
  * All gated with ``required_role="Governance Admin"`` (Platform Admins always
    pass; Governance Admins match by name; others are filtered out upstream).
  * Read tools never mutate. ``save_workflow_draft`` / ``publish_workflow`` are
    ``app_write`` (DB-only), so they route through the governed ToolExecutor and
    are audited but don't trigger infra approval gates.
  * The same validators the API uses are reused (``validate_spec_dict`` +
    ``project_run``) so what the agent checks == what the editor/publish enforce.
"""
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.tools.mcp import tool

_AUTHOR_ROLE = "Governance Admin"

# Longest ``run_workflow_tests`` will hold a chat turn waiting for verdicts.
# Nothing streams to the browser while a tool runs, so the turn must stay well
# under a proxy's idle timeout; cases that outlast the wait keep executing on the
# runner thread and are read back with ``list_workflow_tests``.
_RUN_WAIT_CAP_SECONDS = 150

# Returned by the write tools when the environment locks in-place authoring
# (WORKFLOW_AUTHORING_LOCKED, e.g. prod). The agent surfaces this to the admin.
_LOCKED_MSG = (
    "Workflow authoring is locked in this environment. Workflows can only be changed "
    "by importing a vetted bundle (promotion), not edited live here. Build and publish "
    "in a lower environment, then promote."
)


def _authoring_locked() -> bool:
    from app.core.config import settings

    return bool(settings.WORKFLOW_AUTHORING_LOCKED)

# Gate kinds + expression operators the spec language supports, surfaced to the
# agent so it can author specs without guessing (mirrors spec_loader.GATE_TYPES
# and expr's operator set).
# Note: the legacy "children" gate is deprecated (superseded by subworkflow
# stages) and intentionally omitted so the agent never authors a new one.
_GATE_TYPES = [
    "manager", "platform_admin", "data_owner", "training", "pr_merge", "manual_task",
]

# What each gate type is for and which EXTRA fields it accepts. Without this the
# agent guesses — most commonly putting `instructions` on an approval gate, which
# the loader rejects ("instructions is only valid on a 'manual_task' gate") and
# which costs a whole tool iteration to discover.
_GATE_TYPE_DETAILS: Dict[str, Dict[str, Any]] = {
    "manager": {
        "waits_for": "a human approval",
        "when": "the requester's manager (or a named group via `approver`) must sign off.",
        "extra_fields": [],
    },
    "platform_admin": {
        "waits_for": "a human approval",
        "when": "a platform/governance admin must sign off.",
        "extra_fields": [],
    },
    "data_owner": {
        "waits_for": "a human approval",
        "when": "the owner of the data being accessed must sign off; pair with "
                "`approver={'source':'approver_group_tag'|'assets'}` to resolve who.",
        "extra_fields": [],
    },
    "training": {
        "waits_for": "an automated check",
        "when": "the requester must have completed a course before the flow continues.",
        "extra_fields": ["course_code (required)", "course_name (optional label)"],
    },
    "pr_merge": {
        "waits_for": "an automated check",
        "when": "a pull request must be merged (GitOps) before the flow continues.",
        "extra_fields": [],
    },
    "manual_task": {
        "waits_for": "a person to do work OFF-PLATFORM and mark it done",
        "when": "there is NO tool for this step yet. The assignee gets an inbox item "
                "with your instructions and clicks 'Mark done' (or \"Can't complete\", "
                "which rejects the request). This is the ONLY gate type that takes "
                "`instructions`.",
        "extra_fields": [
            "instructions (REQUIRED — what the person must actually do, in plain steps)",
            "due_in_days (optional int — shows an overdue badge in the inbox)",
        ],
    },
}
_EXPR_OPS = [
    "$var (ctx field, dotted paths, optional default)",
    "$item (for_each item, only in item_args/for_each)",
    "$ctx (whole context)", "$literal (value as-is)",
    "$eq/$ne/$in [a,b]", "$contains [a,b] (a contains b; inverse of $in)",
    "$and/$or [..]", "$not a", "$bool a",
    "$coalesce [..] (first truthy)", "$concat [..] (string-join, None->'')",
    "$obj {k: expr}", "$list [expr,..]",
]


def _db():
    from app.db.session import get_db
    return next(get_db())


def _composable_workflows(db) -> List[Dict[str, Any]]:
    """Workflows usable as a ``subworkflow`` ref: every authored workflow plus any
    seed-catalog key, de-duplicated. ``composable`` marks the ones a runtime
    resolver can actually load now (published, or catalog) vs. draft-only.
    """
    from app.services.workflow_service import WorkflowService
    from app.workflows.graphs.specs import SPECS

    out: Dict[str, Dict[str, Any]] = {}
    for wf in WorkflowService.list_workflows(db):
        out[wf.key] = {
            "key": wf.key,
            "name": wf.name,
            "goal": wf.goal,
            "status": wf.status,
            "composable": wf.status == "published",
        }
    for key in SPECS:
        if key in out:
            out[key]["composable"] = True  # catalog seed is always resolvable
        else:
            out[key] = {"key": key, "name": key, "goal": None,
                        "status": "catalog", "composable": True}
    return sorted(out.values(), key=lambda w: w["key"])


def _composable_keys(db) -> set:
    """Set of keys a subworkflow ref can resolve to at runtime (published + catalog)."""
    return {w["key"] for w in _composable_workflows(db) if w["composable"]}


# --------------------------------------------------------------------------
# Read tools
# --------------------------------------------------------------------------
@tool(
    name="list_workflow_building_blocks",
    description=(
        "List the building blocks for authoring a no-code workflow (Workflow): the "
        "available step TOOLS (with side-effect class + whether they mutate), the "
        "GATE types, and the expression operators. Call this FIRST when helping an "
        "admin design or edit a workflow so you wire steps to real tools."
    ),
    required_role=_AUTHOR_ROLE,
    friendly_label="Loading workflow building blocks...",
)
async def list_workflow_building_blocks() -> Dict[str, Any]:
    from app.workflows.tool_registry import available_tools

    db = _db()
    try:
        step_tools = available_tools(db)
        available_workflows = _composable_workflows(db)
    finally:
        db.close()
    return {
        "step_tools": step_tools,
        "gate_types": _GATE_TYPES,
        # Per-type purpose + the extra fields each one accepts. `instructions` and
        # `due_in_days` are valid ONLY on manual_task; `course_code` ONLY on
        # training. Putting them elsewhere fails validation.
        "gate_type_details": _GATE_TYPE_DETAILS,
        "expression_operators": _EXPR_OPS,
        # The ONLY valid values for a subworkflow `ref`. Never invent a ref — pick
        # a key from here. `composable: true` means it resolves at runtime now
        # (published/catalog); a draft must be published before this can publish.
        "available_workflows": available_workflows,
        "spec_shape": {
            "name": "str (required)",
            "complete_fact": "optional fact written on completion",
            "stages": "ordered list of gate/step/subworkflow objects",
            "on_reject": (
                "OPTIONAL list of STEPS (same shape as a 'step' stage) that run when a "
                "gate DENIES the request, before it ends. This is the rejection branch: "
                "'stages' only describes what happens while the request is still alive. "
                "Gates and subworkflows are NOT allowed here — the decision is final. "
                "Never set 'approvals' on these steps (a gate just refused, so there is "
                "nothing to attest and it fails validation). Two extra context keys are "
                "available to $var here: 'rejection_reason' (the approver's note) and "
                "'rejected_gate' (which gate said no — use it in 'run_if' to handle "
                "different gates differently)."
            ),
            "subworkflow": {"kind": "subworkflow", "name": "str",
                            "ref": "key of an existing workflow to run inline (compound) — MUST "
                                   "be one of available_workflows keys; do not invent it",
                            "run_if": "optional expression -> bool; when false the whole "
                                      "subworkflow is SKIPPED. The conditional key is 'run_if' "
                                      "(NOT 'when'/'if'/'condition'). Omit it to always run.",
                            "input": "optional object of name -> expression mapping parent "
                                     "context into the nested workflow",
                            "writes_context": "optional list of context keys this stage contributes"},
            "gate": {"kind": "gate", "name": "str", "type": "one of gate_types",
                     "waiting_status": "optional", "auto_approve": "optional expression -> bool",
                     "approver": "optional approver source: {source:'group', group:'<name>'} for a "
                                 "hardcoded group/role, OR {source:'approver_group_tag', "
                                 "assets_from:<expr=assets>, fallback_to_owner:true} to read the UC "
                                 "approver_group tag off the request's assets",
                     "instructions": "REQUIRED on type 'manual_task' (invalid elsewhere): what the "
                                     "assignee must do off-platform before marking it done",
                     "due_in_days": "optional on type 'manual_task': SLA in days, drives overdue "
                                    "visibility in the approvals inbox"},
            "step": {"kind": "step", "name": "str", "tool": "a step_tools name",
                     "args": "object of name -> expression",
                     "approvals": "OPTIONAL advanced override; omit to auto-inherit all preceding gates",
                     "success_fact": "optional timeline marker; omit on notify/closing steps, never == complete_fact",
                     "for_each": "optional expression -> list",
                     "item_args": "object (per-item), uses $item",
                     "run_if": "optional expression -> bool; when false the step is SKIPPED "
                               "(conditional branching). Omit it to always run."},
        },
        "note": (
            "A gate's 'type' is the KIND of approval (one of gate_types) — it is NOT a "
            "group/role name. To require approval from a specific group (e.g. "
            "'edh_training_admin'), use a human gate type like 'manager' and set "
            "approver={'source':'group','group':'edh_training_admin'}; do NOT put the "
            "group name in 'type' (it fails validation). Use gate type 'training' only "
            "for training-completion gates, not to mean 'a training admin approves'. "
            "Use gate type 'manual_task' when the workflow needs work for which NO step "
            "tool exists: the request pauses, the assignee sees your 'instructions' in "
            "their approvals inbox, does the work, and clicks Mark done — then the graph "
            "continues. This is the correct answer to a tool gap; do NOT fake it with a "
            "notification step (which doesn't wait for anything) and do NOT treat a "
            "manual task as an approval — if the workflow also needs authorization, add "
            "a real human gate as well. Always give a manual task an 'approver' so "
            "someone owns it. "
            "Reserved stage names: complete, rejected, pending, completed. A step "
            "automatically inherits the approvals of every gate before it (the graph "
            "guarantees those gates passed), so you do NOT need to set 'approvals' — "
            "leave it off unless you want to override the derived set. Similarly, "
            "'success_fact' is optional (a timeline marker) — skip it on notification/"
            "closing steps, and never set it to the same value as the spec's "
            "'complete_fact'. Use a step's 'run_if' for conditional "
            "branching (e.g. only notify security when tier == 'high'). "
            "REJECTION: a denied request always ends — every gate's refusal path is "
            "terminal — and the platform emails the requester a default notice with the "
            "approver's reason, so you do NOT need to add anything for them to be told. "
            "Add 'on_reject' steps only for handling this workflow specifically needs on "
            "a denial (a differently-worded message, notifying a system of record, "
            "releasing something reserved earlier). There is no way to continue the "
            "workflow after a rejection, and no 'rejected' stage may be declared in "
            "'stages' — put those steps in 'on_reject' instead. To COMPOSE "
            "an existing capability, add a 'subworkflow' stage whose 'ref' is a key "
            "from 'available_workflows' (this makes the workflow 'compound'); it runs "
            "inline as a nested graph — its gates pause/resume like native ones and "
            "a rejection inside it rejects the parent. To run a subworkflow "
            "conditionally, set its 'run_if' (NOT 'when') to an expression -> bool. "
            "Never invent a subworkflow 'ref' — it must match an available_workflows "
            "key, or validation/publish will fail. Prefer composing over duplicating "
            "stages. The deprecated 'children' gate / 'spawn_child_request' tool are "
            "superseded by subworkflow stages — don't use them. This tool is the single "
            "source of truth for building blocks; do not rely on the Context Catalog for "
            "authoring mechanics."
        ),
    }


class SearchSimilarWorkflowsInput(BaseModel):
    description: str = Field(
        ...,
        description="A natural-language description of the workflow the admin wants to build (its goal/what it does).",
    )


@tool(
    name="search_similar_workflows",
    description=(
        "Search EXISTING workflows (Workflows) for ones similar to what the admin wants to "
        "build, by keyword-matching the description against each workflow's key, name, and "
        "goal. Call this BEFORE drafting a new workflow: if a close match exists, suggest "
        "reusing/cloning/editing it instead of authoring a duplicate. Returns ranked "
        "candidates with key, name, goal, status, and request_type."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=SearchSimilarWorkflowsInput,
    friendly_label="Searching for similar workflows...",
)
async def search_similar_workflows(description: str) -> Dict[str, Any]:
    from app.services.workflow_service import WorkflowService

    # Tokenize the description into lowercased word stems; score each workflow by
    # how many tokens appear in its key/name/goal. Cheap, dependency-free, and
    # good enough to surface obvious reuse candidates.
    import re

    tokens = {t for t in re.split(r"[^a-z0-9]+", (description or "").lower()) if len(t) > 2}

    db = _db()
    try:
        workflows = WorkflowService.list_workflows(db)
        scored: List[Dict[str, Any]] = []
        for wf in workflows:
            haystack = " ".join(
                str(x or "").lower()
                for x in (wf.key, wf.name, wf.goal, wf.request_type)
            )
            score = sum(1 for t in tokens if t in haystack)
            if score <= 0:
                continue
            scored.append({
                "key": wf.key,
                "name": wf.name,
                "goal": wf.goal,
                "status": wf.status,
                "request_type": wf.request_type,
                "match_score": score,
            })
        scored.sort(key=lambda c: c["match_score"], reverse=True)
        top = scored[:8]
        return {
            "query": description,
            "matches": top,
            "count": len(top),
            "note": (
                "If a candidate is close, prefer reusing it: clone or edit it with "
                "get_workflow rather than authoring a new duplicate workflow."
                if top
                else "No similar existing workflows found — authoring a new one is appropriate."
            ),
        }
    finally:
        db.close()


class ResearchWorkflowContextInput(BaseModel):
    topic: str = Field(
        ...,
        description=(
            "What the workflow is about, in a few words — e.g. 'read access to a "
            "Unity Catalog table' or 'provisioning a new workspace'. Used to search "
            "the organization's curated knowledge base."
        ),
    )
    request_type: Optional[str] = Field(
        default=None,
        description="The workflow's request_type / key, if known — searched as an extra term.",
    )


@tool(
    name="research_workflow_context",
    description=(
        "Research the organization's own conventions BEFORE drafting a workflow's "
        "instructions. Runs a Context Catalog pass for this topic (naming "
        "conventions, approval norms, ownership rules, policy constraints, expiry/"
        "access-review requirements) and returns the matching passages with their "
        "document titles, plus a checklist of what to fold into the playbook. Call "
        "this BEFORE authoring instructions_markdown so the workflow reflects how "
        "THIS company works rather than generic Databricks advice — and cite the "
        "document titles you used."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=ResearchWorkflowContextInput,
    friendly_label="Researching your organization's conventions...",
)
async def research_workflow_context(
    topic: str, request_type: Optional[str] = None
) -> Dict[str, Any]:
    from app.services.context_catalog_service import ContextCatalogService

    # One call, several angles: an author shouldn't have to know to search the
    # catalog four times, and "did anyone write down our naming convention?" is
    # exactly the knowledge that makes generated instructions worth following.
    facets = {
        "conventions": f"{topic} naming convention standard",
        "approvals": f"{topic} approval process who approves",
        "ownership": f"{topic} ownership data owner responsible",
        "policy": f"{topic} policy requirement restriction compliance",
    }
    if request_type:
        facets["request_type"] = f"{request_type} {topic}"

    db = _db()
    try:
        domains = [
            {"slug": d.slug, "name": d.name}
            for d in ContextCatalogService.list_domains(db)
        ]
        seen: Dict[str, Dict[str, Any]] = {}
        by_facet: Dict[str, List[str]] = {}
        for facet, query in facets.items():
            try:
                results = ContextCatalogService.search(db, query, limit=4, track_usage=True)
            except Exception:  # noqa: BLE001 - a facet miss must not fail the pass
                results = []
            titles: List[str] = []
            for r in results:
                doc_id = str(r.get("document_id"))
                titles.append(str(r.get("document_title") or doc_id))
                if doc_id in seen:
                    continue
                seen[doc_id] = {
                    "document_id": doc_id,
                    "document_title": r.get("document_title"),
                    "domain": r.get("domain_name"),
                    "source_url": r.get("source_url"),
                    # Trimmed: enough to quote and cite without flooding the turn.
                    "content": (r.get("content") or "")[:1200],
                    "matched_facets": [facet],
                }
            by_facet[facet] = sorted(set(titles))
            for t in titles:
                for entry in seen.values():
                    if entry["document_title"] == t and facet not in entry["matched_facets"]:
                        entry["matched_facets"].append(facet)
    finally:
        db.close()

    passages = list(seen.values())
    return {
        "topic": topic,
        "domains_available": domains,
        "passages": passages,
        "titles_by_facet": by_facet,
        "count": len(passages),
        # The checklist is the point: it's what the playbook must answer, whether or
        # not the catalog had a document about it.
        "checklist": [
            "Naming conventions the requester must follow (and what to reject).",
            "Who approves, in what order, and what they are signing off on.",
            "Who owns the resource afterwards and who to route questions to.",
            "Policy constraints: least privilege, expiry / access review, restricted data.",
            "What justification or business context the requester must provide.",
            "What the agent should push back on rather than fulfil.",
        ],
        "note": (
            "Fold these into instructions_markdown and CITE the document titles you "
            "used (e.g. 'per the Data Access Standard'). For any checklist item the "
            "catalog does NOT cover, either ask the admin or state your assumption "
            "explicitly in the instructions' Assumptions section — do not invent "
            "internal policy."
            if passages
            else "The catalog had nothing on this topic. Do NOT invent internal policy: "
            "ask the admin the checklist questions above, and record whatever they "
            "can't answer yet under 'Open questions & risks' in the instructions."
        ),
    }


class GetWorkflowInput(BaseModel):
    key: str = Field(..., description="The workflow key (workflow identifier), e.g. 'workspace_access'.")


@tool(
    name="get_workflow",
    description=(
        "Fetch an existing workflow (Workflow) by key, including its current graph_spec, "
        "instructions_markdown, status (draft/published), request_type, and metadata. Use "
        "this to inspect a workflow before editing it: build on the returned graph_spec and "
        "REFINE the existing instructions_markdown — do not start from scratch or you'll "
        "discard prior admin edits."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=GetWorkflowInput,
    friendly_label="Loading workflow...",
)
async def get_workflow(key: str) -> Dict[str, Any]:
    from app.services.workflow_service import WorkflowService

    db = _db()
    try:
        workflow = WorkflowService.get_by_key(db, key)
        if not workflow:
            existing = [s.key for s in WorkflowService.list_workflows(db)]
            return {"found": False,
                    "error": f"No workflow with key '{key}'.",
                    "available_keys": existing}
        return {
            "found": True,
            "key": workflow.key,
            "name": workflow.name,
            "status": workflow.status,
            "version": workflow.version,
            "request_type": workflow.request_type,
            "goal": workflow.goal,
            "graph_spec": workflow.graph_spec,
            # Include the current instructions + scoping so an edit REFINES what's
            # there instead of regenerating from scratch (which would drop admin
            # wording). Note: the ``## Execution`` block in instructions_markdown is
            # auto-derived from the graph — edit the spec, not that block, to change it.
            "instructions_markdown": workflow.instructions_markdown,
            "allowed_tools": workflow.allowed_tools,
            "policy_ref": workflow.policy_ref,
        }
    finally:
        db.close()


class ValidateSpecInput(BaseModel):
    graph_spec: Dict[str, Any] = Field(..., description="The candidate workflow graph_spec (JSON object with name + stages).")


@tool(
    name="validate_workflow_spec",
    description=(
        "Structurally validate a candidate workflow graph_spec WITHOUT saving it: "
        "checks stage shapes, gate types, that each step's tool exists, and that all "
        "expressions are well-formed. Returns {valid, error}. Always validate before "
        "saving or publishing."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=ValidateSpecInput,
    friendly_label="Validating workflow...",
)
async def validate_workflow_spec(graph_spec: Dict[str, Any]) -> Dict[str, Any]:
    from app.workflows.spec_loader import (
        SpecError,
        lint_step_tool_args,
        lint_subworkflow_refs,
        validate_spec_dict,
    )

    try:
        validate_spec_dict(graph_spec)
    except SpecError as e:
        return {"valid": False, "error": str(e)}
    # Structurally valid — surface non-blocking lints so the author catches wrong/
    # missing tool args (which **kwargs would otherwise swallow) and subworkflow
    # refs that don't name a real workflow (which would hard-fail at publish).
    db = _db()
    try:
        warnings = lint_step_tool_args(graph_spec) + lint_subworkflow_refs(
            graph_spec, _composable_keys(db)
        )
    finally:
        db.close()
    return {"valid": True, "warnings": warnings}


class PreviewSpecInput(BaseModel):
    graph_spec: Dict[str, Any] = Field(..., description="The candidate workflow graph_spec to project.")
    sample_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Sample request context (the fields a real request would carry) to evaluate expressions against.",
    )


@tool(
    name="preview_workflow_spec",
    description=(
        "Dry-run a candidate workflow graph_spec against a sample request context "
        "WITHOUT running any tool or writing anything. Projects which gates auto-approve, "
        "the exact args each step would receive, and fan-out — so you can confirm behavior "
        "with the admin before saving/publishing."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=PreviewSpecInput,
    friendly_label="Previewing workflow...",
)
async def preview_workflow_spec(
    graph_spec: Dict[str, Any], sample_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    from app.workflows.dry_run import project_run
    from app.workflows.spec_loader import lint_step_tool_args, lint_subworkflow_refs

    try:
        projection = project_run(graph_spec, sample_context or {})
    except Exception as e:  # noqa: BLE001 - surface to the agent so it can fix the spec
        return {"ok": False, "error": str(e)}
    db = _db()
    try:
        warnings = lint_step_tool_args(graph_spec) + lint_subworkflow_refs(
            graph_spec, _composable_keys(db)
        )
    finally:
        db.close()
    return {"ok": True, "projection": projection, "warnings": warnings}


class EvaluateSpecInput(BaseModel):
    graph_spec: Dict[str, Any] = Field(..., description="The candidate workflow graph_spec to evaluate.")


@tool(
    name="evaluate_workflow_spec",
    description=(
        "Evaluate a candidate workflow graph_spec for SAFETY and COMPLETENESS without "
        "saving it. Returns a deterministic report: a risk score 0-100 (higher = riskier) "
        "+ tier, a quality score 0-100 (higher = better) + tier, and findings (each with "
        "severity, category, message, and a fix). Call this after validate/preview and "
        "BEFORE recommending save/publish: read the findings back to the admin in plain "
        "language, explain the scores, and propose concrete fixes for any high/critical "
        "items (e.g. a risky mutation with no approval gate)."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=EvaluateSpecInput,
    friendly_label="Evaluating workflow...",
)
async def evaluate_workflow_spec(graph_spec: Dict[str, Any]) -> Dict[str, Any]:
    from app.workflows.evaluator import evaluate_spec

    db = _db()
    try:
        return evaluate_spec(graph_spec, db)
    finally:
        db.close()


# --------------------------------------------------------------------------
# Mutating tools (DB-only; governed + audited via the ToolExecutor)
# --------------------------------------------------------------------------
class SaveDraftInput(BaseModel):
    key: str = Field(..., description="Workflow key. Created if new, updated (as a draft) if it exists.")
    graph_spec: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "The workflow graph_spec to save. REQUIRED when creating a new workflow. "
            "On an UPDATE you may omit it to keep the stored graph unchanged — do that "
            "when you're only revising instructions_markdown, goal, or name, so you "
            "don't have to resend the whole graph (and risk truncating it)."
        ),
    )
    name: Optional[str] = Field(default=None, description="Human-friendly workflow name (defaults to key).")
    request_type: Optional[str] = Field(
        default=None,
        description="The RequestType value this workflow governs (required for it to run; can be set before publish).",
    )
    goal: Optional[str] = Field(
        default=None,
        description=(
            "The workflow's ROUTING LINE — one sentence. At runtime this is the "
            "workflow's ENTIRE entry in the self-service agent's Capabilities menu "
            "(rendered as '- <key>: <goal>'), and it is ALL the agent sees when it "
            "decides which workflow a user's message means; the instructions_markdown "
            "playbook is only fetched afterwards. So write it to DISCRIMINATE, not to "
            "summarize: what the user gets, when to pick this one, and what it does "
            "NOT cover when a similar workflow exists. Name the boundary explicitly "
            "(existing vs. new, one asset vs. bulk, read vs. write). Check the "
            "neighbours with search_similar_workflows first — two lines that read "
            "alike make the agent guess. Never 'Fulfill a <name> request' (the "
            "auto-stub) and never a restatement of the key."
        ),
    )
    instructions_markdown: Optional[str] = Field(
        default=None,
        description=(
            "The runtime playbook (markdown) the self-service agent follows to gather inputs "
            "from the user, validate them, and format the execute_workflow call. STRONGLY "
            "recommended on every save. If omitted OR left blank, only a THIN baseline is "
            "auto-generated from the graph (a goal stub + the $var list + a flow overview) and "
            "the response will flag instructions_auto_generated=true with a warning to author "
            "and re-save — i.e. the workflow will be 'just the graph' until you supply real "
            "instructions. Pass a full playbook here rather than relying on that fallback."
        ),
    )
    take_offline: bool = Field(
        default=False,
        description=(
            "Only needed when the workflow is currently PUBLISHED. A workflow has one "
            "definition, so saving a draft over a published one takes it OFF the "
            "Capabilities menu until someone publishes again — live users stop being "
            "able to start it. Leave this false: the save is refused and you should "
            "tell the admin the workflow is live and ask whether to take it offline to "
            "edit. Set it to true only after they say yes IN THIS CONVERSATION."
        ),
    )


@tool(
    name="save_workflow_draft",
    description=(
        "Save a workflow graph_spec as a DRAFT (creates the Workflow if new, else updates it "
        "and marks it draft). Validates the spec first and refuses to save an invalid one. "
        "Auto-generates baseline runtime instructions from the spec when none are supplied. "
        "Does NOT publish — the workflow won't affect live requests until published, so you "
        "do NOT need permission to save. Save as part of the design turn, right after you "
        "validate and preview: a draft is reversible (every save is snapshotted for undo) "
        "and it is what test cases attach to, so waiting for confirmation just leaves the "
        "design unsaved and untested. Tell the admin you saved a draft, and ask before "
        "PUBLISHING, not before saving. ONE exception: if the workflow is already "
        "PUBLISHED, saving would take it offline, so the save is refused until the admin "
        "agrees (see take_offline)."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=SaveDraftInput,
    side_effect_class="app_write",
    friendly_label="Saving workflow draft...",
    friendly_completion_label="Workflow draft saved",
)
async def save_workflow_draft(
    key: str,
    graph_spec: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
    request_type: Optional[str] = None,
    goal: Optional[str] = None,
    instructions_markdown: Optional[str] = None,
    take_offline: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    from app.services.workflow_service import WorkflowService
    from app.workflows.instructions import render_instructions_markdown
    from app.workflows.spec_loader import (
        SpecError,
        lint_step_tool_args,
        lint_subworkflow_refs,
        validate_spec_dict,
    )

    if _authoring_locked():
        return {"ok": False, "locked": True, "error": _LOCKED_MSG}

    actor = kwargs.get("_user_email")
    db = _db()
    try:
        existing = WorkflowService.get_by_key(db, key)
        # A workflow has ONE definition and a status column, so "save a draft" over
        # a published workflow means demoting it: it leaves the Capabilities menu
        # and live users can no longer start it. That is an outage, and nothing in
        # "add a field to the intake questions" asks for one — the agent saves
        # without asking permission precisely because a draft is supposed to be
        # inert. Refuse and make the blast radius the admin's explicit decision.
        if existing is not None and existing.status == "published" and not take_offline:
            return {
                "ok": False,
                "requires_confirmation": True,
                "workflow_status": "published",
                "error": (
                    f"'{key}' is PUBLISHED and live right now. This platform keeps one "
                    "definition per workflow, so saving a draft over it would take it "
                    "off the Capabilities menu until it is published again — anyone who "
                    "tries to start it in the meantime can't. Nothing was saved."
                ),
                "next_action": (
                    "Tell the admin the workflow is live and ask whether to take it "
                    "offline to make this edit (they may prefer to schedule it). If they "
                    "agree, call save_workflow_draft again with take_offline=true, then "
                    "run the tests and publish it again in the same turn so the gap is "
                    "as short as possible."
                ),
            }
        # A follow-up save that only revises the playbook doesn't need the graph
        # resent. Requiring it meant the model had to reproduce the entire spec to
        # change one paragraph — and a save that omitted it failed with "Field
        # required: graph_spec", which reads like a bug in the model's call rather
        # than a field it could simply leave out.
        if graph_spec is None:
            if not (existing and existing.graph_spec):
                return {
                    "ok": False,
                    "error": (
                        f"No workflow '{key}' exists yet, so graph_spec is required to "
                        "create it. Pass the full spec you validated."
                    ),
                }
            graph_spec = existing.graph_spec
            reused_stored_spec = True
        else:
            reused_stored_spec = False

        try:
            validate_spec_dict(graph_spec)
        except SpecError as e:
            return {"ok": False, "error": f"Invalid graph_spec, not saved: {e}"}

        arg_warnings = lint_step_tool_args(graph_spec) + lint_subworkflow_refs(
            graph_spec, _composable_keys(db)
        )
        fields: Dict[str, Any] = {"graph_spec": graph_spec, "status": "draft"}
        if name:
            fields["name"] = name
        if request_type:
            fields["request_type"] = request_type
        if goal:
            fields["goal"] = goal
        # Runtime instructions: honor an explicit value, otherwise auto-derive a
        # baseline from the spec so the self-service agent never gets a blank
        # (the #1 cause of "the workflow does nothing when I run it"). Treat an
        # empty/whitespace-only string the same as omitted: agents frequently
        # pass "" for optional fields, which would otherwise defeat the safety
        # net and persist blank instructions (and a blank Details page).
        has_explicit_instructions = bool(instructions_markdown and instructions_markdown.strip())
        # Track whether this save had to fall back to the graph-derived baseline so
        # we can tell the agent. Without this signal the agent gets a bare ok:True,
        # never realizes it skipped authoring the runtime playbook, and the workflow
        # ends up as "just the graph" (a thin auto-stub) — the exact complaint.
        instructions_auto_generated = False
        if has_explicit_instructions:
            fields["instructions_markdown"] = instructions_markdown
        elif not (existing and existing.instructions_markdown):
            fields["instructions_markdown"] = render_instructions_markdown(
                graph_spec,
                request_type=request_type or (existing.request_type if existing else None),
                goal=goal or (existing.goal if existing else None),
            )
            instructions_auto_generated = True
        took_offline = existing is not None and existing.status == "published"
        if existing:
            # Back up the current body before overwriting it. An assistant save
            # lands on top of whatever the admin had — including hand edits it
            # never saw — and publishing used to be the only thing that
            # snapshotted, so those edits were unrecoverable.
            WorkflowService.snapshot_draft(
                db, existing.id, actor=actor, note="before authoring assistant save",
            )
            workflow = WorkflowService.update(db, existing.id, **fields)
            action = "updated"
        else:
            workflow = WorkflowService.create(db, created_by=actor, key=key, **fields)
            action = "created"
        warnings: List[str] = list(arg_warnings)
        if took_offline:
            warnings.append(
                f"'{key}' WAS PUBLISHED and is now a draft, so it is off the "
                "Capabilities menu and nobody can start it until you publish again. "
                "Say this to the admin plainly, and finish the job in this turn: run "
                "the tests and publish, or roll back."
            )
        if not workflow.request_type:
            warnings.append("No request_type set — set one before publishing or the graph won't run.")
        if instructions_auto_generated:
            warnings.append(
                "instructions_markdown was NOT provided, so a THIN baseline was "
                "auto-generated from the graph (just a goal stub, the $var input "
                "list, and a flow overview). This is the 'just the graph' fallback — "
                "the runtime self-service agent has no real playbook to gather inputs "
                "or validate them. Author proper instructions (a '## Information to "
                "Gather' numbered list with per-field description/required/format/hint, "
                "'## Validation & Guidance', and '## Approvals & Flow') and call "
                "save_workflow_draft again with instructions_markdown set."
            )
        # Score the playbook that was actually persisted. The graph evaluation says
        # nothing about the text the runtime agent follows, so without this the
        # agent has no way to know its instructions are thin.
        from app.workflows.instructions_quality import score_instructions

        instructions_quality = score_instructions(
            workflow.instructions_markdown, workflow.graph_spec
        )
        if instructions_quality["score"] < 65:
            gaps = "; ".join(
                f["message"] for f in instructions_quality["findings"][:3]
            )
            warnings.append(
                f"Instruction quality is {instructions_quality['score']}/100 "
                f"({instructions_quality['tier']}). Fix these before offering to "
                f"publish: {gaps}"
            )

        # Score the goal against the menu it will compete in. The goal is the whole
        # Capabilities entry the runtime agent routes from, so a stub or a line that
        # reads like a sibling's is a routing bug — invisible without this check.
        from app.workflows.goal_quality import menu_siblings, score_goal

        goal_quality = score_goal(
            workflow.goal,
            key=workflow.key,
            name=workflow.name,
            siblings=menu_siblings(
                WorkflowService.list_published(db), exclude_key=workflow.key,
            ),
        )
        if goal_quality["score"] < 65:
            problems = "; ".join(f["message"] for f in goal_quality["findings"][:2])
            warnings.append(
                f"Goal quality is {goal_quality['score']}/100 "
                f"({goal_quality['tier']}). The goal is this workflow's ENTIRE line "
                f"in the runtime agent's Capabilities menu, so this is a routing "
                f"problem, not cosmetics: {problems} Rewrite the goal and save again."
            )
        return {
            "ok": True,
            "action": action,
            "key": workflow.key,
            "status": workflow.status,
            "version": workflow.version,
            "request_type": workflow.request_type,
            # Return the persisted playbook so the authoring UI can hydrate its
            # instructions field directly from this result — the studio mirrors the
            # graph live but had no instructions to show until a (race-prone)
            # canonical reload, which is why a new workflow looked "just the graph".
            "instructions_markdown": workflow.instructions_markdown,
            # Explicit, machine-readable signal so the agent (and UI) can tell an
            # authored playbook from the auto-baseline instead of guessing.
            "instructions_source": "auto_baseline" if instructions_auto_generated else "authored",
            "instructions_auto_generated": instructions_auto_generated,
            # Deterministic rubric over the playbook: which required sections are
            # present, whether every $var the graph consumes is documented, whether
            # this is still the baseline stub.
            "instructions_quality": instructions_quality,
            # Deterministic rubric over the Capabilities menu line, including which
            # published workflows it currently reads like.
            "goal_quality": goal_quality,
            # Tell the agent when it left the graph alone, so a follow-up save that
            # only touched the playbook can't be mistaken for one that reshaped the
            # flow (and so it knows omitting graph_spec worked as intended).
            "graph_spec_unchanged": reused_stored_spec,
            # True when this save demoted a live workflow (only reachable with the
            # admin's explicit take_offline=true).
            "took_offline": took_offline,
            "warnings": warnings,
            "note": (
                "Saved as a draft, but with an AUTO-GENERATED baseline playbook — "
                "author real instructions_markdown and re-save before publishing. "
                if instructions_auto_generated
                else "Saved as a draft. Publish it (publish_workflow) to make it live."
            ),
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


class WorkflowTestCase(BaseModel):
    # Aliases because models reliably guess ``title``/``input`` here, and a
    # rejected call costs an iteration out of the turn's budget — which is how a
    # design turn ran out of room before it could run the tests it just saved.
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        ...,
        validation_alias=AliasChoices("name", "title", "label"),
        description="Short label, e.g. 'missing required field'.",
    )
    question: str = Field(
        ...,
        validation_alias=AliasChoices("question", "input", "prompt", "user_message"),
        description="Exactly what a user would type to start this case. Use realistic values.",
    )
    expected_outcome: str = Field(
        ...,
        validation_alias=AliasChoices(
            "expected_outcome", "expected", "expectation", "expected_behavior",
        ),
        description=(
            "What SHOULD happen, in plain English, checkable from a transcript: what the "
            "agent asks for, what it refuses, which tool it calls. 'Handles it correctly' "
            "is useless. Never expect a value the user didn't supply."
        ),
    )


class SaveWorkflowTestsInput(BaseModel):
    key: str = Field(..., description="Workflow key these cases belong to. Must already exist.")
    cases: List[WorkflowTestCase] = Field(
        ...,
        description=(
            "3-5 cases covering the happy path, a missing required field, an out-of-scope "
            "ask the workflow must refuse, an ambiguous input, and the rejection path."
        ),
    )


@tool(
    name="save_workflow_tests",
    description=(
        "Save behavioral test cases for a workflow so the admin can verify it actually "
        "behaves as intended. Each case is a question plus a plain-English expected "
        "outcome; running one starts the real agent with every mutating tool sandboxed "
        "and an LLM judge compares the transcript to the expectation. Propose 3-5 cases "
        "right after saving a draft — happy path, missing required field, out-of-scope "
        "refusal, ambiguous input, rejection path — and then CALL run_workflow_tests "
        "yourself in the same turn; saving cases you never run proves nothing. "
        "Hand-written (admin-authored) cases are never replaced by this tool."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=SaveWorkflowTestsInput,
    side_effect_class="app_write",
    friendly_label="Saving workflow tests...",
    friendly_completion_label="Workflow tests saved",
)
async def save_workflow_tests(
    key: str,
    cases: List[Dict[str, Any]],
    **kwargs: Any,
) -> Dict[str, Any]:
    from app.services.workflow_service import WorkflowService
    from app.services.workflow_test_service import WorkflowTestService

    if _authoring_locked():
        return {"ok": False, "locked": True, "error": _LOCKED_MSG}

    actor = kwargs.get("_user_email")
    db = _db()
    try:
        workflow = WorkflowService.get_by_key(db, key)
        if workflow is None:
            return {
                "ok": False,
                "error": (
                    f"No workflow with key '{key}'. Save the draft first "
                    f"(save_workflow_draft), then attach its test cases."
                ),
            }
        normalized: List[Dict[str, Any]] = []
        for case in cases or []:
            if isinstance(case, dict):
                normalized.append(case)
            else:  # a pydantic model instance when called in-process
                normalized.append(case.model_dump())
        saved = WorkflowTestService.replace_tests(
            db, workflow.id, normalized, source="agent", created_by=actor,
        )
        skipped = len(normalized) - len(saved)
        return {
            "ok": True,
            "workflow_key": workflow.key,
            "saved": len(saved),
            # Surfaced so the studio can hydrate the Tests tab from this result
            # instead of waiting for a refetch.
            "tests": [
                {
                    "id": row.id,
                    "name": row.name,
                    "question": row.question,
                    "expected_outcome": row.expected_outcome,
                }
                for row in saved
            ],
            "warnings": (
                [f"{skipped} case(s) were dropped for missing a question or expected outcome."]
                if skipped > 0 else []
            ),
            # The next step is stated as a directive with the ids ready to pass:
            # saving used to end with "tell the admin to click Run all", and that
            # was the last thing the model read, so it handed off instead of
            # running the suite it had just written.
            "next_action": "run_workflow_tests",
            "next_action_args": {"key": workflow.key,
                                 "test_ids": [row.id for row in saved]},
            "note": (
                f"Saved {len(saved)} case(s). NOW RUN THEM: call run_workflow_tests "
                f"with key='{workflow.key}' IN THIS TURN, before you reply to the "
                f"admin. Running is safe and cheap — the real agent runs with every "
                f"mutating tool sandboxed, so nothing is provisioned. Do not ask the "
                f"admin to go click Run, and do not end your turn here: a case you "
                f"wrote but never ran tells nobody whether the workflow works. Any "
                f"cases the admin wrote themselves were preserved."
            ),
        }
    finally:
        db.close()


class ListWorkflowTestsInput(BaseModel):
    key: str = Field(..., description="Workflow key whose test cases and latest results to read.")
    include_transcripts: bool = Field(
        default=False,
        description=(
            "Include the agent transcript for each case. Use this when diagnosing a "
            "failure — it shows what the agent actually said and called. Verbose, so "
            "leave it off when you only need verdicts."
        ),
    )


@tool(
    name="list_workflow_tests",
    description=(
        "Read a workflow's behavioral test cases AND the result of the most recent run "
        "of each: verdict, score, the judge's rationale, what it found missing, which "
        "tools the agent called, and (optionally) the transcript. Call this when the "
        "admin asks how the tests did, says a case failed, or before changing a "
        "workflow you have already tested — it is the only way to see what actually "
        "happened rather than guessing."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=ListWorkflowTestsInput,
    friendly_label="Reading workflow tests...",
)
async def list_workflow_tests(
    key: str, include_transcripts: bool = False, **kwargs: Any
) -> Dict[str, Any]:
    from app.services.workflow_service import WorkflowService
    from app.services.workflow_test_service import (
        WorkflowTestService, run_to_dict, test_to_dict,
    )

    db = _db()
    try:
        workflow = WorkflowService.get_by_key(db, key)
        if workflow is None:
            return {"ok": False, "error": f"No workflow with key '{key}'."}
        cases = WorkflowTestService.list_tests(db, workflow.id)
        latest = WorkflowTestService.latest_runs(db, workflow.id)
        out_cases: List[Dict[str, Any]] = []
        for case in cases:
            entry = test_to_dict(case)
            run = latest.get(case.id)
            if run is None:
                entry["latest_run"] = None
                entry["result"] = "never run"
            else:
                run_dict = run_to_dict(run, include_transcript=include_transcripts)
                if include_transcripts:
                    # Keep the tool payload reviewable: the judge already
                    # summarized the run, so a full transcript is for diagnosis.
                    run_dict["transcript"] = _trim_transcript(run_dict.get("transcript"))
                entry["latest_run"] = run_dict
                entry["result"] = (
                    run.status if run.status != "complete" else (run.verdict or "unscored")
                )
            out_cases.append(entry)
        health = WorkflowTestService.health(db, workflow.id)
        return {
            "ok": True,
            "workflow_key": workflow.key,
            "tests": out_cases,
            "health": health,
            "note": _tests_note(health),
        }
    finally:
        db.close()


def _trim_transcript(transcript: Any, *, max_entries: int = 12, max_chars: int = 1200):
    """Cap a stored transcript so a diagnosis doesn't blow the agent's context."""
    if not isinstance(transcript, list):
        return []
    trimmed = []
    for entry in transcript[:max_entries]:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        content = item.get("content")
        if isinstance(content, str) and len(content) > max_chars:
            item["content"] = content[:max_chars] + "...[truncated]"
        trimmed.append(item)
    if len(transcript) > max_entries:
        trimmed.append({"role": "note",
                        "content": f"...{len(transcript) - max_entries} more message(s) omitted"})
    return trimmed


def _tests_note(health: Dict[str, Any]) -> str:
    if health.get("total", 0) == 0:
        return (
            "This workflow has no test cases, so nothing verifies that it behaves the "
            "way the admin expects. Propose 3-5 with save_workflow_tests."
        )
    if health.get("never_run"):
        return (
            f"{health['never_run']} case(s) have never been run — their result is "
            f"unknown, which is not the same as passing. Run them with run_workflow_tests."
        )
    if health.get("ready"):
        return "Every enabled case has been run and passed."
    return (
        f"{health.get('failing', 0)} failing and {health.get('errored', 0)} errored "
        f"case(s). Read each rationale before changing anything: the workflow may be "
        f"wrong, or the expectation may be."
    )


class RunWorkflowTestsInput(BaseModel):
    key: str = Field(..., description="Workflow key whose cases to run.")
    test_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Specific case ids to run (from list_workflow_tests). Omit to run every "
            "enabled case. Pass just the failing ids when re-checking a fix."
        ),
    )


@tool(
    name="run_workflow_tests",
    description=(
        "RUN a workflow's behavioral test cases and wait for the results. Each case "
        "starts the real self-service agent against this workflow's saved instructions "
        "and tools with every mutating tool sandboxed (nothing is provisioned, no "
        "audit facts are written), then an LLM judge scores the transcript against the "
        "case's expected outcome. Returns each verdict with the judge's rationale. "
        "Tests run against the SAVED workflow, so save your changes first. Use this to "
        "close the loop: save, run, read the failures, fix the instructions or the "
        "expectation, run again."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=RunWorkflowTestsInput,
    side_effect_class="app_write",
    friendly_label="Running workflow tests...",
    friendly_completion_label="Workflow tests finished",
)
async def run_workflow_tests(
    key: str, test_ids: Optional[List[str]] = None, **kwargs: Any
) -> Dict[str, Any]:
    import asyncio

    from app.core.config import settings
    from app.services.workflow_service import WorkflowService
    from app.services.workflow_test_service import (
        WorkflowTestService, run_to_dict,
    )
    from app.workflows.test_runner import run_group_in_thread

    if not getattr(settings, "WORKFLOW_TESTS_ENABLED", True):
        return {
            "ok": False,
            "error": (
                "Workflow tests are disabled in this environment (Admin → Settings → "
                "Workflow tests)."
            ),
        }

    actor = kwargs.get("_user_email")
    db = _db()
    try:
        workflow = WorkflowService.get_by_key(db, key)
        if workflow is None:
            return {"ok": False, "error": f"No workflow with key '{key}'."}
        cases = WorkflowTestService.list_tests(db, workflow.id)
        if test_ids:
            wanted = set(test_ids)
            cases = [c for c in cases if c.id in wanted]
        else:
            cases = [c for c in cases if c.enabled]
        if not cases:
            return {
                "ok": False,
                "error": (
                    "There are no enabled test cases to run. Propose some with "
                    "save_workflow_tests first."
                ),
            }

        # Same per-admin budget the Tests tab enforces: this starts real agent
        # conversations, so an agent looping on "run again" must not be able to
        # spend without limit.
        limit = int(getattr(settings, "WORKFLOW_TEST_RUNS_PER_HOUR", 60) or 60)
        recent = WorkflowTestService.recent_run_count(db, actor)
        if recent + len(cases) > limit:
            return {
                "ok": False,
                "rate_limited": True,
                "error": (
                    f"Test run limit reached ({recent} of {limit} cases in the last "
                    f"hour). Tell the admin to wait or raise the limit in Admin → "
                    f"Settings → Workflow tests. Do not retry."
                ),
            }

        group_id, runs = WorkflowTestService.create_run_group(
            db, workflow.id, cases, triggered_by=actor,
        )
    finally:
        db.close()

    # Executed on the runner's own thread (its own event loop) exactly as the UI
    # path does, then polled here — a case is a full agent conversation, and the
    # model client is not safe to drive from the request loop.
    run_group_in_thread(group_id)

    per_case = max(30, int(getattr(settings, "WORKFLOW_TEST_TIMEOUT_SECONDS", 180) or 180))
    concurrency = max(1, int(getattr(settings, "WORKFLOW_TEST_CONCURRENCY", 2) or 2))
    waves = max(1, -(-len(runs) // concurrency))
    budget = min(_RUN_WAIT_CAP_SECONDS, per_case * waves + 15)

    deadline = asyncio.get_running_loop().time() + budget
    finished: List[Any] = []
    while True:
        await asyncio.sleep(2)
        poll = _db()
        try:
            current = WorkflowTestService.get_run_group(poll, group_id)
            done = all(r.status in ("complete", "error") for r in current)
            finished = [run_to_dict(r, include_transcript=False) for r in current]
        finally:
            poll.close()
        if done or asyncio.get_running_loop().time() >= deadline:
            break

    passed = [r for r in finished if r.get("passed")]
    failed = [r for r in finished
              if r.get("status") == "complete" and not r.get("passed")]
    errored = [r for r in finished if r.get("status") == "error"]
    pending = [r for r in finished if r.get("status") in ("queued", "running")]

    summary = {
        "ok": True,
        "workflow_key": key,
        "run_group_id": group_id,
        "total": len(finished),
        "passed": len(passed),
        "failed": len(failed),
        "errored": len(errored),
        "still_running": len(pending),
        "results": finished,
    }
    if pending:
        summary["note"] = (
            f"{len(pending)} case(s) were still running when the wait budget ran out. "
            f"They are still executing in the background — tell the admin that, then "
            f"call list_workflow_tests to read the result. Do NOT start another run; "
            f"that would double-charge the same cases."
        )
    elif failed or errored:
        summary["note"] = (
            "Read each failing case's rationale and 'missing' list before changing "
            "anything, then decide WHICH is wrong: the workflow (instructions don't "
            "tell the agent to do what you expect) or the expectation (it asked for "
            "something the workflow was never meant to do). Say which one you think it "
            "is and why, fix that, save, and run only the failing ids again. If a "
            "transcript would help, call list_workflow_tests with "
            "include_transcripts=true."
        )
    else:
        summary["note"] = "Every case passed. Safe to offer the admin a publish."
    return summary


class PublishWorkflowInput(BaseModel):
    key: str = Field(..., description="Workflow key to publish. Must already exist as a draft with a valid graph_spec.")


@tool(
    name="publish_workflow",
    description=(
        "Publish a workflow (Workflow) so it governs live requests of its request_type. Runs "
        "the full pre-publish gate (structural validation + compiles the spec + resolves every "
        "tool) and refuses to publish an invalid one. This is consequential — only call after "
        "the admin EXPLICITLY confirms they want it live. Snapshots an immutable version for rollback."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=PublishWorkflowInput,
    side_effect_class="app_write",
    friendly_label="Publishing workflow...",
    friendly_completion_label="Workflow published",
)
async def publish_workflow(key: str, **kwargs: Any) -> Dict[str, Any]:
    from app.services.workflow_service import WorkflowService
    from app.workflows.dry_run import project_run
    from app.workflows.spec_loader import validate_spec_dict

    if _authoring_locked():
        return {"ok": False, "locked": True, "error": _LOCKED_MSG}

    actor = kwargs.get("_user_email")
    db = _db()
    try:
        workflow = WorkflowService.get_by_key(db, key)
        if not workflow:
            return {"ok": False, "error": f"No workflow with key '{key}' to publish."}
        if not workflow.graph_spec:
            return {"ok": False, "error": f"Workflow '{key}' has no graph_spec to publish."}
        try:
            validate_spec_dict(workflow.graph_spec)
            project_run(workflow.graph_spec, {})  # compiles + resolves every tool, side-effect free
        except Exception as e:  # noqa: BLE001 - structural/compile errors block publish
            return {"ok": False, "error": f"Pre-publish check failed, not published: {e}"}
        if not workflow.request_type:
            return {"ok": False,
                    "error": "No request_type set on this workflow; set one (save_workflow_draft) before publishing."}
        published = WorkflowService.publish(db, workflow.id, published_by=actor)
        return {
            "ok": True,
            "key": published.key,
            "status": published.status,
            "version": published.version,
            "request_type": published.request_type,
            "note": "Published and live. A version snapshot was saved for rollback.",
        }
    finally:
        db.close()
