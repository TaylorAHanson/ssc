from pydantic import BaseModel, ConfigDict
from typing import Dict, List, Optional

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
    
    model_config = ConfigDict(from_attributes=True)

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role. 'Platform Admin' has all roles."""
        lower_roles = [r.lower() for r in self.roles]
        return "platform admin" in lower_roles or role_name.lower() in lower_roles
