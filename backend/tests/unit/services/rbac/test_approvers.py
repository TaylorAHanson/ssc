"""Approver groups, view / edit rights and the group lookup (ADR 0005 D6, D8, D13).

Group membership is mocked through ``User.entitlements`` (the email plus the
Databricks group display names ``current_user.me()`` returns at sign-in); the
Databricks lookup is mocked at the SDK boundary.
"""
import asyncio
import types

import pytest

from app.core.config import settings
from app.models.user import User
from app.services.rbac import approvers

UC_GROUP = "UC Approvers"
MS_GROUP = "Metastore Approvers"
WS_GROUP = "WS Approvers"


def _user(email="someone@example.com", roles=(), groups=()):
    return User(id=email, email=email, full_name=email, roles=list(roles),
                entitlements=[email, *groups], is_active=True)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_UC_APPROVER_GROUP", f"  {UC_GROUP}  ")
    monkeypatch.setattr(settings, "RBAC_METASTORE_APPROVER_GROUP", MS_GROUP)
    monkeypatch.setattr(settings, "RBAC_WS_APPROVER_GROUP", WS_GROUP)
    monkeypatch.setattr(settings, "RBAC_VIEWER_ROLES", ["Governance Admin"])
    monkeypatch.setattr(settings, "RBAC_ALLOW_RESET", False)
    approvers._group_cache.clear()
    yield
    approvers._group_cache.clear()


class _FakeGroups:
    def __init__(self, existing, fail=()):
        self.existing = set(existing)
        self.fail = set(fail)
        self.calls = []

    def list(self, filter=None, attributes=None):
        self.calls.append((filter, attributes))
        for name in self.fail:
            if f'"{name}"' in filter:
                raise RuntimeError("SCIM unavailable")
        return iter([types.SimpleNamespace(id="1", display_name=n)
                     for n in self.existing if filter == f'displayName eq "{n}"'])


@pytest.fixture
def fake_sdk(monkeypatch):
    """Replace ``databricks.sdk.WorkspaceClient`` with one whose groups API is ``_FakeGroups``."""
    state = {"groups": _FakeGroups(existing=[])}

    class _Client:
        def __init__(self, *a, **kw):
            self.groups = state["groups"]

    monkeypatch.setattr("databricks.sdk.WorkspaceClient", _Client)
    return state


# --- approver_group / is_member ----------------------------------------------


def test_approver_group_reads_each_planes_setting_stripped():
    assert approvers.approver_group("uc") == UC_GROUP
    assert approvers.approver_group("uc_metastore") == MS_GROUP
    assert approvers.approver_group("workspace") == WS_GROUP


def test_approver_group_is_none_when_blank_or_unknown_plane(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_WS_APPROVER_GROUP", "   ")
    assert approvers.approver_group("workspace") is None
    assert approvers.approver_group("nope") is None


def test_setting_changes_apply_without_restart(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_UC_APPROVER_GROUP", "New UC Group")
    assert approvers.approver_group("uc") == "New UC Group"


@pytest.mark.parametrize("plane, group, label", [
    ("uc", UC_GROUP, f"UC securables approvers: {UC_GROUP}"),
    ("uc_metastore", MS_GROUP, f"UC metastore approvers: {MS_GROUP}"),
    ("workspace", None, "Platform Admins (no Workspace securables group set)"),
    (None, "grp_x", "Access rule approvers: grp_x"),
    (None, None, "Platform Admins"),
])
def test_approval_label_names_the_types_group_or_the_platform_admin_fallback(plane, group, label):
    assert approvers.approval_label(plane, group) == label


def test_is_member_is_case_insensitive_and_trimmed():
    user = _user(groups=["uc approvers "])
    assert approvers.is_member(user, UC_GROUP) is True
    assert approvers.is_member(user, " UC APPROVERS") is True
    assert approvers.is_member(user, WS_GROUP) is False


def test_is_member_of_no_group_is_false():
    user = _user(groups=[""])
    assert approvers.is_member(user, None) is False
    assert approvers.is_member(user, "") is False


def test_an_app_role_with_the_groups_name_is_not_membership():
    """Membership comes from Databricks groups (entitlements), not app roles."""
    user = _user(roles=[UC_GROUP])
    assert approvers.is_member(user, UC_GROUP) is False


# --- can_edit / editable_planes / can_view -----------------------------------


def test_platform_admin_edits_every_plane():
    admin = _user(roles=["Platform Admin"])
    assert approvers.editable_planes(admin) == ["uc", "uc_metastore", "workspace"]
    assert approvers.can_view(admin) is True


def test_group_member_edits_only_their_plane_but_views_all():
    member = _user(groups=[WS_GROUP])
    assert approvers.can_edit(member, "workspace") is True
    assert approvers.can_edit(member, "uc") is False
    assert approvers.editable_planes(member) == ["workspace"]
    assert approvers.can_view(member) is True


def test_editable_planes_follow_planes_order():
    member = _user(groups=[WS_GROUP, UC_GROUP])
    assert approvers.editable_planes(member) == ["uc", "workspace"]


def test_viewer_role_views_but_edits_nothing():
    viewer = _user(roles=["governance_admin"])
    assert approvers.can_view(viewer) is True
    assert approvers.editable_planes(viewer) == []


def test_plain_user_can_neither_view_nor_edit():
    user = _user(groups=["Some Other Group"])
    assert approvers.can_view(user) is False
    assert approvers.editable_planes(user) == []


def test_unset_group_falls_back_to_platform_admin(monkeypatch):
    monkeypatch.setattr(settings, "RBAC_METASTORE_APPROVER_GROUP", "")
    member = _user(groups=[UC_GROUP])
    assert approvers.can_edit(member, "uc_metastore") is False
    assert approvers.can_edit(_user(roles=["Platform Admin"]), "uc_metastore") is True


def test_require_view_and_edit_raise_plain_messages():
    with pytest.raises(approvers.RbacForbidden):
        approvers.require_view(_user())
    approvers.require_view(_user(groups=[UC_GROUP]))

    member = _user(groups=[UC_GROUP])
    approvers.require_edit(member, "uc")
    with pytest.raises(approvers.RbacForbidden) as exc_info:
        approvers.require_edit(member, "workspace")
    assert WS_GROUP in str(exc_info.value)
    assert "Workspace securables" in str(exc_info.value)


# --- group_exists --------------------------------------------------------------


def test_group_exists_true_false_and_cached(fake_sdk):
    fake_sdk["groups"] = _FakeGroups(existing=[UC_GROUP])

    assert asyncio.run(approvers.group_exists(UC_GROUP)) is True
    assert asyncio.run(approvers.group_exists("Missing")) is False
    calls = len(fake_sdk["groups"].calls)
    assert asyncio.run(approvers.group_exists(UC_GROUP)) is True
    assert asyncio.run(approvers.group_exists("Missing")) is False
    assert len(fake_sdk["groups"].calls) == calls
    assert fake_sdk["groups"].calls[0] == (f'displayName eq "{UC_GROUP}"', "id,displayName")


def test_group_exists_cache_expires(fake_sdk, monkeypatch):
    fake_sdk["groups"] = _FakeGroups(existing=[UC_GROUP])
    clock = {"now": 1000.0}
    monkeypatch.setattr(approvers.time, "monotonic", lambda: clock["now"])

    asyncio.run(approvers.group_exists(UC_GROUP))
    clock["now"] += approvers.GROUP_CACHE_SECONDS + 1
    asyncio.run(approvers.group_exists(UC_GROUP))
    assert len(fake_sdk["groups"].calls) == 2


def test_group_exists_escapes_double_quotes(fake_sdk):
    name = 'odd "quoted" group'
    asyncio.run(approvers.group_exists(name))
    assert fake_sdk["groups"].calls[0][0] == 'displayName eq "odd \\"quoted\\" group"'


def test_group_exists_is_none_on_error_and_not_cached(fake_sdk):
    fake_sdk["groups"] = _FakeGroups(existing=[], fail=[UC_GROUP])
    assert asyncio.run(approvers.group_exists(UC_GROUP)) is None
    assert asyncio.run(approvers.group_exists(UC_GROUP)) is None
    assert len(fake_sdk["groups"].calls) == 2


# --- permissions ------------------------------------------------------------------


def test_permissions_report_each_plane_and_group_status(fake_sdk, monkeypatch):
    monkeypatch.setattr(settings, "RBAC_METASTORE_APPROVER_GROUP", "")
    monkeypatch.setattr(settings, "RBAC_WS_APPROVER_GROUP", "Typo Group")
    fake_sdk["groups"] = _FakeGroups(existing=[UC_GROUP])

    perms = asyncio.run(approvers.permissions(_user(groups=[UC_GROUP])))

    by_plane = {p.plane: p for p in perms.planes}
    assert [p.plane for p in perms.planes] == ["uc", "uc_metastore", "workspace"]
    assert by_plane["uc"].label == "UC securables"
    assert (by_plane["uc"].approver_group, by_plane["uc"].group_status, by_plane["uc"].can_edit) == (
        UC_GROUP, "found", True)
    assert (by_plane["uc_metastore"].approver_group, by_plane["uc_metastore"].group_status) == (None, "unset")
    assert (by_plane["workspace"].group_status, by_plane["workspace"].can_edit) == ("not_found", False)
    assert perms.can_view is True
    assert perms.can_export is True
    assert perms.can_import is False
    assert perms.can_reset is False


def test_permissions_unknown_when_lookup_fails(fake_sdk):
    fake_sdk["groups"] = _FakeGroups(existing=[], fail=[MS_GROUP])
    perms = asyncio.run(approvers.permissions(_user(roles=["Platform Admin"])))
    assert {p.plane: p.group_status for p in perms.planes}["uc_metastore"] == "unknown"


def test_platform_admin_imports_and_resets_only_when_allowed(fake_sdk, monkeypatch):
    admin = _user(roles=["Platform Admin"])
    perms = asyncio.run(approvers.permissions(admin))
    assert (perms.can_import, perms.can_reset, perms.can_export) == (True, False, True)

    monkeypatch.setattr(settings, "RBAC_ALLOW_RESET", True)
    approvers._group_cache.clear()
    assert asyncio.run(approvers.permissions(admin)).can_reset is True


def test_viewer_cannot_export(fake_sdk):
    perms = asyncio.run(approvers.permissions(_user(roles=["Governance Admin"])))
    assert (perms.can_view, perms.can_export, perms.can_import) == (True, False, False)
    assert not any(p.can_edit for p in perms.planes)
