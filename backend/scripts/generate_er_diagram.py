"""
Generate an entity-relationship diagram (Mermaid `erDiagram`) for the Lakebase
schema on demand.

By default the diagram is derived from the SQLAlchemy models (`Base.metadata`),
which needs no database connection. Pass `--live` to instead reflect the schema
from the connected database (Lakebase in a deployed env, or the local SQLite dev
DB), which is useful for spotting drift between the models and what actually
exists.

Usage:
    # From the models (no DB needed)
    python scripts/generate_er_diagram.py
    python scripts/generate_er_diagram.py --out docs/er_diagram.mmd

    # From the live database
    python scripts/generate_er_diagram.py --live
"""
import argparse
import logging
import os
import re
import sys

# Add the backend directory to sys.path so `app...` imports resolve when the
# script is run directly (matching the other scripts in this folder).
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import MetaData

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _load_model_metadata() -> MetaData:
    """Populate and return `Base.metadata` from the ORM models.

    Importing `app.db` registers most models against `Base`. A couple of models
    (notably `OdpsModel`) live in modules that aren't re-exported there, so we
    import them explicitly to make sure every table participates.
    """
    import app.db  # noqa: F401  (side effect: registers most models)
    import app.db.odps  # noqa: F401  (OdpsModel isn't re-exported by app.db)
    from app.db.base import Base

    return Base.metadata


def _load_live_metadata() -> MetaData:
    """Reflect the schema from the connected database."""
    from app.db.session import get_engine

    engine = get_engine()
    metadata = MetaData()
    metadata.reflect(bind=engine)
    logger.info("Reflected %d tables from %s", len(metadata.tables), engine.url)
    return metadata


def _mermaid_type(column) -> str:
    """Render a column type as a Mermaid-safe single token.

    Mermaid's erDiagram attribute grammar is strict: types may not contain
    spaces, parentheses or commas. We normalise e.g. ``VARCHAR(255)`` to
    ``VARCHAR_255`` and fall back to ``unknown`` for dialect-specific types that
    can't be rendered (common when reflecting live).
    """
    try:
        raw = str(column.type)
    except Exception:
        return "unknown"
    token = re.sub(r"[^0-9A-Za-z]+", "_", raw).strip("_")
    return token or "unknown"


def _entity_name(table_name: str) -> str:
    """Mermaid entity identifier. Table names are already identifier-safe."""
    return table_name


def generate_mermaid(metadata: MetaData) -> str:
    lines = ["erDiagram"]

    tables = list(metadata.sorted_tables)

    # --- Entities (one block of attributes per table) ---
    for table in tables:
        lines.append(f"    {_entity_name(table.name)} {{")
        for column in table.columns:
            type_token = _mermaid_type(column)
            keys = []
            if column.primary_key:
                keys.append("PK")
            if column.foreign_keys:
                keys.append("FK")
            key_suffix = f" {','.join(keys)}" if keys else ""
            # Mermaid attribute line: `<type> <name> [PK|FK]`
            lines.append(f"        {type_token} {column.name}{key_suffix}")
        lines.append("    }")

    # --- Relationships (one edge per foreign-key constraint) ---
    seen_edges = set()
    for table in tables:
        for fk in table.foreign_key_constraints:
            referred = fk.referred_table
            if referred is None:
                continue
            cols = ",".join(c.name for c in fk.columns)
            # Cardinality: a non-nullable FK is "exactly one" parent, a nullable
            # FK is "zero or one". The child side is always "zero or many".
            nullable = any(c.nullable for c in fk.columns)
            left = "||--o{" if not nullable else "|o--o{"
            edge_key = (referred.name, table.name, cols)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            lines.append(
                f'    {_entity_name(referred.name)} {left} '
                f'{_entity_name(table.name)} : "{cols}"'
            )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Mermaid ER diagram for the Lakebase schema."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Reflect the schema from the connected DB instead of the ORM models.",
    )
    parser.add_argument(
        "--out",
        metavar="FILE",
        help="Write the diagram to FILE instead of stdout.",
    )
    args = parser.parse_args()

    metadata = _load_live_metadata() if args.live else _load_model_metadata()
    diagram = generate_mermaid(metadata)

    table_count = len(metadata.sorted_tables)
    if args.out:
        with open(args.out, "w") as f:
            f.write(diagram)
        logger.info("Wrote ER diagram for %d tables to %s", table_count, args.out)
    else:
        sys.stdout.write(diagram)
        logger.info("Rendered ER diagram for %d tables", table_count)


if __name__ == "__main__":
    main()
