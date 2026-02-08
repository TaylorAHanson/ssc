from sqlalchemy.orm import Session
from app.db.user import RoleModel
import logging

logger = logging.getLogger(__name__)

# Standard roles definition
ROLES = [
    {"id": "role_platform_admin", "name": "platform_admin", "description": "Full system access"},
    {"id": "role_governance_admin", "name": "governance_admin", "description": "Governance and policy management"},
    {"id": "role_security_admin", "name": "security_admin", "description": "Security auditing and access control"},
    {"id": "role_finance_admin", "name": "finance_admin", "description": "Budget and cost management"},
    {"id": "role_business_user", "name": "business_user", "description": "Standard business user access"},
]

def init_db(db: Session) -> None:
    """
    Initialize database validation data.
    Ensures that all standard roles exist in the database.
    """
    logger.info("Initializing database: Checking roles...")
    
    roles_created = 0
    for role_data in ROLES:
        role = db.query(RoleModel).filter(RoleModel.name == role_data["name"]).first()
        if not role:
            logger.info(f"Seeding missing role: {role_data['name']}")
            role = RoleModel(**role_data)
            db.add(role)
            roles_created += 1
            
    if roles_created > 0:
        db.commit()
        logger.info(f"Database initialization complete. Seeded {roles_created} roles.")
    else:
        logger.info("Database initialization complete. All roles already exist.")
