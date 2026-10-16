"""Access rules: the group-to-securable ledger that replaces the RBAC workbook.

``rbac_rules`` holds one row per workbook row. A row's ``principal_name`` and
``privilege`` cells may each pack several values separated by ``;`` exactly as
the workbook does, so the CSV export can reproduce the sheets byte for byte.
``environment`` is the configured environment key (``RBAC_ENVIRONMENTS``) the
row belongs to, while ``environment_value`` is the Environment cell as written.

``rbac_sheets`` remembers each managed sheet's header text and column order
(captured on import) so the export writes the same layout back. Columns the app
doesn't model are kept per row in ``extra``, keyed by column index.

Every write goes through a change set (``rbac_change_sets``) and leaves one
``rbac_rule_events`` row per touched rule. The event id is monotonic, so the
largest id is the ledger's watermark for "has anything changed since the last
export". Deleted rules are soft-deleted (``status == "deleted"``) so history and
revert keep working.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped

from app.db.base import Base

PLANE_UC = "uc"
PLANE_METASTORE = "uc_metastore"
PLANE_WORKSPACE = "workspace"
PLANES = (PLANE_UC, PLANE_METASTORE, PLANE_WORKSPACE)

RULE_ACTIVE = "active"
RULE_DELETED = "deleted"

# Where a write came from (also stamped on events and change sets).
SOURCE_IMPORT = "import"
SOURCE_REQUEST = "request"
SOURCE_ADMIN_EDIT = "admin_edit"
SOURCE_REVERT = "revert"


class RbacSheetModel(Base):
    """One managed workbook sheet and the header layout the export reproduces."""

    __tablename__ = "rbac_sheets"

    name: Mapped[str] = Column(String, primary_key=True)
    plane: Mapped[str] = Column(String, nullable=False)
    environment: Mapped[Optional[str]] = Column(String, nullable=True)
    # [{"header": "Environment", "field": "environment_value"}, {"header": "Notes", "field": null}, ...]
    columns: Mapped[list] = Column(JSON, nullable=False, default=list)
    source_import_id: Mapped[Optional[str]] = Column(String, nullable=True)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RbacRuleModel(Base):
    __tablename__ = "rbac_rules"

    __table_args__ = (
        Index("ix_rbac_rules_sheet_status_position", "sheet", "status", "position"),
        Index("ix_rbac_rules_plane_env", "plane", "environment"),
        Index("ix_rbac_rules_row_hash", "row_hash"),
    )

    id: Mapped[str] = Column(String, primary_key=True)
    sheet: Mapped[str] = Column(String, nullable=False)
    plane: Mapped[str] = Column(String, nullable=False)
    environment: Mapped[Optional[str]] = Column(String, nullable=True)
    # Order within the sheet; the export writes rows in this order.
    position: Mapped[int] = Column(Integer, nullable=False, default=0)

    environment_value: Mapped[str] = Column(Text, nullable=False, default="")
    securable_type: Mapped[str] = Column(Text, nullable=False, default="")
    catalog: Mapped[str] = Column(Text, nullable=False, default="")
    schema_name: Mapped[str] = Column(Text, nullable=False, default="")
    object_name: Mapped[str] = Column(Text, nullable=False, default="")
    workspace_name: Mapped[str] = Column(Text, nullable=False, default="")
    owner: Mapped[str] = Column(Text, nullable=False, default="")
    principal_type: Mapped[str] = Column(Text, nullable=False, default="")
    principal_name: Mapped[str] = Column(Text, nullable=False, default="")
    privilege: Mapped[str] = Column(Text, nullable=False, default="")
    # Unmodelled columns, keyed by column index (as a string) within the sheet layout.
    extra: Mapped[dict] = Column(JSON, nullable=False, default=dict)

    status: Mapped[str] = Column(String, nullable=False, default=RULE_ACTIVE)
    # Optimistic-lock counter; every write bumps it.
    version: Mapped[int] = Column(Integer, nullable=False, default=1)
    # Hash of sheet + canonical cells + extra, for duplicate detection and Compare.
    row_hash: Mapped[str] = Column(String, nullable=False, default="")

    source: Mapped[str] = Column(String, nullable=False, default=SOURCE_ADMIN_EDIT)
    source_ref: Mapped[Optional[str]] = Column(String, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    created_by: Mapped[Optional[str]] = Column(String, nullable=True)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by: Mapped[Optional[str]] = Column(String, nullable=True)


class RbacChangeSetModel(Base):
    """One atomic write to the ledger: an import, an approved request, an admin edit or a revert."""

    __tablename__ = "rbac_change_sets"

    __table_args__ = (Index("ix_rbac_change_sets_created_at", "created_at"),)

    id: Mapped[str] = Column(String, primary_key=True)
    source: Mapped[str] = Column(String, nullable=False)
    # Import id, request id, or the change set id being reverted.
    source_ref: Mapped[Optional[str]] = Column(String, nullable=True)
    actor: Mapped[Optional[str]] = Column(String, nullable=True)
    reason: Mapped[Optional[str]] = Column(Text, nullable=True)
    # {"created": n, "updated": n, "deleted": n, "sheets": [...], "planes": [...]}
    summary: Mapped[dict] = Column(JSON, nullable=False, default=dict)
    reverted_by: Mapped[Optional[str]] = Column(String, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)


class RbacRuleEventModel(Base):
    __tablename__ = "rbac_rule_events"

    __table_args__ = (
        Index("ix_rbac_rule_events_rule", "rule_id"),
        Index("ix_rbac_rule_events_change_set", "change_set_id"),
    )

    # Monotonic: max(id) is the ledger watermark.
    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = Column(String, nullable=False)
    change_set_id: Mapped[str] = Column(String, nullable=False)
    # create | update | delete | restore
    action: Mapped[str] = Column(String, nullable=False)
    before: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    after: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    actor: Mapped[Optional[str]] = Column(String, nullable=True)
    reason: Mapped[Optional[str]] = Column(Text, nullable=True)
    source: Mapped[str] = Column(String, nullable=False)
    source_ref: Mapped[Optional[str]] = Column(String, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)


class RbacImportModel(Base):
    __tablename__ = "rbac_imports"

    id: Mapped[str] = Column(String, primary_key=True)
    filename: Mapped[Optional[str]] = Column(String, nullable=True)
    # sha256 of the uploaded file; the run must upload the same file it previewed.
    file_sha256: Mapped[str] = Column(String, nullable=False)
    # initial | reset
    mode: Mapped[Optional[str]] = Column(String, nullable=True)
    # previewed | completed | failed
    status: Mapped[str] = Column(String, nullable=False, default="previewed")
    actor: Mapped[Optional[str]] = Column(String, nullable=True)
    # Per-sheet: {"<sheet>": {"plane", "environment", "rows", "columns": [...], "warnings": n}}
    sheets: Mapped[dict] = Column(JSON, nullable=False, default=dict)
    skipped_sheets: Mapped[list] = Column(JSON, nullable=False, default=list)
    # Capped list of Finding dicts (warnings only; imports are never blocked by policy).
    findings: Mapped[list] = Column(JSON, nullable=False, default=list)
    change_set_id: Mapped[Optional[str]] = Column(String, nullable=True)
    error: Mapped[Optional[str]] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)


class RbacExportModel(Base):
    __tablename__ = "rbac_exports"

    __table_args__ = (Index("ix_rbac_exports_started_at", "started_at"),)

    id: Mapped[str] = Column(String, primary_key=True)
    # schedule | manual | on_change
    trigger: Mapped[str] = Column(String, nullable=False)
    actor: Mapped[Optional[str]] = Column(String, nullable=True)
    # running | completed | partial | skipped | failed
    status: Mapped[str] = Column(String, nullable=False, default="running")
    # Ledger watermark (max rbac_rule_events.id) this export reflects.
    watermark: Mapped[int] = Column(Integer, nullable=False, default=0)
    # {"created": n, "updated": n, "deleted": n, "change_sets": [...], "request_ids": [...]}
    change_summary: Mapped[dict] = Column(JSON, nullable=False, default=dict)
    warnings: Mapped[list] = Column(JSON, nullable=False, default=list)
    error: Mapped[Optional[str]] = Column(Text, nullable=True)
    started_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)


class RbacExportFileModel(Base):
    """One CSV written by an export. ``content`` is the archived copy served for download."""

    __tablename__ = "rbac_export_files"

    __table_args__ = (Index("ix_rbac_export_files_export", "export_id"),)

    id: Mapped[str] = Column(String, primary_key=True)
    export_id: Mapped[str] = Column(String, nullable=False)
    sheet: Mapped[str] = Column(String, nullable=False)
    path: Mapped[Optional[str]] = Column(String, nullable=True)
    sha256: Mapped[Optional[str]] = Column(String, nullable=True)
    row_count: Mapped[int] = Column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = Column(Integer, nullable=False, default=0)
    # written | skipped | failed
    status: Mapped[str] = Column(String, nullable=False)
    error: Mapped[Optional[str]] = Column(Text, nullable=True)
    # Null once pruned by RBAC_EXPORT_ARCHIVE_DAYS.
    content: Mapped[Optional[str]] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
