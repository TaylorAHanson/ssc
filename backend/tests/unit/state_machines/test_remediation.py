"""Unit tests for severity-based enforcement gating."""

import pytest

from app.state_machines.enforcement_sentinel.remediation import (
    normalize_severity,
    resolve_enforcement_step,
)


@pytest.mark.parametrize(
    "mode,severity,action,expected",
    [
        ("audit_only", "CRITICAL", "KILL", "audit_skipped"),
        ("active_enforcement", "CRITICAL", "SKIPPED_ALLOWLIST", "skip"),
        ("active_enforcement", "CRITICAL", "KILL", "kill"),
        ("active_enforcement", "HIGH", "KILL", "kill"),
        ("active_enforcement", "MEDIUM", "KILL", "warn"),
        ("active_enforcement", "LOW", "KILL", "warn"),
        ("active_enforcement", "HIGH", "WARN", "warn"),
        ("active_enforcement", "MEDIUM", "PAUSE", "warn"),
        ("active_enforcement", "CRITICAL", "DROP", "warn"),
    ],
)
def test_resolve_enforcement_step(mode, severity, action, expected):
    assert resolve_enforcement_step(mode, severity, action) == expected


def test_normalize_severity_defaults():
    assert normalize_severity(None) == "HIGH"
    assert normalize_severity("critical") == "CRITICAL"
