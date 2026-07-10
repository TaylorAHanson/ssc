from sqlalchemy import Column, String, DateTime, JSON, func
from app.db.base import Base


class AppSettingModel(Base):
    """Runtime, admin-editable configuration override.

    Each row is one setting whose value overrides the deploy-time default
    (env var from databricks.yml or a key in configuration.yaml). The value is
    stored JSON-encoded so a bool/int/string/list all round-trip cleanly. At
    startup ``settings_store.load_overrides`` reads every row and applies it to
    the live ``settings`` object / config dicts, so overrides take effect
    without a redeploy. Deploy-time infra + secrets are intentionally NOT
    represented here (see ``settings_store.READONLY_FIELDS``).
    """

    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(String, nullable=True)
