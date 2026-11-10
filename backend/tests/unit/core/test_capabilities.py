"""The role/capability registry: who holds what, aliasing and the Super Admin pass."""
import pytest

from app.core import capabilities as caps
from app.core.capabilities import (
    APPLICATION_ADMIN,
    GOVERNANCE_ADMIN,
    OPS_ADMIN,
    ROLE_LABELS,
    SECURITY_ADMIN,
    SUPER_ADMIN,
    USER,
    all_capabilities,
    canonical_role,
    capabilities_for,
    get_capability,
    has_capability,
    has_role,
    persona_for,
    role_key,
)
from app.models.user import User


def test_every_capability_names_known_roles_and_groups():
    for cap in all_capabilities():
        assert cap.group in caps.GROUP_ORDER, cap.id
        assert SUPER_ADMIN in cap.roles, f"{cap.id} should list Super Admin first"
        for role in cap.roles:
            assert role in ROLE_LABELS, (cap.id, role)
        assert USER not in cap.roles, cap.id


def test_capability_ids_are_unique():
    ids = [c.id for c in all_capabilities()]
    assert len(ids) == len(set(ids))


def test_super_admin_holds_every_capability():
    assert capabilities_for([SUPER_ADMIN]) == [c.id for c in all_capabilities()]


@pytest.mark.parametrize("capability_id", [
    "governance.control_tower", "allowlist.manage", "access_rules.admin", "roles.manage",
    "approvals.break_glass", "governance.tools",
])
def test_application_admin_lacks_governance_and_super_only_capabilities(capability_id):
    assert not has_capability([APPLICATION_ADMIN], capability_id)


@pytest.mark.parametrize("capability_id", [
    "settings.manage", "admin.hub", "roles.view", "tools.registry.manage", "workflows.author",
    "approvals.application", "requests.edit_parameters", "requests.view_all", "ops.health",
])
def test_application_admin_runs_the_app(capability_id):
    assert has_capability([APPLICATION_ADMIN], capability_id)


@pytest.mark.parametrize("capability_id", [
    "requests.view_all", "delegations.view_all", "ops.sentinel_runs", "ops.health",
    "sentinel.view", "platform.probe",
])
def test_ops_admin_gets_operations(capability_id):
    assert has_capability([OPS_ADMIN], capability_id)


@pytest.mark.parametrize("capability_id", [
    "settings.manage", "governance.control_tower", "workflows.author", "approvals.application",
])
def test_ops_admin_does_not_run_the_app_or_governance(capability_id):
    assert not has_capability([OPS_ADMIN], capability_id)


def test_governance_admin_grid_row():
    held = set(capabilities_for([GOVERNANCE_ADMIN]))
    assert {"governance.control_tower", "allowlist.manage", "access_rules.admin",
            "workflows.author", "tools.registry.view", "sentinel.view"} <= held
    assert not held & {"settings.manage", "tools.registry.manage", "roles.view",
                       "requests.view_all", "approvals.application"}


def test_security_admins_hold_only_their_approval_steps():
    assert capabilities_for([SECURITY_ADMIN]) == ["approvals.security"]


def test_the_finance_admin_role_is_retired():
    assert "Finance Admin" not in ROLE_LABELS
    # A stored "Finance Admin" is just an unknown custom role now and holds nothing.
    assert capabilities_for(["Finance Admin"]) == []


@pytest.mark.parametrize("capability_id", ["finance.tools", "security.tools", "approvals.finance"])
def test_retired_chat_tool_capabilities_are_gone(capability_id):
    with pytest.raises(ValueError):
        get_capability(capability_id)


def test_plain_user_holds_nothing():
    assert capabilities_for([USER]) == []
    assert capabilities_for([]) == []


def test_unknown_capability_raises():
    with pytest.raises(ValueError):
        has_capability([SUPER_ADMIN], "no.such.capability")


@pytest.mark.parametrize("name", ["Platform Admin", "platform_admin", "PLATFORM ADMIN",
                                  "application_admin", " Application  Admin "])
def test_old_and_snake_case_names_fold_to_application_admin(name):
    assert canonical_role(name) == APPLICATION_ADMIN
    assert role_key(name) == "application_admin"


def test_legacy_platform_admin_holds_application_capabilities_only():
    assert has_capability(["Platform Admin"], "settings.manage")
    assert not has_capability(["Platform Admin"], "governance.control_tower")
    assert not has_capability(["Platform Admin"], "roles.manage")


def test_has_role_super_passes_and_aliases_resolve():
    assert has_role([SUPER_ADMIN], GOVERNANCE_ADMIN)
    assert has_role([SUPER_ADMIN], "anything custom")
    assert has_role(["platform_admin"], APPLICATION_ADMIN)
    assert has_role([APPLICATION_ADMIN], "Platform Admin")
    assert not has_role([APPLICATION_ADMIN], GOVERNANCE_ADMIN)
    assert not has_role([GOVERNANCE_ADMIN], SUPER_ADMIN)


def test_custom_role_is_kept_as_given():
    assert canonical_role("Data Steward") == "Data Steward"
    assert has_role(["data steward"], "Data Steward")


def test_persona_priority():
    assert persona_for([USER, OPS_ADMIN, SUPER_ADMIN]) == SUPER_ADMIN
    assert persona_for([GOVERNANCE_ADMIN, "platform_admin"]) == APPLICATION_ADMIN
    assert persona_for(["Finance Admin", SECURITY_ADMIN]) == SECURITY_ADMIN
    assert persona_for([OPS_ADMIN]) == OPS_ADMIN
    assert persona_for([]) == USER


def test_setting_list_extends_a_capability(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "RBAC_VIEWER_ROLES", ["Security Admin", "platform_admin"], raising=False)
    assert has_capability([SECURITY_ADMIN], "access_rules.view")
    assert has_capability([APPLICATION_ADMIN], "access_rules.view")
    assert not has_capability([SECURITY_ADMIN], "access_rules.admin")
    assert caps.roles_for("access_rules.view") == [GOVERNANCE_ADMIN, SECURITY_ADMIN, APPLICATION_ADMIN]
    assert caps.roles_for("access_rules.view", include_setting=False) == [GOVERNANCE_ADMIN]


def test_user_model_can():
    user = User(id="u", email="u@example.com", full_name="U", roles=[OPS_ADMIN])
    assert user.can("requests.view_all")
    assert not user.can("settings.manage")
    assert "ops.health" in user.capability_ids()


def test_denied_detail_names_holders():
    msg = caps.denied_detail("allowlist.manage")
    assert "Manage the allowlist" in msg
    assert "Super Admin" in msg and "Governance Admin" in msg
