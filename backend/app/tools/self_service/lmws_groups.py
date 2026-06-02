"""
LMWS group/user lookup tools for the agent.

These replace the former Entra ID search tools. Unlike Entra ID (which the app
queried directly over Microsoft Graph), LMWS lookups run as a Databricks job
against the LMWS notebook (see ``app.providers.lmws``). The provider's inline
path submits the job and polls until it returns.

LATENCY CAVEAT: with *ephemeral* classic compute a run incurs a 1-3 min cluster
cold start, which can exceed the agent's per-turn timeout
(``AGENT_TIMEOUT_SECONDS``). For responsive interactive lookups, pin an
always-on cluster or instance pool via ``DATABRICKS_JOB_CLUSTER_ID`` /
``DATABRICKS_JOB_INSTANCE_POOL_ID``. Membership *writes* go through the
non-blocking state-machine job-step path instead and are unaffected.
"""
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.lmws import LmwsProvider
from app.core.exceptions import RetryableError
import logging

logger = logging.getLogger(__name__)


class LmwsListRetrieveInput(BaseModel):
    list_name: str = Field(..., description="Exact name of the LMWS list/group to look up.")


@tool(
    name="lmws_list_retrieve",
    description=(
        "Look up an LMWS group/list by its exact name, returning its owner, "
        "supervisors, and members. Use to verify a list exists or inspect its "
        "membership before requesting changes."
    ),
    args_schema=LmwsListRetrieveInput,
)
async def lmws_list_retrieve(list_name: str) -> Dict[str, Any]:
    """Retrieve owner/supervisors/members of an LMWS list."""
    logger.info(f"LMWS list_retrieve: {list_name}")
    try:
        return await LmwsProvider().list_retrieve(list_name)
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to retrieve LMWS list '{list_name}': {e}")


class LmwsMemberRetrieveInput(BaseModel):
    member: str = Field(..., description="The user CN (or email) whose group memberships to look up.")


@tool(
    name="lmws_member_retrieve",
    description=(
        "Look up all LMWS group/list memberships for a given user (CN). Use to "
        "verify a user exists and see which lists they already belong to."
    ),
    args_schema=LmwsMemberRetrieveInput,
)
async def lmws_member_retrieve(member: str) -> Dict[str, Any]:
    """Retrieve all LMWS group memberships for a user."""
    logger.info(f"LMWS member_retrieve: {member}")
    try:
        return await LmwsProvider().member_retrieve(member)
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to retrieve LMWS memberships for '{member}': {e}")
