from sqlalchemy.orm import Session
from app.db.role_mapping import RoleMappingModel
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

# Default role mappings to seed
DEFAULT_MAPPINGS = [
    {"external_role": "srikanth.anumula@databricks.com", "internal_role": "Platform Admin"},
    {"external_role": "admin@example.com", "internal_role": "Platform Admin"},
    {"external_role": "platform_admin", "internal_role": "Platform Admin"},
    {"external_role": "governance_admin", "internal_role": "Governance Admin"},
    {"external_role": "finance_admin", "internal_role": "Finance Admin"},
    {"external_role": "security_admin", "internal_role": "Security Admin"},
    {"external_role": "users", "internal_role": "User"},
]

def init_db(db: Session) -> None:
    """
    Initialize database validation data.
    Ensures that default role mappings exist.
    """
    logger.info("Initializing database: Checking role mappings...")
    
    mappings_created = 0
    for mapping_data in DEFAULT_MAPPINGS:
        mapping = db.query(RoleMappingModel).filter(
            RoleMappingModel.external_role == mapping_data["external_role"],
            RoleMappingModel.internal_role == mapping_data["internal_role"]
        ).first()
        
        if not mapping:
            logger.info(f"Seeding missing role mapping: {mapping_data['external_role']} -> {mapping_data['internal_role']}")
            mapping = RoleMappingModel(**mapping_data)
            db.add(mapping)
            mappings_created += 1
            
    if mappings_created > 0:
        db.commit()
        logger.info(f"Database initialization complete. Seeded {mappings_created} role mappings.")
    else:
        logger.info("Database initialization complete. Default mappings already exist.")
