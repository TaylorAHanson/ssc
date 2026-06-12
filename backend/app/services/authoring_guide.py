"""Remove the legacy 'Authoring Workflows (Workflows)' guide from the Context Catalog.

The workflow-authoring guide used to be seeded into the Context Catalog so the
agent could read it. That created two problems: (1) the shared
``search_context_catalog`` tool let the *main* self-service agent surface this
admin-only doc to end users, and (2) the doc drifted out of sync with the spec
model (e.g. compound/subworkflow stages, deprecated ``children`` gate). The
authoring agent now relies on ``list_workflow_building_blocks`` (always current,
derived from the live tool/spec layer) as its single source of truth instead.

This module now only *removes* the previously-seeded content so existing installs
get cleaned up on boot. Cleanup is idempotent and never raises (must not break
startup). The matching system domain is removed only when it has no other
documents, so any admin-authored runbooks placed there are preserved.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_DOMAIN_NAME = "Platform Administration"
# The guide was reseeded under a few titles over time (e.g. "… (Skills) — Guide",
# "… (Workflows) — Guide"), but every revision carried the "workflow-authoring"
# tag. Match on that tag (or an "Authoring Workflows" title prefix) so all
# historical variants are purged, not just the latest title.
_AUTHORING_TAG = "workflow-authoring"
_TITLE_PREFIX = "Authoring Workflows"


def _is_authoring_doc(doc) -> bool:
    if _AUTHORING_TAG in (getattr(doc, "tags", None) or []):
        return True
    return (getattr(doc, "title", "") or "").startswith(_TITLE_PREFIX)


def remove_authoring_guide(db: Session) -> None:
    """Delete any seeded authoring-guide doc (and its empty system domain).

    Idempotent: a no-op once the content is gone. Never raises.
    """
    from app.services.context_catalog_service import ContextCatalogService

    try:
        domain = next(
            (d for d in ContextCatalogService.list_domains(db) if d.name == _DOMAIN_NAME),
            None,
        )
        if domain is None:
            return

        docs = ContextCatalogService.list_documents(db, domain.id)
        removed = False
        for doc in docs:
            if _is_authoring_doc(doc):
                ContextCatalogService.delete_document(db, doc.id)
                removed = True
                logger.info("Removed legacy Context Catalog document '%s'", doc.title)

        # Drop the system domain only if it was seeded solely for this guide and
        # now has no documents — never clobber admin-authored runbooks.
        remaining = ContextCatalogService.list_documents(db, domain.id)
        if not remaining and getattr(domain, "domain_type", None) == "system":
            ContextCatalogService.delete_domain(db, domain.id)
            logger.info("Removed empty system Context Catalog domain '%s'", _DOMAIN_NAME)
        elif removed:
            logger.info(
                "Kept Context Catalog domain '%s' (%d other document(s) present)",
                _DOMAIN_NAME, len(remaining),
            )
    except Exception as e:  # noqa: BLE001 - cleanup must never break startup
        logger.warning("Authoring guide cleanup skipped: %s", e)
