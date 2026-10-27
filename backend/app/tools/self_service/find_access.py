"""
Tool to look up who holds what in the Access Rules ledger (ADR 0005 D9).
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.approval_scope import parse_csv
from app.tools.mcp import tool


_SPLIT = " Split a dotted name like 'catalog.schema.table' across catalog, schema_name and object_name."


class FindAccessInput(BaseModel):
    securable_type: Optional[str] = Field(
        None, description="Workbook spelling, e.g. 'catalog', 'schema', 'table', 'workspace', 'folder'.")
    catalog: Optional[str] = Field(None, description="Catalog name." + _SPLIT)
    schema_name: Optional[str] = Field(None, description="Schema name (inside the catalog).")
    object_name: Optional[str] = Field(None, description="Table, view, volume, folder or other object name.")
    workspace_name: Optional[str] = Field(None, description="Workspace name, for workspace securables.")
    principal_name: Optional[str] = Field(
        None, description="A group or service principal name (partial match) to list what it holds.")
    mine: bool = Field(
        False,
        description=(
            "True to list everything held by the groups the signed-in user belongs to (for 'what do I have "
            "access to?'). Leave it off when you name a securable: those results already flag which rules "
            "are yours (held_by_you)."),
    )
    environment: Optional[str] = Field(None, description="Environment key or alias, e.g. 'dev', 'prd'.")
    limit: int = Field(default=25, ge=1, le=100, description="Max matches to return.")


_YOURS_NOTE = (
    "held_by_you is true when the rule's group is one you're in. To answer 'do I have access?', say which of "
    "your groups hold what on the securable, and whether it is direct or inherited from the catalog or schema. "
    "If none of your groups appear, say the access rules don't give you this, NOT that you're denied: grants "
    "made outside the access rules (ownership, manual grants, broad groups) aren't listed here. When none of "
    "your groups hold it and run_sql is available, settle it now rather than asking: 'SELECT 1 FROM "
    "catalog.schema.table LIMIT 1', run as the user, shows whether they can really read it. Then offer to "
    "request access if they can't."
)

_NO_GROUPS_NOTE = (
    "The user's groups weren't available on this call, so you can't say which rules are theirs. Don't guess; "
    "you can say who holds access, and when run_sql is available settle 'do I have access?' with 'SELECT 1 "
    "FROM catalog.schema.table LIMIT 1', run as the user."
)

_NOTE = (
    "via='parent' means the access comes from a catalog or schema rule above the securable. "
    "Approved changes reach Databricks at the next scheduled update, so mention timing only if the user asks "
    "whether access is live right now. Call these 'the access rules'; don't say 'desired state' or 'ledger'."
)

_TRY_SUGGESTIONS = (
    "Nothing matched as asked, but `suggestions` lists securables with that name at another level or in "
    "another environment (a name given as a catalog may be a schema or table). If one clearly fits, look it "
    "up now without asking and tell the user which one you used; if several fit, ask which they meant. "
    "A suggestion that is the securable asked about has rules, just none for the group or environment given."
)

_NOTHING_FOUND = (
    "No access rule mentions this name as a catalog, schema, table or workspace in any environment, so no group "
    "gets access to it through the access rules. Say so plainly and ask for the full name (catalog.schema.table) "
    "or another spelling."
)


@tool(
    name="find_access",
    description=(
        "Look up the access rules: who holds which privileges on a securable (including rules on its parent "
        "catalog or schema), or everything a group or service principal holds. Use it before "
        "preview_access_change to tell the user when a group already has what they're asking for. If you're "
        "unsure whether a name is a catalog, schema or table, pass your best guess: when nothing matches, the "
        "result suggests securables with that name at any level. Each rule is flagged held_by_you when it is "
        "held by a group the signed-in user is in, so this also answers 'do I have access to X?'; pass mine=true "
        "alone for 'what do I have access to?'."
    ),
    args_schema=FindAccessInput,
    feature_flag="rbac",
    friendly_label="Looking up access rules",
    friendly_completion_label="Looked up access rules",
)
def find_access(
    securable_type: Optional[str] = None,
    catalog: Optional[str] = None,
    schema_name: Optional[str] = None,
    object_name: Optional[str] = None,
    workspace_name: Optional[str] = None,
    principal_name: Optional[str] = None,
    mine: bool = False,
    environment: Optional[str] = None,
    limit: int = 25,
    **kwargs,
) -> Dict[str, Any]:
    from app.db.session import get_lakebase_session
    from app.services.rbac import ledger

    email = (kwargs.get("_user_email") or "").strip().lower()
    # The signed-in user's own email leads their group list; only the rest are groups.
    my_groups: List[str] = [
        g for g in parse_csv(kwargs.get("_user_entitlements")) if g.strip().lower() != email
    ]
    mine_set = {g.lower() for g in my_groups}
    about_a_securable = any((securable_type, catalog, schema_name, object_name, workspace_name))

    if mine and not my_groups:
        return {"ok": True, "matches": [], "count": 0, "truncated": False, "suggestions": [],
                "note": _NO_GROUPS_NOTE}

    cap = max(int(limit or 1), 1)
    db = get_lakebase_session()
    try:
        result = ledger.find_access(
            db,
            securable_type=securable_type,
            catalog=catalog,
            schema_name=schema_name,
            object_name=object_name,
            workspace_name=workspace_name,
            principal_name=principal_name,
            principals_in=my_groups if mine else None,
            environment=environment,
            # Read wide when the user's groups are known, so a rule of theirs isn't cut off
            # by the limit before it can be flagged and put first.
            limit=100 if mine_set else cap,
        )
    except ledger.LedgerError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        db.close()

    if mine_set:
        for m in result.matches:
            m.held_by_you = m.principal_name.strip().lower() in mine_set
        # Stable: the user's own rules first, each group's order otherwise kept.
        result.matches.sort(key=lambda m: not m.held_by_you)
        result.truncated = result.truncated or len(result.matches) > cap
        result.matches = result.matches[:cap]

    note = _NOTE
    if mine or about_a_securable:
        note += " " + (_YOURS_NOTE if mine_set else _NO_GROUPS_NOTE)
    out: Dict[str, Any] = {**result.model_dump(mode="json"), "count": len(result.matches), "note": note}
    if result.suggestions:
        out["next_step"] = _TRY_SUGGESTIONS
    elif not result.matches and about_a_securable:
        out["next_step"] = _NOTHING_FOUND
    return out
