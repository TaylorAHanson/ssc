"""Scan-report arithmetic.

The scan details modal shows a violation total, HIGH/MEDIUM/LOW cards, and a
list of findings with per-policy tab counts. All of them count the same unit —
one finding per failing (resource, policy) check — so they agree with each
other. These tests pin that.
"""

from app.workflows.sentinel import aggregate_check_counts


def _check(policy, severity, outcomes, action="WARN"):
    """A (resource, policy) evaluation whose rules pass/fail per ``outcomes``."""
    return {
        "policy": policy,
        "severity": severity,
        "action": action,
        "result": "PASS" if all(outcomes) else "VIOLATION",
        "rule_results": [
            {"id": f"{policy}_rule_{i}", "passed": ok} for i, ok in enumerate(outcomes)
        ],
    }


def test_a_check_failing_several_rules_is_one_finding():
    """The violation list shows one row per failing (resource, policy), so the
    headline must not count that row's failed rules separately."""
    checks = [
        _check("data_certification", "HIGH", [False] * 5 + [True]),
        _check("jobs", "MEDIUM", [False, True]),
        _check("dashboards", "LOW", [False]),
    ]

    counts = aggregate_check_counts(checks)

    assert counts["violation_count"] == 3
    assert counts["severity_counts"] == {"HIGH": 1, "MEDIUM": 1, "LOW": 1}
    assert sum(counts["severity_counts"].values()) == counts["violation_count"]


def test_violation_count_matches_the_number_of_open_finding_rows():
    checks = [
        _check("data_certification", "HIGH", [False, False, False]),
        _check("data_certification", "HIGH", [False, True]),
        _check("dashboards", "HIGH", [False]),
        _check("dashboards", "NONE", [True]),
    ]
    open_rows = [c for c in checks if c["result"] == "VIOLATION"]

    assert aggregate_check_counts(checks)["violation_count"] == len(open_rows)


def test_pass_violation_and_exempt_account_for_every_check():
    checks = [
        _check("compute", "HIGH", [False, True, True]),
        _check("apps", "NONE", [False, False], action="SKIPPED_ALLOWLIST"),
        _check("jobs", "NONE", [True, True]),
    ]

    counts = aggregate_check_counts(checks)

    assert counts["total_checks"] == 3
    assert counts["pass_count"] == 1
    assert counts["violation_count"] == 1
    assert counts["exempt_count"] == 1
    assert (
        counts["pass_count"] + counts["violation_count"] + counts["exempt_count"]
        == counts["total_checks"]
    )


def test_approved_exceptions_are_excluded_from_the_total_and_the_cards():
    """An approved allowlist exception is signed-off risk. Counting it in the
    total but giving it severity NONE would leave the cards unable to add up."""
    checks = [
        _check("apps", "NONE", [False, False, False], action="SKIPPED_ALLOWLIST"),
    ]

    counts = aggregate_check_counts(checks)

    assert counts["violation_count"] == 0
    assert counts["exempt_count"] == 1
    assert counts["severity_counts"] == {}


def test_pending_exceptions_still_count_as_open_findings():
    # Only *approved* exceptions are exempt; a pending one is unreviewed risk.
    checks = [_check("apps", "MEDIUM", [False], action="PENDING_EXCEPTION")]

    counts = aggregate_check_counts(checks)

    assert counts["violation_count"] == 1
    assert counts["exempt_count"] == 0
    assert counts["severity_counts"] == {"MEDIUM": 1}


def test_policies_without_per_rule_results_count_the_same_way():
    checks = [
        {"policy": "legacy", "severity": "HIGH", "action": "WARN", "result": "VIOLATION"},
        {"policy": "legacy", "severity": "NONE", "action": "ALLOW", "result": "PASS"},
    ]

    counts = aggregate_check_counts(checks)

    assert counts["total_checks"] == 2
    assert counts["pass_count"] == 1
    assert counts["violation_count"] == 1
    assert counts["severity_counts"] == {"HIGH": 1}


def test_legacy_critical_severity_folds_into_high():
    # CRITICAL was collapsed into HIGH; a stray value must not create a fourth
    # bucket that the UI never renders, or the cards stop summing to the total.
    checks = [_check("legacy", "CRITICAL", [False, False])]

    counts = aggregate_check_counts(checks)

    assert counts["severity_counts"] == {"HIGH": 1}
    assert sum(counts["severity_counts"].values()) == counts["violation_count"]


def test_no_checks_produces_zeroes():
    assert aggregate_check_counts([]) == {
        "total_checks": 0,
        "pass_count": 0,
        "violation_count": 0,
        "exempt_count": 0,
        "severity_counts": {},
    }
