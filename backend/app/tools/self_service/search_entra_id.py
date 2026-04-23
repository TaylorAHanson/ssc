"""
Tools to search for users and groups in the Identity Provider (IDP).
"""
from typing import Dict, Any, List
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.entra_id.client import EntraIdProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import logging

logger = logging.getLogger(__name__)

def _get_idp_provider() -> EntraIdProvider:
    """Helper to instantiate the Entra ID provider."""
    return EntraIdProvider(
        tenant_id=getattr(settings, "ENTRA_ID_TENANT_ID", "mock-tenant-id"),
        client_id=getattr(settings, "ENTRA_ID_CLIENT_ID", "mock-client-id"),
        client_secret=getattr(settings, "ENTRA_ID_CLIENT_SECRET", "mock-client-secret")
    )

class SearchEntraIdGroupsInput(BaseModel):
    query: str = Field(..., description="The search query (e.g., part of the group name).")

@tool(
    name="search_entra_id_groups",
    description="Searches for groups in Entra ID to verify if a group exists before attempting to create or use it.",
    args_schema=SearchEntraIdGroupsInput
)
async def search_entra_id_groups(query: str) -> Dict[str, Any]:
    """
    Search for Entra ID groups using the Entra ID Provider.
    """
    logger.info(f"Searching Entra ID groups for query: {query}")
    try:
        async with _get_idp_provider() as provider:
            return await provider.search_groups(query)
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to search Entra ID groups: {str(e)}")


class SearchEntraIdUsersInput(BaseModel):
    query: str = Field(..., description="The search query (e.g., email address, name).")

@tool(
    name="search_entra_id_users",
    description="Searches for users in Entra ID to verify if a user exists.",
    args_schema=SearchEntraIdUsersInput
)
async def search_entra_id_users(query: str) -> Dict[str, Any]:
    """
    Search for Entra ID users using the Entra ID Provider.
    """
    logger.info(f"Searching Entra ID users for query: {query}")
    try:
        async with _get_idp_provider() as provider:
            return await provider.search_users(query)
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to search Entra ID users: {str(e)}")

