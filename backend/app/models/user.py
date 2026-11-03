from pydantic import BaseModel, ConfigDict
from typing import Dict, List, Optional

from app.core import capabilities

class User(BaseModel):
    """
    Application-level User model derived from Identity Provider (SCIM) entitlements
    and role mappings. Not stored in the database.
    """
    id: str
    email: str
    full_name: str
    is_active: bool = True
    
    # Entitlements fetched from IdP (e.g., Databricks SCIM)
    entitlements: List[str] = []
    
    # Calculated application roles
    roles: List[str] = []

    # "user", or "service_principal" for a registered machine caller (ADR-0006 D7),
    # whose id and email both hold its application id.
    actor_type: str = "user"
    application_id: Optional[str] = None
    # Identity headers the Apps proxy forwarded on this request (names only).
    identity_headers: List[str] = []

    model_config = ConfigDict(from_attributes=True)

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role. 'Super Admin' has all roles.

        Comparison is case-insensitive and treats underscores and spaces as
        equivalent, so callers may pass either the canonical name
        ("Governance Admin") or the snake_case form ("governance_admin") and
        still match the role as stored. The old name 'Platform Admin' is read as
        'Application Admin' on either side.
        """
        return capabilities.has_role(self.roles, role_name)

    def can(self, capability_id: str) -> bool:
        """Whether the user holds a capability from ``app.core.capabilities``."""
        return capabilities.has_capability(self.roles, capability_id)

    def capability_ids(self) -> List[str]:
        return capabilities.capabilities_for(self.roles)
