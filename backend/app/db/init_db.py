from sqlalchemy.orm import Session
from app.db.user import UserModel, RoleModel
import logging
import uuid
from app.core.config import settings

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

    # 2. SEED ADMIN USER
    # We use the same email as deps.py for local dev fallback
    admin_email = "admin@qualcomm.com"
    admin_user = db.query(UserModel).filter(UserModel.email == admin_email).first()
    
    if not admin_user:
        logger.info(f"Seeding admin user: {admin_email}")
        try:
             admin_user = UserModel(
                 id=str(uuid.uuid4()),
                 email=admin_email,
                 full_name="System Admin",
                 is_active=True
             )
             db.add(admin_user)
             db.flush() # Flush to get ID if needed, but here we set it manually.
                        # Flush helps check for integrity errors early.
             
             # Assign all roles
             all_roles = db.query(RoleModel).all()
             admin_user.roles = all_roles
             
             db.commit()
             db.refresh(admin_user)
             logger.info(f"Admin user {admin_email} seeded successfully.")
        except Exception as e:
             logger.warning(f"Failed to seed admin user (might already exist): {e}")
             db.rollback()
    else:
        logger.debug(f"Admin user {admin_email} already exists.")
