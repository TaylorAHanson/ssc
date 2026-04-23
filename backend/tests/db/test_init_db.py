import pytest
from sqlalchemy.orm import Session
from app.db.init_db import init_db, DEFAULT_MAPPINGS
from app.db.role_mapping import RoleMappingModel

def test_init_db_creates_mappings(db_session: Session):
    """Test that init_db creates all defined default role mappings."""
    # Ensure DB is clean first
    db_session.query(RoleMappingModel).delete()
    db_session.commit()

    # Run init_db
    init_db(db_session)

    # Verify all mappings exist
    for mapping_data in DEFAULT_MAPPINGS:
        mapping = db_session.query(RoleMappingModel).filter(
            RoleMappingModel.external_role == mapping_data["external_role"],
            RoleMappingModel.internal_role == mapping_data["internal_role"]
        ).first()
        assert mapping is not None, f"Mapping {mapping_data['external_role']} missing"

    # Count mappings
    count = db_session.query(RoleMappingModel).count()
    assert count == len(DEFAULT_MAPPINGS)

def test_init_db_idempotent(db_session: Session):
    """Test that running init_db twice does not duplicate records."""
    # Run once
    init_db(db_session)
    count1 = db_session.query(RoleMappingModel).count()

    # Run twice
    init_db(db_session)
    count2 = db_session.query(RoleMappingModel).count()

    assert count1 == count2
