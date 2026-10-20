"""Approver-facing metadata change report and routing buckets (Workstream A)."""
import re

import pytest

from app.services.metadata import report
from app.services.metadata.types import AuthzDecision, ChangeItem, ChangePreview, Finding, ItemDecision


def item(key="tier", after="gold", *, name="sales_dev.bronze.orders", column=None, action="set", before=None,
         before_exists=False, field="tag", environment="dev", object_state="exists", copied_from=None):
    fqn = name.lower() + (f"::{column.lower()}" if column else "")
    return ChangeItem(fqn_key=fqn, name=name, column=column, securable_type="column" if column else "table",
                      environment=environment, field=field, key=key if field == "tag" else None, action=action,
                      before=before, before_exists=before_exists, after=after, object_state=object_state,
                      copied_from=copied_from)


def decision(it=None, *, allowed=True, needs_approval=False, can_request=True, via="owner", reasons=(),
             sensitive=(), groups=(), findings=()):
    return ItemDecision(
        item=it or item(),
        decision=AuthzDecision(allowed=allowed, needs_approval=needs_approval, can_request=can_request, via=via,
                               reasons=list(reasons), sensitive_reasons=list(sensitive),
                               approver_groups=list(groups)),
        findings=list(findings),
    )


def preview_of(decisions, **kwargs):
    buckets = [report.bucket(d) for d in decisions]
    fields = dict(
        items=decisions,
        direct_count=buckets.count(report.DIRECT),
        approval_count=buckets.count(report.APPROVAL),
        denied_count=buckets.count(report.DENIED),
        needs_approval=report.APPROVAL in buckets,
        object_count=len({d.item.fqn_key for d in decisions}),
        environments=sorted({d.item.environment for d in decisions if d.item.environment}),
    )
    fields.update(kwargs)
    return ChangePreview(**fields)


def table_rows(markdown):
    return [line for line in markdown.splitlines() if line.startswith("| ") and not line.startswith("| Object")]


def cells(row):
    """Split a GFM table row the way marked does: a backslash escapes the next character."""
    out, current, i = [], "", 0
    while i < len(row):
        if row[i] == "\\" and i + 1 < len(row):
            current += row[i:i + 2]
            i += 2
            continue
        if row[i] == "|":
            out.append(current)
            current = ""
        else:
            current += row[i]
        i += 1
    out.append(current)
    return [c.strip() for c in out[1:-1]]


# --- bucket ----------------------------------------------------------------------


@pytest.mark.parametrize("kwargs, findings, expected", [
    (dict(allowed=True), (), report.DIRECT),
    (dict(allowed=True, needs_approval=True), (), report.APPROVAL),
    (dict(allowed=False, can_request=True), (), report.APPROVAL),
    (dict(allowed=False, can_request=False), (), report.DENIED),
    (dict(allowed=True, needs_approval=True, can_request=False), (), report.DENIED),
    (dict(allowed=True), (Finding(code="x", severity="deny", message="no"),), report.DENIED),
    (dict(allowed=True), (Finding(code="x", severity="warn", message="hmm"),), report.DIRECT),
])
def test_bucket(kwargs, findings, expected):
    assert report.bucket(decision(findings=findings, **kwargs)) == expected


# --- build_report ----------------------------------------------------------------


def test_report_summarises_the_change():
    pv = preview_of(
        [
            decision(item("tier", "gold")),
            decision(item("pii", None), needs_approval=True, sensitive=["pii is sensitive."], groups=["data_owners"]),
            decision(item("owner", "x"), allowed=False, can_request=False, via="none", reasons=["Not your object."]),
        ],
        noop_count=2,
        approver_groups=["data_owners"],
        risk={"band": "medium", "score": 42},
    )
    md = report.build_report(pv, reason="Tag the orders table", title="Metadata change")
    lines = md.splitlines()

    assert lines[0] == "**Metadata change**"
    assert "- 1 object: 1 direct, 1 need approval, 1 denied, 2 already true." in lines
    assert "- Environments: `dev`" in lines
    assert "- Reason: Tag the orders table" in lines
    assert "- Risk: medium (42/100)" in lines
    assert "- Approvers: members of `data_owners`" in lines
    assert "| Object | Change | Before → after | Decision | Reasons |" in lines

    rows = [cells(r) for r in table_rows(md)]
    assert rows[0] == ["`sales_dev.bronze.orders` (`dev`)", "tag `tier`", "(none) → `gold`", "Direct (owner)", ""]
    assert rows[1][2:] == ["(none) → (key only)", "Needs approval (owner)", "pii is sensitive."]
    assert rows[2][3:] == ["Denied", "Not your object."]
    assert md.endswith("\n")


def test_report_without_a_reason_items_or_approval():
    md = report.build_report(ChangePreview(noop_count=1))
    assert "- Reason: none given" in md
    assert "| Object" not in md and "Approvers" not in md and "Risk" not in md


def test_report_names_missing_approver_groups_and_blocks():
    pv = preview_of([decision(item(), needs_approval=True)],
                    blocked=True,
                    findings=[Finding(code="reason_too_short", severity="deny", message="Give a reason."),
                              Finding(code="unmanaged_object", severity="warn", message="Adopt it first."),
                              Finding(code="fyi", severity="info", message="Just so you know.")])
    md = report.build_report(pv)
    assert "- **Blocked by policy**" in md
    assert "- Approvers: no approver group is set for these objects." in md
    assert "**Findings**\n- Blocked: Give a reason.\n- Warning: Adopt it first.\n- Note: Just so you know." in md


def test_report_labels_each_kind_of_change():
    pv = preview_of([
        decision(item("tier", action="unset", before="gold", before_exists=True, after=None)),
        decision(item("pii", None, before=None, before_exists=True)),
        decision(item(field="comment", action="comment_clear", before="Old.", before_exists=True, after=None)),
        decision(item(field="comment", action="comment_set", after="New.", column="Email", object_state="pending",
                      environment="prd", copied_from="dev")),
    ])
    rows = [cells(r) for r in table_rows(report.build_report(pv))]
    assert rows[0][2] == "`gold` → removed"
    assert rows[1][2] == "(key only) → (key only)"
    assert rows[2][1:3] == ["description", "`Old.` → (cleared)"]
    assert rows[3][0] == "`sales_dev.bronze.orders.Email` (`prd`, copied from `dev`, not created yet)"


def test_report_caps_rows_and_findings():
    decisions = [decision(item(f"k{i}", "v")) for i in range(report.MAX_ROWS + 3)]
    findings = [Finding(code="f", severity="warn", message=f"Finding {i}.") for i in range(report.MAX_FINDINGS + 2)]
    md = report.build_report(preview_of(decisions, findings=findings))

    assert len(table_rows(md)) == report.MAX_ROWS
    # GFM needs a blank line after a table, or the next line becomes a row.
    assert f"| k{report.MAX_ROWS - 1} " not in md
    assert "\n\n- …and 3 more changes.\n" in md
    assert md.count("- Warning: Finding") == report.MAX_FINDINGS
    assert "- …and 2 more findings." in md


HOSTILE = [
    "a|b",
    "x\\|y",
    "two\\\\|pipes",
    "`tick`|`tock`",
    "<script>alert(1)</script>",
    "[click](https://evil.example/x)",
    "line1\nline2 | line3",
]


@pytest.mark.parametrize("value", HOSTILE)
def test_untrusted_values_cannot_break_the_table(value):
    pv = preview_of([decision(
        item(key=value, after=value, before=value, before_exists=True, column=value.replace("\n", " ")),
        reasons=[value, f"Owner is `{value}` here"], sensitive=[value], needs_approval=True,
        findings=[Finding(code="f", severity="info", message=value)],
    )])
    md = report.build_report(pv, reason=value, title=value)

    rows = table_rows(md)
    assert len(rows) == 1
    assert len(cells(rows[0])) == 5
    assert "https://" not in md
    # Markdown renders nothing inside a code span; everywhere else HTML must arrive escaped.
    outside_code = re.sub(r"`[^`\n]*`", "", md)
    assert re.search(r"(?<!\\)[<\[]", outside_code) is None


def test_untrusted_text_outside_code_spans_is_escaped():
    md = report.build_report(preview_of([decision(item())]), reason="<b>[x](https://e.example)</b> # hi",
                             title="**Hi** <i>")
    assert md.splitlines()[0] == "**\\*\\*Hi\\*\\* \\<i\\>**"
    assert "- Reason: \\<b\\>\\[x\\]\\(hxxps://e.example\\)\\</b\\> \\# hi" in md
