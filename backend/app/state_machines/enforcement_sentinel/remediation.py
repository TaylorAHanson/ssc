"""
Severity-based gating for Enforcement Sentinel remediation.

OPA policies emit `severity` (NONE, LOW, MEDIUM, HIGH, CRITICAL) alongside `action`.
The state machine uses this to decide whether to run destructive handlers or fall back to owner warnings.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

NON_REMEDIATION_ACTIONS = frozenset({"SKIPPED_ALLOWLIST", "PENDING_EXCEPTION", "ALLOW"})

# High-impact automated changes; MEDIUM tier blocks these (warn instead).
DESTRUCTIVE_ACTIONS = frozenset(
    {
        "KILL",
        "DROP",
        "SUSPEND",
        "REVOKE_ADMIN",
        "ARCHIVE",
        "ARCHIVE_FLAG",
        "STOP_AND_RECONFIGURE",
    }
)


def normalize_severity(raw: Any) -> str:
    """Normalize OPA severity to a known tier; missing values default to HIGH (fail-safe)."""
    if raw is None or raw == "":
        return "HIGH"
    s = str(raw).strip().upper()
    if s in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE"}:
        return s
    logger.warning("Unknown severity %r from policy; treating as HIGH", raw)
    return "HIGH"


def determine_intended_step(severity_raw: Any, action: str) -> str:
    """Determine what action should be taken based on severity and action string, ignoring mode."""
    severity = normalize_severity(severity_raw)
    if action in NON_REMEDIATION_ACTIONS:
        return "skip"
    if action == "WARN":
        return "warn"
    if action == "CERTIFY":
        return "certify"
    if action == "UNCERTIFY":
        return "uncertify"
    if severity in {"NONE", "LOW"}:
        return "warn"
    if severity == "MEDIUM" and action in DESTRUCTIVE_ACTIONS:
        return "warn"
    if action == "KILL" and severity in {"HIGH", "CRITICAL"}:
        return "kill"
    # HIGH/CRITICAL non-KILL actions (PAUSE, DROP, …): no typed handler yet — notify owner
    if severity in {"HIGH", "CRITICAL"}:
        return "warn"
    if severity == "MEDIUM":
        return "warn"
    return "skip"


def resolve_enforcement_step(mode: str, severity_raw: Any, action: str) -> str:
    """
    Decide what the enforcement phase should do for one violation.

    Returns:
        - ``skip`` — no handler calls (allowlist skip, etc.)
        - ``audit_skipped`` — would have acted, but skipped due to audit mode
        - ``start_certification`` — create a DATA_CERTIFICATION request via AI auto-generation
        - ``warn`` — call ``handler.warn`` with policy context
        - ``kill`` — call ``handler.kill`` (only valid when action is KILL)
        - ``certify`` — call ``handler.certify``
        - ``uncertify`` — call ``handler.uncertify``
    """
    intended = determine_intended_step(severity_raw, action)
    if mode != "active_enforcement" and intended != "skip":
        return "audit_skipped"
    return intended


def warn_prefix(severity: str, action: str) -> str:
    """Build a short prefix for demoted or unmapped remediation warnings."""
    return f"[{normalize_severity(severity)}/{action}]"
