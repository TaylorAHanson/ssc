from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.types import TypeDecorator
import sqlalchemy.types as types
import json
from datetime import datetime
from app.db.base import Base

class JSONType(TypeDecorator):
    """Platform-independent JSON type."""
    impl = types.String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(SQLiteJSON())
        elif dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB
            return dialect.type_descriptor(JSONB())
        else:
            return dialect.type_descriptor(types.String())

    def process_bind_param(self, value, dialect):
        if dialect.name == "sqlite":
            return json.dumps(value) if value is not None else None
        return value

    def process_result_value(self, value, dialect):
        if dialect.name == "sqlite" and value is not None:
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return value
        return value

class DataAssetModel(Base):
    """
    Model for cached data assets available for discovery.
    """
    __tablename__ = "data_assets"

    id = Column(String, primary_key=True, index=True) # Usually catalog.schema.table
    catalog = Column(String, nullable=False, index=True)
    schema = Column(String, nullable=False, index=True)
    table_name = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False) # Table, View, Model, etc
    description = Column(String, nullable=True)
    owner = Column(String, nullable=True)
    domain = Column(String, nullable=True, index=True)
    tags = Column(JSONType, default=list)
    certified = Column(Boolean, default=False)
    contract_url = Column(String, nullable=True)
    data_quality = Column(JSONType, nullable=True) # e.g. {"freshness": "99%", ...}
    certification_violations = Column(JSONType, nullable=True) # List of strings explaining why certification failed
    sla = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
