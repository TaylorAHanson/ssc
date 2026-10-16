"""Tests for the approver-facing Access Rules report (ADR 0005 D10)."""

import re

from app.models.rbac import AccessItem, AccessProposal, ChangeSetPreview, Finding, ItemOutcome, RowDiff
from app.services.rbac import report

SHEET = "UC_Securables_RBAC_Dev"


def item(principal="g_new", privileges=("SELECT",), **kw):
    fields = dict(environment="dev", securable_type="schema", catalog="sales_dev", schema_name="orders", plane="uc")
    fields.update(kw)
    return AccessItem(principal_name=principal, privileges=list(privileges), **fields)


def snap(principals="g_analysts", privileges="USE_SCHEMA;SELECT", **kw):
    cells = dict(environment_value="dev", securable_type="schema", catalog="sales_dev", schema_name="orders",
                 object_name="", workspace_name="", owner="sales_owners", principal_type="group",
                 principal_name=principals, privilege=privileges, extra={})
    cells.update(kw)
    return cells


def proposal(**kw):
    requested = item(privileges=["USE_SCHEMA", "SELECT"])
    prereq = item(securable_type="catalog", schema_name="", privileges=["USE_CATALOG"])
    already = item(principal="g_old", privileges=["SELECT"])
    fields = dict(
        plane="uc",
        approver_group="uc-approvers",
        items=[prereq, requested, already],
        added_prerequisites=[prereq],
        outcomes=[
            ItemOutcome(index=0, item=prereq, status="change"),
            ItemOutcome(index=1, item=requested, status="change"),
            ItemOutcome(index=2, item=already, status="noop", reason="already in place"),
        ],
        diffs=[
            RowDiff(op="create", sheet=SHEET, plane="uc", environment="dev", after=snap(
                principals="g_new", privileges="USE_CATALOG", securable_type="catalog", schema_name="")),
            RowDiff(op="update", rule_id="r-1", sheet=SHEET, plane="uc", environment="dev",
                    before=snap(), after=snap(principals="g_analysts;g_new")),
        ],
        findings=[Finding(severity="warn", code="broad_grant", message="SELECT on a whole schema is broad.")],
    )
    fields.update(kw)
    return AccessProposal(**fields)


def test_report_shows_approvers_changes_rows_and_context():
    md = report.render_proposal_report(
        proposal(), siblings=[("req-ws-1", "workspace"), (None, "uc_metastore")],
        justification="Finance dashboard for the quarterly close", requester="ana@example.com",
    )
    assert "**UC securables access change**" in md
    assert "Approvers: members of `uc-approvers`." in md
    assert "Requested by: `ana@example.com`" in md
    assert "Justification: Finance dashboard for the quarterly close" in md
    # The requested grant, with the prerequisite listed separately rather than as a request.
    requested = md.split("**Requested changes**")[1].split("**Prerequisites added**")[0]
    assert "Grant `USE_SCHEMA;SELECT` on schema `sales_dev.orders` to group `g_new` in `dev`" in requested
    assert "USE_CATALOG" not in requested
    assert "Grant `USE_CATALOG` on catalog `sales_dev` to group `g_new`" in md.split("**Prerequisites added**")[1]
    # Compact before / after per row, with the sheet.
    assert "- **New row**: catalog `sales_dev` on sheet `UC_Securables_RBAC_Dev`" in md
    assert "- **Row edited**: schema `sales_dev.orders` on sheet `UC_Securables_RBAC_Dev`" in md
    assert "Principals: + `g_new` (now `g_analysts;g_new`)" in md
    assert "Privileges: `USE_SCHEMA;SELECT` (unchanged)" in md
    assert "group `g_old` in `dev` — already in place" in md
    assert "SELECT on a whole schema is broad." in md.split("**Warnings**")[1]
    linked = md.split("**Linked requests**")[1]
    assert "Workspace securables: request `req-ws-1`" in linked
    assert "UC metastore: filed alongside this one" in linked


def test_report_without_a_group_says_platform_admins_approve():
    md = report.render_proposal_report(proposal(approver_group=None, approver_fallback=True), justification="")
    assert "No approver group set — Platform Admins approve." in md
    assert "Justification: none given" in md
    assert "**Linked requests**" not in md


def test_report_lists_blocked_items_and_loose_denies():
    bad = item(privileges=["MANAGE"])
    md = report.render_proposal_report(proposal(
        items=[bad], added_prerequisites=[], diffs=[], blocked=True,
        outcomes=[ItemOutcome(index=0, item=bad, status="blocked", reason="MANAGE can't be requested.")],
        findings=[
            Finding(severity="deny", code="hard_denied_privilege", message="MANAGE can't be requested.",
                    change_index=0),
            Finding(severity="deny", code="justification_too_short", message="Say why in at least 20 characters."),
        ],
    ))
    blocked = md.split("**Blocked**")[1]
    assert "Grant `MANAGE` on schema `sales_dev.orders` to group `g_new` in `dev` — MANAGE can't be requested." in blocked
    assert "Say why in at least 20 characters." in blocked
    assert blocked.count("MANAGE can't be requested.") == 1


def test_untrusted_text_is_neutralized():
    evil = "[click](https://evil.example) <img src=x> **bold** | pipe"
    hostile = item(principal="g`x](https://evil.example)", privileges=["SELECT"])
    md = report.render_proposal_report(
        proposal(items=[hostile], added_prerequisites=[], diffs=[],
                 outcomes=[ItemOutcome(index=0, item=hostile, status="change")],
                 findings=[Finding(severity="warn", code="w", message=evil)]),
        justification=evil, requester="x@example.com",
    )
    assert "https://evil" not in md
    assert re.search(r"(?<!\\)<img", md) is None
    assert "\\*\\*bold\\*\\*" in md and "\\| pipe" in md
    # A backtick can't close the code span early.
    assert "`g'x](hxxps://evil.example)`" in md


def test_workspace_securables_name_the_workspace():
    ws = item(securable_type="folder", catalog="", schema_name="", object_name="/Shared/finance",
              workspace_name="ws-dev", plane="workspace", privileges=["CAN_READ"])
    line = report.item_line(ws, "workspace")
    assert line == "Grant `CAN_READ` on folder `/Shared/finance` in workspace `ws-dev` to group `g_new` in `dev`"


def test_change_set_report_for_a_two_person_edit():
    preview = ChangeSetPreview(
        diffs=[RowDiff(op="update", rule_id="r-1", sheet=SHEET, plane="uc", environment="dev",
                       before=snap(), after=snap(privileges="USE_SCHEMA;SELECT;MODIFY", owner="new_owners")),
               RowDiff(op="delete", rule_id="r-2", sheet=SHEET, plane="uc", environment="dev",
                       before=snap(principals="g_old"))],
        findings=[Finding(severity="warn", code="w", message="Two open requests touch this row.")],
        blocked=False,
        two_person_required=True,
    )
    md = report.render_change_set_report(preview, plane="uc", approver_group="uc-approvers",
                                         editor="admin@example.com", reason="Quarterly access review")
    assert "**UC securables direct edit (second approval)**" in md
    assert "Edited by: `admin@example.com`. The editor can't approve their own edit." in md
    assert "Reason: Quarterly access review" in md
    assert "Privileges: + `MODIFY` (now `USE_SCHEMA;SELECT;MODIFY`)" in md
    assert "Owner: `sales_owners` → `new_owners`" in md
    assert "- **Row removed**: schema `sales_dev.orders` on sheet `UC_Securables_RBAC_Dev`" in md
    assert "was group `g_old`" in md
    assert "Two open requests touch this row." in md


def test_revert_report_names_the_change_set_both_reasons_and_who_approves():
    preview = ChangeSetPreview(
        diffs=[RowDiff(op="update", rule_id="r-1", sheet=SHEET, plane="uc", environment="dev",
                       before=snap(privileges="USE_SCHEMA;SELECT;MODIFY"), after=snap())],
        findings=[], blocked=False, two_person_required=True,
    )
    md = report.render_revert_report(
        preview, change_set_id="cs-1", original_reason="Granted [MODIFY](https://evil.example)",
        original_actor="editor@example.com", planes=["uc"], approver_group="uc-approvers",
        editor="admin@example.com", reason="Wrong `group` <img src=x>",
    )
    assert "**UC securables revert (second approval)**" in md
    assert "Reverts change set `cs-1`, made by `editor@example.com`." in md
    assert "Approvers: members of `uc-approvers`." in md
    assert "Requested by: `admin@example.com`. The requester can't approve their own revert." in md
    assert "https://evil" not in md and not re.search(r"(?<!\\)<img", md)
    assert "Privileges: − `MODIFY` (now `USE_SCHEMA;SELECT`)" in md

    across = report.render_revert_report(
        preview, change_set_id="cs-2", original_reason=None, original_actor=None, planes=["uc", "workspace"],
        approver_group=None, editor="admin@example.com", reason="",
    )
    assert "**Access Rules revert (second approval)**" in across
    assert "Approvers: Platform Admins, because this revert changes" in across
    assert "Original reason: none recorded" in across and "Reason for reverting: none given" in across


def test_long_change_sets_are_capped(monkeypatch):
    monkeypatch.setattr(report, "MAX_ENTRIES", 2)
    diffs = [RowDiff(op="create", sheet=SHEET, plane="uc", environment="dev", after=snap(principals=f"g_{i}"))
             for i in range(5)]
    md = report.render_proposal_report(proposal(diffs=diffs))
    assert md.count("**New row**") == 2
    assert "…and 3 more rows." in md


def test_summary_counts_and_approvers():
    assert report.proposal_summary(proposal()) == (
        "UC securables: 1 change, 1 prerequisite added, 1 already true. Approvers: members of uc-approvers."
    )
    assert report.proposal_summary(proposal(approver_group=None, blocked=True)).endswith("Blocked by policy.")
