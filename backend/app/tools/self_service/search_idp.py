"""
Tools to search for users and groups in the Identity Provider (IDP).
"""
from typing import Dict, Any, List
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.idp.client import IDPProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import logging

logger = logging.getLogger(__name__)

def _get_idp_provider() -> IDPProvider:
    """Helper to instantiate the IDP provider."""
    return IDPProvider(
        base_url=settings.IDP_BASE_URL or "https://mock-idp.example.com",
        api_key=settings.IDP_API_KEY or "mock-key"
    )

class SearchIDPGroupsInput(BaseModel):
    query: str = Field(..., description="The search query (e.g., part of the group name).")

@tool(
    name="search_idp_groups",
    description="Searches for groups in the Identity Provider (also known as Active Directory/Ldap/ListManager/Qgroups/N2K) to verify if a group exists before attempting to create or use it.",
    args_schema=SearchIDPGroupsInput
)
async def search_idp_groups(query: str) -> Dict[str, Any]:
    """
    Search for IDP groups using the IDP Provider.
    """
    logger.info(f"Searching IDP groups for query: {query}")
    try:
        async with _get_idp_provider() as provider:
            return await provider.search_groups(query)
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to search IDP groups: {str(e)}")


class SearchIDPUsersInput(BaseModel):
    query: str = Field(..., description="The search query (e.g., email address, name).")

@tool(
    name="search_idp_users",
    description="Searches for users in the Identity Provider (also known as Active Directory/Ldap/ListManager/Qgroups/N2K) to verify if a user exists.",
    args_schema=SearchIDPUsersInput
)
async def search_idp_users(query: str) -> Dict[str, Any]:
    """
    Search for IDP users using the IDP Provider.
    """
    logger.info(f"Searching IDP users for query: {query}")
    try:
        async with _get_idp_provider() as provider:
            return await provider.search_users(query)
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to search IDP users: {str(e)}")

