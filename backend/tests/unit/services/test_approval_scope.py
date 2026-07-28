"""The shared approval-visibility rules.

This filter was written out three times before it was extracted, and the copies
had drifted on role spelling — one normalized case and underscores, the other
didn't. These tests pin the normalization so a role spelled ``platform_admin``
resolves identically everywhere.
"""
from app.services.approval_scope import (
    allowed_approval_types,
    approval_visibility_filter,
    is_platform_admin,
    normalize_role,
    parse_csv,
)


def test_role_spellings_normalize_to_the_same_role():
    for spelling in ("Platform Admin", "platform_admin", "PLATFORM ADMIN", " Platform admin "):
        assert normalize_role(spelling) == "platform admin"
        assert is_platform_admin([spelling]) is True


def test_platform_admin_sees_every_approval_type():
    types = allowed_approval_types(["Platform Admin"])
    assert set(types) == {
        "platform_admin", "manager", "data_owner", "security",
        "security_admin", "finance_admin", "governance_admin",
    }


def test_scoped_roles_see_only_their_own_types():
    assert allowed_approval_types(["Security Admin"]) == ["security", "security_admin"]
    assert allowed_approval_types(["Finance Admin"]) == ["finance_admin"]
    assert allowed_approval_types(["Governance Admin"]) == ["governance_admin"]


def test_unknown_and_empty_roles_grant_nothing():
    assert allowed_approval_types(["Intern"]) == []
    assert allowed_approval_types([]) == []
    assert allowed_approval_types(None) == []


def test_multiple_roles_union_without_duplicates():
    types = allowed_approval_types(["Security Admin", "Governance Admin", "Security Admin"])
    assert types == ["security", "security_admin", "governance_admin"]


def test_parse_csv_handles_the_injected_kwarg_shapes():
    """Tools receive these flattened to a comma-separated string."""
    assert parse_csv("Platform Admin, Finance Admin") == ["Platform Admin", "Finance Admin"]
    assert parse_csv("") == []
    assert parse_csv(None) == []
    assert parse_csv(" , ,") == []


def test_filter_covers_assignment_delegation_group_and_type(db_session):
    """All four visibility paths must be OR'd, not just direct assignment."""
    from app.db import ApprovalModel

    clause = str(approval_visibility_filter(
        "me@example.com", roles=["Finance Admin"], entitlements=["my-group"]
    ))

    assert "approvals.assigned_to_email" in clause
    assert "approvals.delegated_to_email" in clause
    assert "approvals.assigned_to_role" in clause
    assert "approvals.approval_type" in clause
    # A query using it must actually execute against the schema.
    assert db_session.query(ApprovalModel).filter(
        approval_visibility_filter("me@example.com", roles=["Finance Admin"], entitlements=["g"])
    ).all() == []


def test_filter_with_no_roles_still_matches_the_user_directly(db_session):
    """A plain user with no roles must still see approvals assigned to them."""
    import uuid
    from app.db import ApprovalModel

    mine = ApprovalModel(
        id=str(uuid.uuid4()),
        request_id="req-1",
        approval_type="manager",
        status="pending",
        assigned_to_email="me@example.com",
    )
    theirs = ApprovalModel(
        id=str(uuid.uuid4()),
        request_id="req-2",
        approval_type="manager",
        status="pending",
        assigned_to_email="someone@example.com",
    )
    db_session.add_all([mine, theirs])
    db_session.commit()

    visible = db_session.query(ApprovalModel).filter(
        approval_visibility_filter("me@example.com", roles=[], entitlements=[])
    ).all()

    assert [a.id for a in visible] == [mine.id]
