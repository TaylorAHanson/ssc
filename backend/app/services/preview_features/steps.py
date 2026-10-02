"""The logic behind the ``preview_feature_request`` workflow's steps.

The tools in ``app/workflows/tools/preview_features.py`` are thin wrappers
around these. Each opens its own short-lived DB session.

Everything quoted from Databricks (names, descriptions, announcements, error
text) is untrusted and goes through ``safe_text`` / ``safe_code``.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.db.preview_feature import ACCOUNT_TARGET, PreviewFeatureModel, PreviewFeatureTargetModel
from app.services.app_code_review.report import safe_code, safe_text
from app.services.preview_features import settings_api
from app.services.preview_features.status import (
    mark_passed, observed_effective, record_verification, rows_for_request, target_label, wanted_value,
)

logger = logging.getLogger(__name__)

_PHASE_LABELS = {"BETA": "Beta", "PUBLIC_PREVIEW": "Public Preview", "PRIVATE_PREVIEW": "Private Preview"}
_DOCS_URL_RE = re.compile(r"^https://docs\.databricks\.com/[A-Za-z0-9/_\-.#%]*$")
AUTOMATION_ACTOR = "service principal"


def _session():
    from app.db.session import get_lakebase_session

    return get_lakebase_session()


def _doc_link(label: str, url: Optional[str]) -> str:
    """A markdown link only for well-formed docs.databricks.com URLs."""
    if url and _DOCS_URL_RE.match(url):
        return f"[{label}]({url})"
    return ""


def _value_label(row: PreviewFeatureTargetModel) -> str:
    obs = row.observed_value or {}
    eff = obs.get("effective")
    if "workspaces_listed" in obs:
        return f"on in {obs.get('workspaces_on', 0)} of {obs.get('workspaces_listed', 0)} workspaces"
    if eff is None:
        return "unknown"
    return ("on" if eff else "off") + (" (set here)" if obs.get("set_here") else " (inherited)")


def _load(db, request_id: str) -> Tuple[Optional[PreviewFeatureModel], List[PreviewFeatureTargetModel]]:
    rows = rows_for_request(db, request_id)
    if not rows:
        return None, []
    return db.get(PreviewFeatureModel, rows[0].feature_id), rows


def _latest_approval(db, request_id: str, approval_type: Optional[str] = None,
                     exclude_type: Optional[str] = None) -> Optional[str]:
    from app.db import EventModel

    facts = (
        db.query(EventModel)
        .filter(EventModel.request_id == request_id, EventModel.event_type == "approval_received")
        .order_by(EventModel.created_at.desc())
        .all()
    )
    for f in facts:
        data = f.event_data or {}
        kind = data.get("approval_type")
        if approval_type and kind != approval_type:
            continue
        if exclude_type and kind == exclude_type:
            continue
        return data.get("approved_by") or data.get("actor")
    return None


# ---------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------
def assess(
    request_id: str,
    *,
    feature_ref: Optional[str] = None,
    action: Optional[str] = None,
    targets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Summarize the request for approvers, linking its targets first if needed.

    A request opened from the tab is already linked. One the chat agent started
    with ``execute_workflow`` carries only the workflow's inputs, so this links it
    (same checks as the tab). An unknown feature or ineligible target fails the
    request with the reason.
    """
    from app.services.preview_features.request_flow import PreviewRequestError, link_request

    db = _session()
    try:
        feature, rows = _load(db, request_id)
        if feature is None:
            if not feature_ref:
                return {"ok": False, "error": "The request doesn't say which preview feature it's for."}
            try:
                link_request(db, request_id, feature_ref=feature_ref, action=action, targets=targets)
            except PreviewRequestError as e:
                db.rollback()
                return {"ok": False, "error": str(e)}
            feature, rows = _load(db, request_id)
        action = rows[0].action or "enable"
        verb = "turn on" if action == "enable" else "turn off"
        lines = [
            f"**{safe_text(feature.display_name, 200)}** {safe_code(feature.setting_name) if feature.setting_name else ''}",
            "",
            f"- Request: **{verb}**",
            f"- Phase: {_PHASE_LABELS.get(feature.phase or '', feature.phase or 'unknown')}",
            f"- Scope: {'account (done in the account console)' if feature.scope == 'account' else 'workspace'}",
        ]
        links = " · ".join(x for x in (
            _doc_link("Docs", feature.docs_link), _doc_link("Release note", feature.announcement_url)) if x)
        if links:
            lines.append(f"- Links: {links}")
        if feature.description:
            lines += ["", safe_text(feature.description, 800)]
        if feature.announcement_text and feature.announcement_text != feature.description:
            lines += ["", f"_Announcement:_ {safe_text(feature.announcement_text, 800)}"]
        lines += ["", "| Target | Listed | Current value |", "|---|---|---|"]
        for r in sorted(rows, key=lambda r: r.target):
            listed = "yes" if (r.available or r.target == ACCOUNT_TARGET) else "**no**"
            lines.append(f"| {safe_text(target_label(r.target), 120)} | {listed} | {_value_label(r)} |")
        notes = []
        if feature.scope != "account" and feature.value_type == "boolean":
            notes.append("After approval the workspace service principal changes the setting itself; "
                         "any workspace where that fails goes to a manual task.")
        elif feature.scope != "account":
            notes.append("This setting isn't a simple on/off switch, so a person makes the change.")
        if feature.scope == "account":
            notes.append("Account previews are changed by an account admin in the account console, "
                         "so approval creates a manual task.")
        if action == "disable":
            notes.append("Turning a preview off leaves it explicitly off on each workspace: the API can't "
                         "reset it to inherited, and an explicit off may override an account-level on.")
        if notes:
            lines += [""] + [f"- {n}" for n in notes]
        return {
            "summary": f"{verb.capitalize()} {feature.display_name} for {len(rows)} target(s).",
            "targets": [r.target for r in rows],
            "scope": "account" if feature.scope == "account" else "workspace",
            "report_title": "Preview feature request",
            "report_markdown": "\n".join(lines),
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def set_status(request_id: str, status: str) -> Dict[str, Any]:
    db = _session()
    try:
        rows = rows_for_request(db, request_id)
        approver = _latest_approval(db, request_id, exclude_type="manual_task") if status == "approved" else None
        changed = 0
        for r in rows:
            if r.status in ("implemented", "not_requested") and status in ("approved", "rejected"):
                continue  # already settled (e.g. closed because the feature went GA)
            r.status = status
            if approver:
                r.approved_by = approver
            if status == "rejected":
                r.note = "Request rejected"
            changed += 1
        db.commit()
        return {"updated": changed, "status_set": status}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# apply (the automatic change)
# ---------------------------------------------------------------------------
def _apply_one(feature: PreviewFeatureModel, target: str, wanted: bool) -> Dict[str, Any]:
    from app.core.workspaces import get_workspace_config

    ws = get_workspace_config(target)
    if ws is None:
        return {"target": target, "outcome": "manual", "reason": "workspace isn't configured"}
    try:
        client = settings_api.build_client(ws)
        current = settings_api.get_value(client, feature.setting_name)
        if current.effective is wanted:
            return {"target": target, "outcome": "unchanged", "value": current.to_dict()}
        new = settings_api.set_boolean(client, feature.setting_name, wanted)
        return {"target": target, "outcome": "changed", "value": new.to_dict()}
    except settings_api.SettingsApiError as e:
        return {"target": target, "outcome": "manual", "reason": str(e)[:300]}
    except Exception as e:  # noqa: BLE001 - one workspace's failure goes to the manual task
        logger.warning("Preview apply on '%s' failed: %s", target, e, exc_info=True)
        return {"target": target, "outcome": "manual", "reason": str(e)[:300]}


async def apply(request_id: str) -> Dict[str, Any]:
    db = _session()
    try:
        feature, rows = _load(db, request_id)
        if feature is None:
            return {"needs_manual": True, "applied_targets": [], "manual_targets": []}
        work = [(r.target, wanted_value(r)) for r in rows if r.status == "approved" and r.target != ACCOUNT_TARGET]
        automatable = bool(feature.setting_name) and feature.value_type == "boolean"
    finally:
        db.close()

    if automatable:
        results = await asyncio.gather(*(
            asyncio.to_thread(_apply_one, feature, target, wanted) for target, wanted in work
        ))
    else:
        results = [{"target": t, "outcome": "manual", "reason": "not an on/off setting"} for t, _ in work]

    db = _session()
    try:
        now = datetime.utcnow()
        by_target = {r.target: r for r in rows_for_request(db, request_id)}
        for res in results:
            row = by_target.get(res["target"])
            if row is None:
                continue
            if res.get("value"):
                row.observed_value = res["value"]
                row.observed_at = now
                row.observe_error = None
            if res["outcome"] == "changed":
                row.implemented_by = AUTOMATION_ACTOR
                row.note = "Changed by the workspace service principal"
            elif res["outcome"] == "manual":
                row.note = f"Needs a manual change: {res.get('reason', '')}"[:500]
        db.commit()
    finally:
        db.close()

    manual = [r for r in results if r["outcome"] == "manual"]
    applied = [r["target"] for r in results if r["outcome"] in ("changed", "unchanged")]
    verb = "on" if (work and work[0][1]) else "off"
    lines = ["| Workspace | Result |", "|---|---|"]
    for r in results:
        if r["outcome"] == "changed":
            outcome = f"turned {verb}"
        elif r["outcome"] == "unchanged":
            outcome = f"already {verb}"
        else:
            outcome = f"**manual change needed**: {safe_text(r.get('reason'), 240)}"
        lines.append(f"| {safe_text(r['target'], 120)} | {outcome} |")
    if manual:
        lines += ["", "**What's left to do:** in each workspace marked *manual change needed*, a workspace "
                  f"admin opens **Settings → Previews** and turns **{safe_text(feature.display_name, 200)}** "
                  f"{verb}. Then mark the Implement task done."]
    return {
        "needs_manual": bool(manual),
        "applied_targets": applied,
        "manual_targets": [{"target": r["target"], "reason": r.get("reason")} for r in manual],
        "report_title": "Automatic change",
        "report_markdown": "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------
def _read_live(feature: PreviewFeatureModel, target: str) -> Tuple[Optional[settings_api.ObservedValue], Optional[str]]:
    from app.core.workspaces import get_workspace_config

    ws = get_workspace_config(target)
    if ws is None:
        return None, "workspace isn't configured"
    try:
        return settings_api.get_value(settings_api.build_client(ws), feature.setting_name), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:300]


def _run_probe(feature: PreviewFeatureModel, workspaces: List[str]) -> Optional[bool]:
    """A read-only REST GET whose success means the feature is on. None = no probe."""
    probe = feature.probe or {}
    path = str(probe.get("path") or "").strip()
    if probe.get("type", "rest") != "rest" or not path.startswith("/api/"):
        return None
    from app.core.workspaces import get_target_workspaces, get_workspace_config

    names = workspaces or [w.name for w in get_target_workspaces()][:1]
    for name in names:
        ws = get_workspace_config(name)
        if ws is None:
            continue
        try:
            settings_api.build_client(ws).api_client.do("GET", path)
            return True
        except Exception as e:  # noqa: BLE001
            logger.info("Probe %s on '%s' failed: %s", path, name, e)
    return False


async def verify(request_id: str) -> Dict[str, Any]:
    db = _session()
    try:
        feature, rows = _load(db, request_id)
        if feature is None:
            return {"verified": [], "pending": []}
        listed = [
            t.target for t in db.query(PreviewFeatureTargetModel)
            .filter(PreviewFeatureTargetModel.feature_id == feature.id,
                    PreviewFeatureTargetModel.target != ACCOUNT_TARGET,
                    PreviewFeatureTargetModel.available.is_(True)).all()
        ]
        work = [r.target for r in rows if r.status == "approved"]
    finally:
        db.close()

    live: Dict[str, Tuple[Optional[settings_api.ObservedValue], Optional[str]]] = {}
    if feature.setting_name:
        names = set(listed if ACCOUNT_TARGET in work else []) | {t for t in work if t != ACCOUNT_TARGET}
        reads = await asyncio.gather(*(asyncio.to_thread(_read_live, feature, n) for n in sorted(names)))
        live = dict(zip(sorted(names), reads))
    probe_result = await asyncio.to_thread(_run_probe, feature, [t for t in work if t != ACCOUNT_TARGET] or listed)

    db = _session()
    try:
        now = datetime.utcnow()
        completer = _latest_approval(db, request_id, approval_type="manual_task")
        verified, pending = [], []
        for row in rows_for_request(db, request_id):
            if row.status != "approved":
                continue
            if row.target == ACCOUNT_TARGET:
                values = [v for v, _ in (live.get(n, (None, None)) for n in listed) if v is not None]
                if values:
                    on = sum(1 for v in values if v.effective)
                    row.observed_value = {
                        "effective": (on == len(values)) if on in (0, len(values)) else None,
                        "workspaces_on": on, "workspaces_listed": len(values),
                    }
                    row.observed_at = now
            else:
                value, err = live.get(row.target, (None, None))
                if value is not None:
                    row.observed_value = value.to_dict()
                    row.observed_at = now
                    row.observe_error = None
                elif err:
                    row.observe_error = err
            actor = row.implemented_by or completer
            if record_verification(db, feature, row, actor=actor, now=now):
                verified.append((row.target, "api"))
                continue
            if observed_effective(row) is None and probe_result is not None and wanted_value(row):
                if probe_result:
                    mark_passed(row, "probe", actor, now)
                    verified.append((row.target, "probe"))
                    continue
            if observed_effective(row) is None and completer:
                # Nothing we can read says otherwise: the person who did the work vouches for it.
                mark_passed(row, "attested", completer, now)
                verified.append((row.target, "attested"))
                continue
            pending.append(row.target)
        db.commit()
    finally:
        db.close()

    labels = {"api": "verified (setting value)", "probe": "verified (probe)", "attested": "attested"}
    lines = ["| Target | Result |", "|---|---|"]
    lines += [f"| {safe_text(target_label(t), 120)} | {labels[m]} |" for t, m in verified]
    lines += [f"| {safe_text(target_label(t), 120)} | waiting: the daily sync checks again |" for t in pending]
    return {
        "verified": [t for t, _ in verified],
        "pending": pending,
        "report_title": "Verification",
        "report_markdown": "\n".join(lines),
    }
