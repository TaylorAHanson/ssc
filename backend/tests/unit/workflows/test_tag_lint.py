"""Tests for Tag Lint checks (whitespace, non-ascii, case collisions, typos)."""

from app.workflows.tag_lint import run_lint_checks
from app.workflows.tag_plan import ObjectState, TagVocabulary, build_tag_plan
from app.workflows.tag_policy import TagPolicy


def test_lint_catches_whitespace_and_non_ascii():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={},
        )
    }
    desired = [
        {
            "table": "main.sales.orders",
            "desired_tags": {
                " key_with_space ": " value_with_space ",
                "unicode_key": "val\u2014dash",
            },
        }
    ]
    plan = build_tag_plan(desired, live_state)
    policy = TagPolicy.parse("key_mode: open\nknown_keys: {}")

    findings = run_lint_checks(plan, vocabulary=TagVocabulary(), policy=policy)
    codes = [f.code for f in findings]
    assert "WHITESPACE" in codes
    assert "NON_ASCII" in codes


def test_lint_catches_case_collisions():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={},
        )
    }
    desired = [
        {
            "table": "main.sales.orders",
            "desired_tags": {
                "env": "Dev",
            },
        }
    ]
    plan = build_tag_plan(desired, live_state)
    policy = TagPolicy.parse("key_mode: open\nknown_keys: {}")
    # Catalog has lowercase 'dev'
    vocabulary = TagVocabulary(
        values={"env": {"dev": 5, "prod": 10}},
        available=True,
    )

    findings = run_lint_checks(plan, vocabulary=vocabulary, policy=policy)
    codes = [f.code for f in findings]
    assert "CASE_COLLISION" in codes


def test_lint_catches_near_miss_value():
    live_state = {
        "main.sales.orders": ObjectState(
            display="main.sales.orders",
            object_type="TABLE",
            exists=True,
            tags={},
        )
    }
    desired = [
        {
            "table": "main.sales.orders",
            "desired_tags": {
                "tier": "goldd",
            },
        }
    ]
    plan = build_tag_plan(desired, live_state)
    policy = TagPolicy.parse("key_mode: open\nknown_keys: {}")
    vocabulary = TagVocabulary(
        values={"tier": {"gold": 12, "silver": 5, "bronze": 3}},
        available=True,
    )

    findings = run_lint_checks(plan, vocabulary=vocabulary, policy=policy)
    codes = [f.code for f in findings]
    assert "NEAR_MISS_VALUE" in codes
    near_miss = next(f for f in findings if f.code == "NEAR_MISS_VALUE")
    assert any(s.value == "gold" for s in near_miss.suggestions)
