import pytest
import uuid
from app.db.user import UserModel, RoleModel

def test_user_role_management(db_session):
    # Create Role
    role = RoleModel(id="test_role", name="test_role", description="Test Role")
    db_session.add(role)
    db_session.commit()

    # Create User
    user = UserModel(id=str(uuid.uuid4()), email="test@example.com", full_name="Test User")
    db_session.add(user)
    db_session.commit()

    # Assign Role
    user.roles.append(role)
    db_session.commit()
    db_session.refresh(user)

    # Verify
    assert len(user.roles) == 1
    assert user.roles[0].name == "test_role"
    assert user.has_role("test_role") is True
    assert user.has_role("other_role") is False
