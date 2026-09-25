"""Combine the pre-scan and the reviewer into the result a security admin reads.

The recommendation is the more severe of the deterministic floor and the
reviewer's view, so the model can escalate but never overrule the code. The
same goes for the data-access identity. Confidence starts from the reviewer's
own estimate and is capped by things code can see: the model didn't finish,
it disagrees with the pre-scan, the identity is unclear, or files were left out.

Text from the reviewer and from the repository is untrusted, so the markdown
neutralizes links, images and HTML in it. The only links in the report are
built here, to GitHub at the reviewed commit.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from app.services.app_code_review.models import (
    CONTROL_LABELS,
    IDENTITIES,
    IDENTITY_LABELS,
    RECOMMENDATIONS,
    RISKY_IDENTITIES,
    SEVERITIES,
    Finding,
    ResolvedSource,
    Snapshot,
    more_severe,
)
from app.services.app_code_review.prescan import PreScan
from app.services.app_code_review.reviewer import ReviewerVerdict

# The security team's compliance vocabulary, shown to approvers.
RECOMMENDATION_LABELS = {
    "approve": "Compliant",
    "approve_with_notes": "Compliant with conditions",
    "needs_discussion": "Exception required",
}

_CONTROL_STATUS_LABELS = {"met": "Met", "gap": "Gap", "confirm": "Confirm", "not_applicable": "N/A"}

_SEVERITY_HEADINGS = {"blocker": "Blockers", "concern": "Concerns", "minor": "Minor notes"}


def _floor(findings: List[Finding]) -> str:
    severities = {f.severity for f in findings}
    if "blocker" in severities:
        return "needs_discussion"
    if "concern" in severities:
        return "approve_with_notes"
    return "approve"


def _confidence(
    verdict: Optional[ReviewerVerdict], scan: PreScan, snapshot: Snapshot, identity: str,
) -> Tuple[float, List[str]]:
    factors: List[str] = []
    if verdict is None:
        value = 0.4
        factors.append("The reviewer model didn't finish, so this is the automated scan only.")
    else:
        value = verdict.confidence
        if verdict.forced:
            value = min(value, 0.7)
            factors.append("The reviewer ran out of reading rounds before finishing.")
        if verdict.unread:
            value = min(value, 0.6)
            shown = ", ".join(verdict.unread[:5]) + ("…" if len(verdict.unread) > 5 else "")
            factors.append(
                f"The reviewer didn't read {len(verdict.unread)} of {len(verdict.required)} files that "
                f"bear on the decision ({shown})."
            )
        if (verdict.identity in RISKY_IDENTITIES) != (scan.identity in RISKY_IDENTITIES):
            value = min(value, 0.5)
            factors.append(
                f"The reviewer ({IDENTITY_LABELS.get(verdict.identity, verdict.identity)}) and the "
                f"automated scan ({IDENTITY_LABELS.get(scan.identity, scan.identity)}) disagree on "
                f"whose identity reads data."
            )
    if identity == "unknown":
        value = min(value, 0.6)
        factors.append("Which identity reads data couldn't be determined.")
    if snapshot.budget_exhausted:
        value -= 0.1
        factors.append("Some source files were left out to stay within the review size limit.")
    if not scan.app_config_files:
        value = min(value, 0.7)
        factors.append("No app definition (app.yaml / bundle) was found.")
    return round(max(0.05, min(0.99, value)), 2), factors


def _dedupe(findings: List[Finding]) -> List[Finding]:
    """Drop reviewer findings that restate a pre-scan finding on the same spot.

    The reviewer names categories freely, so "same spot" is file, line and
    severity: a distinct issue on that line is almost always a different severity.
    """
    seen = {(f.file, f.line, f.severity) for f in findings if f.source == "pre-scan" and f.file}
    out = []
    for f in findings:
        if f.source == "reviewer" and (f.file, f.line, f.severity) in seen:
            continue
        out.append(f)
    rank = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    return sorted(out, key=lambda f: (rank.get(f.severity, 9), f.source != "pre-scan"))


# --- markdown ---------------------------------------------------------------

_URL = re.compile(r"(?i)\b(https?)://")
_WWW = re.compile(r"(?i)\bwww\.")


def _defang(s: str) -> str:
    s = _URL.sub(lambda m: m.group(1)[0] + "xx" + m.group(1)[3:] + "://", s)  # https -> hxxps
    return _WWW.sub("www[.]", s)


_CODE_SPAN = re.compile(r"(`[^`\n]+`)")


def safe_text(text: Any, limit: int = 600) -> str:
    """Neutralize untrusted text: no links, images, HTML, or markdown structure.

    `Code spans` are kept (markdown never links or renders HTML inside them);
    everything else is escaped.
    """
    s = _defang(str(text or "")[:limit]).replace("\r", " ").replace("\n", " ")
    parts = []
    for i, part in enumerate(_CODE_SPAN.split(s)):
        if i % 2:
            parts.append(safe_code(part[1:-1]))
        else:
            parts.append(re.sub(r"([\\`*_{}\[\]()<>#!|~])", r"\\\1", part))
    return "".join(parts)


def safe_code(text: Any, limit: int = 240) -> str:
    """Untrusted text as an inline code span (which markdown never turns into links)."""
    s = _defang(str(text or "")).replace("\r", " ").replace("\n", " ").replace("`", "'")[:limit]
    return f"`{s}`" if s.strip() else ""


def _file_link(source: ResolvedSource, snapshot: Snapshot, web_base: str,
               path: Optional[str], line: Optional[int]) -> str:
    if not path:
        return ""
    label = f"{path}:{line}" if line else path
    if path not in snapshot.files:
        return safe_code(label)
    url = f"{web_base.rstrip('/')}/{source.full_name}/blob/{source.sha}/{quote(path)}"
    if line:
        url += f"#L{line}"
    return f"[{safe_code(label)}]({url})"


def render_markdown(result: Dict[str, Any], source: ResolvedSource, snapshot: Snapshot,
                    findings: List[Finding], web_base: str) -> str:
    rec = result["recommendation"]
    pct = int(round(result["confidence"] * 100))
    label = RECOMMENDATION_LABELS[rec] if result["reviewer_completed"] else "Manual review needed"
    lines = [
        f"### {label} · {pct}% confidence",
        "",
        f"**Data access identity:** {IDENTITY_LABELS.get(result['data_access_identity'], 'Unclear')}  ",
        f"**Reviewed:** [{safe_code(source.full_name)}]({source.html_url}) at "
        f"{safe_code(source.ref_label)} ({safe_code(source.sha[:7])})"
        + (f", directory {safe_code(source.subpath)}" if source.subpath else ""),
        "",
    ]
    if result.get("summary"):
        lines += [safe_text(result["summary"], 1500), ""]
    if result.get("approval_path"):
        lines += [f"**Approval path:** {safe_text(result['approval_path'], 800)}", ""]
    if result.get("identity_rationale"):
        lines += [f"**Why this identity:** {safe_text(result['identity_rationale'], 1000)}", ""]
    if result.get("confidence_factors"):
        lines += ["**What lowered confidence:**"]
        lines += [f"- {safe_text(f)}" for f in result["confidence_factors"]]
        lines.append("")

    controls = [c for c in result.get("controls") or [] if c["status"] != "not_applicable"]
    if controls:
        lines += ["#### Controls", "", "| Control | Status | Note |", "|---|---|---|"]
        for c in controls:
            lines.append(
                f"| {CONTROL_LABELS.get(c['control'], c['control'])} | "
                f"{_CONTROL_STATUS_LABELS[c['status']]} | {safe_text(c.get('note'), 300)} |"
            )
        lines.append("")

    leads = result["pre_scan"].get("exception_leads") or []
    if not result["reviewer_completed"] and leads:
        # Without the reviewer, these unverified leads are where to start looking.
        lines += ["#### Automated leads to check", ""]
        for lead in leads[:15]:
            where = _file_link(source, snapshot, web_base, lead["file"], lead["line"])
            lines.append(f"- {safe_text(lead['kind'], 60)} — {where} {safe_code(lead['snippet'])}")
        lines.append("")

    for severity in ("blocker", "concern", "minor"):
        group = [f for f in findings if f.severity == severity]
        if not group:
            continue
        lines.append(f"#### {_SEVERITY_HEADINGS[severity]} ({len(group)})")
        for f in group:
            where = _file_link(source, snapshot, web_base, f.file, f.line)
            tag = " · automated check" if f.source == "pre-scan" else ""
            item = [f"- **{safe_text(f.title, 200)}**{tag}" + (f" — {where}" if where else "")]
            if f.why_it_matters:
                item.append(f"  {safe_text(f.why_it_matters)}")
            if f.evidence:
                item.append(f"  {safe_code(f.evidence)}")
            if f.remediation:
                item.append(f"  *Fix:* {safe_text(f.remediation)}")
            lines.append("  \n".join(item))  # hard breaks: one line per part
        lines.append("")
    if not findings:
        lines += ["No findings.", ""]

    reviewed = result["reviewed"]
    read_note = ""
    if result["reviewer_completed"] and reviewed["required_files"]:
        read = reviewed["required_files"] - len(reviewed["required_unread"])
        read_note = f" The reviewer read {read} of {reviewed['required_files']} files that bear on the decision."
    lines.append(
        f"<sub>{reviewed['files_reviewed']} files in scope, {reviewed['files_skipped']} skipped "
        f"(dependencies, build output, binaries, size limits).{read_note}</sub>"
    )
    return "\n".join(lines)


def build_result(
    source: ResolvedSource,
    snapshot: Snapshot,
    scan: PreScan,
    verdict: Optional[ReviewerVerdict],
    web_base: str = "https://github.com",
) -> Dict[str, Any]:
    findings = _dedupe(scan.findings + (verdict.findings if verdict else []))
    recommendation = _floor(findings)
    identity = scan.identity
    controls = verdict.controls if verdict else []
    if verdict is not None:
        recommendation = more_severe(RECOMMENDATIONS, recommendation, verdict.recommendation)
        if identity in RISKY_IDENTITIES or verdict.identity in RISKY_IDENTITIES:
            identity = more_severe(IDENTITIES, identity, verdict.identity)
        else:
            identity = verdict.identity
        # "Conditions" with nothing above a minor finding anywhere is the model
        # hedging; the rubric says minor findings alone are Compliant. This only
        # ever relaxes that one notch, so a repo gains nothing by provoking it.
        if recommendation == "approve_with_notes" and all(f.severity == "minor" for f in findings):
            recommendation = "approve"
    # An identity blocker (from either side) means the service principal
    # reaches governed data, whatever label the reviewer picked, so the
    # header can't contradict the findings.
    if any(f.severity == "blocker" and f.category == "identity" for f in findings):
        identity = "sp_with_data_access"
        # The pre-scan's "SP alongside the user token" note is now answered.
        findings = [f for f in findings
                    if not (f.source == "pre-scan" and f.category == "identity" and f.severity == "minor")]
    # The one non-negotiable: SP data access always goes to discussion.
    if identity == "sp_with_data_access":
        recommendation = "needs_discussion"
    # Nothing vouched for the code beyond the automated checks (the model was
    # unreachable, or a guardrail refused content in the repo, which a hostile
    # repo can provoke on purpose), so a person has to look.
    if verdict is None:
        recommendation = "needs_discussion"
    confidence, factors = _confidence(verdict, scan, snapshot, identity)

    summary = verdict.summary if verdict else (
        "The reviewer model didn't return a verdict, so this recommendation comes from the "
        "automated checks alone. Review the findings below directly."
    )
    approval_path = verdict.approval_path if verdict else (
        "Manual review: the automated reviewer didn't finish, so the controls weren't assessed."
    )
    result: Dict[str, Any] = {
        "recommendation": recommendation,
        "confidence": confidence,
        "confidence_factors": factors,
        "data_access_identity": identity,
        "identity_rationale": verdict.identity_rationale if verdict else "",
        "summary": summary,
        "approval_path": approval_path,
        "controls": controls,
        "findings": [f.to_dict() for f in findings],
        "pre_scan": scan.to_dict(),
        "reviewer_completed": verdict is not None,
        "reviewed": {
            "repo": source.full_name,
            "sha": source.sha,
            "ref": source.ref_label,
            "subpath": source.subpath,
            "url": source.html_url,
            "files_reviewed": len(snapshot.files),
            "files_skipped": len(snapshot.skipped),
            # Coverage of the model's reading, not just what was downloaded.
            "required_files": len(verdict.required) if verdict else 0,
            "required_unread": (verdict.unread if verdict else [])[:50],
            # Bounded: the executor's audit fact stores the whole result.
            "skipped": [{"path": p, "reason": r} for p, r in snapshot.skipped[:50]],
        },
    }
    result["report_title"] = "App code review"
    result["report_markdown"] = render_markdown(result, source, snapshot, findings, web_base)
    return result
