from sqlalchemy.orm import Session
from app.db.init_db import init_db, ROLES
from app.db.user import RoleModel

def test_init_db_creates_roles(db_session: Session):
    """Test that init_db creates all defined roles."""
    # Ensure DB is clean of roles first (depends on fixture scope, but good to be safe)
    db_session.query(RoleModel).delete()
    db_session.commit()
    
    # Run init_db
    init_db(db_session)
    
    # Verify all roles exist
    for role_data in ROLES:
        role = db_session.query(RoleModel).filter(RoleModel.name == role_data["name"]).first()
        assert role is not None
        assert role.description == role_data["description"]
        
def test_init_db_is_idempotent(db_session: Session):
    """Test that running init_db multiple times doesn't create duplicates."""
    # Run once
    init_db(db_session)
    
    # Count roles
    initial_count = db_session.query(RoleModel).count()
    assert initial_count == len(ROLES)
    
    # Run again
    init_db(db_session)
    
    # Count should be the same
    final_count = db_session.query(RoleModel).count()
    assert final_count == initial_count
