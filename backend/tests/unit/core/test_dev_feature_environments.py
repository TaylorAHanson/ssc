"""Tests for the dev-feature environment allowlist (app.core.config).

This gate controls the "Dev Persona Mode" role override, which lets a caller
hand itself any role via the ``X-Dev-Role-Override`` header. It has leaked into
a production deploy before, which is why it is an allowlist of dev-flavored
tokens rather than a denylist of prod ones. These tests pin both halves of that:
the abbreviations we intend to support, and the prod spellings that must never
match no matter how they are cased or spaced.
"""
import pytest
from unittest.mock import patch

from app.core.config import DEV_FEATURE_ENVIRONMENTS, dev_features_allowed, settings


@pytest.mark.parametrize(
    "value", ["development", "dev", "local", "test", "testing", "tst"]
)
def test_dev_flavored_environments_enable_dev_features(value):
    with patch.object(settings, "ENVIRONMENT", value):
        assert dev_features_allowed() is True


@pytest.mark.parametrize("value", ["  TST  ", "Dev", "TEST", "Local"])
def test_matching_ignores_case_and_surrounding_whitespace(value):
    with patch.object(settings, "ENVIRONMENT", value):
        assert dev_features_allowed() is True


@pytest.mark.parametrize(
    "value",
    [
        "prod", "prd", "production", "PROD", "Production",
        "stage", "staging", "stg",
        "", "   ", None,
        # Near-misses that must not match: the allowlist is exact, not a prefix
        # or substring check, so a prod env named e.g. "dev-prod-mirror" stays out.
        "devprod", "dev-prod-mirror", "testprod", "prod-test", "tstprod",
    ],
)
def test_non_dev_environments_never_enable_dev_features(value):
    with patch.object(settings, "ENVIRONMENT", value):
        assert dev_features_allowed() is False


def test_allowlist_contains_no_production_tokens():
    """A guard for future edits to the set itself."""
    forbidden = {"prod", "prd", "production", "stage", "staging", "stg"}
    assert not (DEV_FEATURE_ENVIRONMENTS & forbidden)
