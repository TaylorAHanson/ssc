"""
Pluggable identity-group provider.

Generalizes group/membership management away from any single vendor (Qualcomm's
LMWS/FWS-API). The active backend is chosen by ``settings.IDENTITY_PROVIDER``:

    noop  - default; logs + records (works out-of-the-box, no external system)
    rest  - generic SCIM/REST endpoint (config-driven)
    lmws  - Qualcomm FWS-API via the vendored Databricks job (legacy adapter)

All V2 group mutations go through the single ``add_group_membership`` tool ->
``get_identity_provider()``, so swapping vendors is one config change.
"""
import logging
from functools import lru_cache

from app.core.config import settings
from app.providers.identity.base import IdentityGroupProvider

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_identity_provider() -> IdentityGroupProvider:
    kind = (settings.IDENTITY_PROVIDER or "noop").strip().lower()
    if kind == "lmws":
        from app.providers.identity.lmws_adapter import LmwsIdentityProvider
        return LmwsIdentityProvider()
    if kind == "rest":
        from app.providers.identity.rest import RestIdentityProvider
        return RestIdentityProvider()
    if kind == "noop":
        from app.providers.identity.noop import NoopIdentityProvider
        return NoopIdentityProvider()
    logger.warning("Unknown IDENTITY_PROVIDER=%r; falling back to noop", kind)
    from app.providers.identity.noop import NoopIdentityProvider
    return NoopIdentityProvider()
