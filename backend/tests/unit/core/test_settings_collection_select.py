"""Select-typed columns in collection settings (e.g. Target Workspaces' type)."""
import pytest

from app.core import settings_store


def _target_workspaces_field():
    return next(f for f in settings_store.EDITABLE_FIELDS if f["key"] == "collection:target_workspaces")


def _row(**extra):
    return {"name": "ws", "host": "https://a", "environment": "prod", **extra}


def test_select_column_accepts_listed_option():
    rows = settings_store._coerce_collection(_target_workspaces_field(), [_row(type="enterprise")])
    assert rows[0]["type"] == "enterprise"


def test_select_column_blank_is_dropped():
    rows = settings_store._coerce_collection(_target_workspaces_field(), [_row(type="")])
    assert "type" not in rows[0]


def test_select_column_rejects_unlisted_value():
    with pytest.raises(ValueError, match="Workspace type"):
        settings_store._coerce_collection(_target_workspaces_field(), [_row(type="enterprize")])
