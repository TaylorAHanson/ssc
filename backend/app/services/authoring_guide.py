"""Seed an editable 'Authoring Workflows (Workflows)' guide into the Context Catalog.

The guide lives in the Context Catalog (not in code) on purpose: admins can edit
it in the Context Catalog UI to add house rules, finicky-tool notes, and naming
conventions, and the agent reads it via ``search_context_catalog`` /
``get_context_document`` when helping author a workflow. Seeding is idempotent —
the domain is matched by name and the document by title, so re-running (every
boot) never duplicates and never clobbers admin edits.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_DOMAIN_NAME = "Platform Administration"
_DOMAIN_DESCRIPTION = (
    "How to operate and customize this platform: authoring no-code workflows "
    "(Workflows), governance settings, and admin runbooks."
)
_DOC_TITLE = "Authoring Workflows (Workflows) — Guide"

# Bump when the canonical guide content changes and you want existing installs to
# pick it up. The seed only rewrites the doc when its stored revision is older.
GUIDE_REVISION = 7
_REVISION_TAG_PREFIX = "guide-rev:"

GUIDE_MARKDOWN = """\
# Authoring Workflows (Workflows)

A **workflow** (a *Workflow*) is **data, not code**: an ordered list of *stages*
compiled into a durable graph. There are two stage kinds:

- **gate** — a human/event approval the request pauses on.
- **step** — runs one governed *tool* (optionally once per item).

You (and the agent, if you're a Platform/Governance Admin) author these in chat
or in the visual editor. The agent tools are: `list_workflow_building_blocks`,
`get_workflow`, `validate_workflow_spec`, `preview_workflow_spec`,
`save_workflow_draft`, and `publish_workflow`.

## The graph_spec shape

```json
{
  "name": "workspace_access",
  "complete_fact": "access_granted",
  "stages": [
    {"kind": "gate", "name": "manager_approval", "type": "manager",
     "auto_approve": {"$eq": [{"$var": "scope"}, "enterprise"]}},
    {"kind": "step", "name": "provision", "tool": "add_group_membership",
     "approvals": ["manager"], "success_fact": "access_granted",
     "args": {"group": {"$var": "access_group"},
              "members": {"$list": [{"$var": "requested_by_email"}]}}}
  ]
}
```

Rules:
- `name` is required. Stages run **in order**.
- Stage names must be unique and **cannot** be `pending`, `complete`,
  `completed`, or `rejected` (reserved).
- A `step` after a gate should list that gate's type in **`approvals`** so policy
  enforcement sees the approval (e.g. `"approvals": ["manager"]`).
- `success_fact` (optional) is written when a step succeeds — it drives the
  request timeline / live graph view, so set it on the meaningful provisioning step.
- `complete_fact` (optional) is written when the whole workflow completes.

## Gate types

A gate's **`type`** is the *kind* of approval — it must be one of the fixed
values below. It is **NOT** a group/role name. To require approval from a
specific group (e.g. `edh_training_admin`), use a human gate type such as
`manager` and point it at the group with the **`approver`** block (see below) —
do **not** put the group name in `type` (that fails validation).

- `manager` — a human approval gate; pair with `approver` to route to a specific
  person/group. (Despite the name, use this generic human gate for any
  group-based approval.)
- `platform_admin` — a platform admin approves (use for plan→review→apply flows).
- `data_owner` — the resolved owner of the data approves.
- `training` — proceeds once training completion is recorded (NOT a human
  approval; don't use it to mean "a training admin approves").
- `pr_merge` — proceeds once the linked PR is merged.
- `children` — proceeds once all spawned child requests complete.

Example — "approval from anyone in group `edh_training_admin`":

```json
{"kind": "gate", "name": "training_admin_approval", "type": "manager",
 "approver": {"source": "group", "group": "edh_training_admin"}}
```

Gates can **auto-approve**: set `auto_approve` to an expression that returns true
to skip the pause (e.g. low-risk scopes).

### Declarative approver source (`approver`) — preferred

Any gate can name its approver(s) directly with an `approver` block — no resolve
step needed. Two sources:

```json
// 1) Hardcoded group/role:
{"kind": "gate", "name": "training_approval", "type": "manager",
 "approver": {"source": "group", "group": "training_managers"}}

// 2) Read the UC approver_group tag off the request's assets (owner fallback):
{"kind": "gate", "name": "await_approval", "type": "data_owner",
 "approver": {"source": "approver_group_tag",
              "assets_from": {"$var": "assets"}, "fallback_to_owner": true}}
```

The resolved approver is surfaced to the approval layer (an `@`-bearing value is
treated as a person; otherwise a group/role) and a pending approval task is
created automatically so it shows on the Approvals page. Use this for most new
workflows; reach for `approvers_from` + a resolve step only for bespoke logic.

### Runtime-resolved approvers (`approvers_from`)

A gate's approver(s) don't have to be static. Set `approvers_from` to an
expression of context, and its value is surfaced to the approval layer (as both
`data_owners` and `approvers`). Pair it with a prior **resolve step** that
discovers the approvers and writes them into context (see `writes_context`
below). This is how the data-access workflow routes to whichever owner group
owns the requested assets — no code:

```json
{"kind": "step", "name": "resolve_owners", "tool": "resolve_data_owners",
 "writes_context": ["data_owners"],
 "args": {"assets": {"$var": "assets"}}},
{"kind": "gate", "name": "await_approval", "type": "data_owner",
 "approvers_from": {"$var": "data_owners"}}
```

## Passing values between stages (`writes_context`)

A step normally records its tool output under `results`. To make a value
available to **later** stages (a gate's `approvers_from`, another step's `args`,
a `run_if`), add `writes_context`: a list of keys to lift from the step's tool
result into the shared context. Only applies to single (non-`for_each`) steps.
The `resolve_data_owners` tool returns `{"data_owners": [...]}`, so
`"writes_context": ["data_owners"]` puts that list in context for the gate.

## Expression mini-language

Dynamic values (`args`, `auto_approve`, `approvers_from`, `for_each`, `item_args`) use a small JSON
expression language (no code). A one-key object whose key starts with `$` is an
operation; anything else is a literal.

- `{"$var": "scope"}` — context field (dotted paths ok); `{"$var": {"path": "a.b", "default": "x"}}` for a default.
- `{"$item": "child_type"}` — the current `for_each` item (only inside `for_each`/`item_args`).
- `{"$ctx": true}` — the whole context. `{"$literal": <any>}` — value as-is.
- `{"$eq": [a, b]}`, `{"$ne": [a, b]}`, `{"$in": [a, b]}` (a in b)
- `{"$contains": [a, b]}` — `b in a` (a contains b); inverse of `$in`. Use for
  list/substring membership, e.g. `{"$contains": [{"$var": "tags"}, "pii"]}`.
  There is no `$contains_any`/regex — for "any of" use `$or` of `$contains`.
- `{"$and": [..]}`, `{"$or": [..]}`, `{"$not": a}`, `{"$bool": a}`
- `{"$coalesce": [a, b]}` — first truthy (like `a or b`).
- `{"$concat": [a, b, ..]}` — string-join the evaluated parts (None renders as
  ""); use this to build a notification `body`/`subject` from literals + vars,
  e.g. `{"$concat": ["New training request: ", {"$var": "topic"}]}`.
- `{"$obj": {"k": expr}}`, `{"$list": [expr, ..]}`

## Fan-out (one step per item)

```json
{"kind": "step", "name": "grant_each", "tool": "grant_uc_access",
 "for_each": {"$var": "assets"},
 "item_args": {"asset": {"$item": "asset_name"}, "level": {"$var": "access_level"}}}
```

## Conditional branching (`run_if`)

A step can be made **conditional**: give it a `run_if` expression that returns a
boolean. When it evaluates false for a request, the step is **skipped** — its
tool never runs, no `success_fact` is written, and the workflow continues to the
next stage. Omit `run_if` to always run.

```json
{"kind": "step", "name": "notify_security", "tool": "send_notification",
 "run_if": {"$eq": [{"$var": "tier"}, "high"]},
 "args": {"subject": "High-tier request", "body": {"$var": "justification"}}}
```

`preview_workflow_spec` shows each conditional step's `decision` (`run` or
`skip`) for your sample context, and skipped steps render as a dashed/greyed node
in the live graph. For divergent multi-step paths, pair `run_if` with
`spawn_child_request` to route into a dedicated child workflow.

## Steps wire to real tools

Call `list_workflow_building_blocks` to see every available step tool with its
`side_effect_class`, whether it mutates, and — importantly — its real argument
names (`args`) and which are `required_args`. `validate_workflow_spec` rejects a
step whose `tool` isn't a real tool.

Request types are **data-driven**: set a workflow's **`request_type`** (any string)
and publish it — that act registers the type, so requests of that type are accepted
with no enum entry, no `specs.py` edit, and no redeploy. Two workflows should not
share a `request_type`. If a workflow has a `training` gate, requests of its type
are automatically flagged as requiring training.

### Use the exact arg names

Each tool accepts specific argument names (e.g. `send_notification` takes
`to_email`, `subject`, `body` — *not* `to`; `spawn_child_request` takes
`child_type` and `parameters` — *not* `request_type`/`payload`). Because tools
have a `**kwargs` catch-all, a wrong name is **silently dropped at runtime** and
the value never reaches the tool. To prevent this, `validate_workflow_spec` and
`preview_workflow_spec` return a `warnings` list flagging any arg that doesn't
match the tool's parameters and any required arg you left unset. Treat warnings
as errors: fix every one (check `list_workflow_building_blocks` → the tool's
`args`) before saving or publishing.

## The safe authoring loop

1. `list_workflow_building_blocks` — learn the tools/gates/operators.
2. Draft the `graph_spec` (start from `get_workflow` on a similar one).
3. `validate_workflow_spec` — fix any structural/expression errors.
4. `preview_workflow_spec` with a realistic `sample_context` — confirm which
   gates fire and the exact args each step receives. **No tools run.**
5. `save_workflow_draft` — saves as a draft; it does **not** affect live requests.
6. Only after the admin explicitly confirms: `publish_workflow` — runs the full
   pre-publish gate and makes it live (a version snapshot is kept for rollback).

## Finicky tools & house rules (edit me)

This section is for admins to extend with environment-specific guidance. Examples:

- **Terraform**: put `terraform_plan` *before* the `platform_admin` gate and
  `create_*`/apply *after*, so a human reviews the plan.
- **Notifications**: a trailing `send_notification` step gives requesters closure;
  use `success_fact` only on the real provisioning step, not the notification.
- **Identity groups**: group/owner names come from configured UC tag keys — prefer
  `{"$var": "access_group"}` over hard-coded names.

> Add your own tool-specific gotchas, naming conventions, and required gates here.
"""


def _find_domain(db: Session, name: str):
    from app.services.context_catalog_service import ContextCatalogService

    for d in ContextCatalogService.list_domains(db):
        if d.name == name:
            return d
    return None


def seed_authoring_guide(db: Session) -> Optional[str]:
    """Ensure the authoring guide domain + document exist (idempotent).

    Returns the document id (created or existing), or ``None`` on failure.
    Never raises — seeding must not break startup.
    """
    from app.services.context_catalog_service import ContextCatalogService

    try:
        domain = _find_domain(db, _DOMAIN_NAME)
        if domain is None:
            domain = ContextCatalogService.create_domain(
                db,
                name=_DOMAIN_NAME,
                description=_DOMAIN_DESCRIPTION,
                domain_type="system",
                categories=["administration", "workflow-authoring"],
                created_by="system",
            )
            logger.info("Seeded Context Catalog domain '%s'", _DOMAIN_NAME)

        rev_tag = f"{_REVISION_TAG_PREFIX}{GUIDE_REVISION}"
        existing = next(
            (d for d in ContextCatalogService.list_documents(db, domain.id)
             if d.title == _DOC_TITLE),
            None,
        )
        if existing is None:
            doc = ContextCatalogService.create_document(
                db,
                domain_id=domain.id,
                title=_DOC_TITLE,
                body_markdown=GUIDE_MARKDOWN,
                status="published",
                tags=["workflow-authoring", "workflows", "admin", rev_tag],
                created_by="system",
            )
            logger.info("Seeded Context Catalog document '%s'", _DOC_TITLE)
            return doc.id

        # Refresh only if our canonical revision is newer than what's stored, so
        # we never clobber admin edits made on the current revision.
        stored_revs = [t for t in (existing.tags or []) if t.startswith(_REVISION_TAG_PREFIX)]
        stored_rev = max(
            (int(t[len(_REVISION_TAG_PREFIX):]) for t in stored_revs if t[len(_REVISION_TAG_PREFIX):].isdigit()),
            default=0,
        )
        if stored_rev < GUIDE_REVISION:
            other_tags = [t for t in (existing.tags or []) if not t.startswith(_REVISION_TAG_PREFIX)]
            ContextCatalogService.update_document(
                db, existing.id, body_markdown=GUIDE_MARKDOWN, tags=other_tags + [rev_tag],
            )
            logger.info("Updated Context Catalog guide '%s' to revision %d", _DOC_TITLE, GUIDE_REVISION)
        return existing.id
    except Exception as e:  # noqa: BLE001 - seeding must never break startup
        logger.warning("Authoring guide seeding skipped: %s", e)
        return None
