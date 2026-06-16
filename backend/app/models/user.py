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
        """Check if user has a specific role. 'Platform Admin' has all roles.

        Comparison is case-insensitive and treats underscores and spaces as
        equivalent, so callers may pass either the canonical name
        ("Governance Admin") or the snake_case form ("governance_admin") and
        still match the role as stored. This avoids silent authorization gaps
        where, e.g., a Governance Admin was denied because the call site used
        an underscore that never matched the space-delimited stored role.
        """
        def _norm(r: str) -> str:
            return r.strip().lower().replace("_", " ")

        normalized = {_norm(r) for r in self.roles}
        return "platform admin" in normalized or _norm(role_name) in normalized
