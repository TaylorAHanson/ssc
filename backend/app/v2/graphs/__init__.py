"""V2 graph registry: request type -> compiled-graph builder.

The durable executor looks a request's type up here to get its graph. In V2
these graphs are the published *Skills* (M3); most are generated from
declarative specs (``specs.py``), with ``data_access`` keeping a dedicated graph
for multi-owner resolution.
"""
import logging
from typing import Any, Callable, Dict, Optional

from app.models.request import RequestType
from app.v2.graphs import data_access

logger = logging.getLogger(__name__)
from app.v2.graphs.specs import (
    SPEC_FACTORIES,
    editable_states,
    stage_specs,
    ui_stage_ids,
)

# Dedicated (hand-authored) graphs.
GRAPH_BUILDERS: Dict[str, Callable] = {
    RequestType.DATA_ACCESS_REQUEST.value: data_access.build_graph,
    RequestType.CATALOG_SCHEMA_TABLE_ACCESS.value: data_access.build_graph,
    RequestType.BATCH_DATA_ACCESS.value: data_access.build_graph,
}

# Spec-generated graphs (everything else) compiled from the data catalog.
# Dedicated entries win on conflict.
for _rt, _builder in SPEC_FACTORIES.items():
    GRAPH_BUILDERS.setdefault(_rt, _builder)


def get_graph_builder(request_type) -> Callable:
    """Return the graph builder for a request type, or raise KeyError."""
    key = getattr(request_type, "value", request_type)
    return GRAPH_BUILDERS[key]


def has_graph(request_type) -> bool:
    key = getattr(request_type, "value", request_type)
    return key in GRAPH_BUILDERS


def registered_types() -> list:
    return sorted(GRAPH_BUILDERS.keys())


def published_graph_spec(db, request_type) -> Optional[dict]:
    """Return a published skill's ``graph_spec`` for this type, or ``None``.

    This is the no-code override point: an admin-authored, published workflow
    graph wins over the code catalog. Any lookup error degrades to ``None`` so
    execution always falls back to the code spec.
    """
    key = getattr(request_type, "value", request_type)
    try:
        from app.db.skill import SkillModel

        skill = (
            db.query(SkillModel)
            .filter(
                SkillModel.request_type == key,
                SkillModel.status == "published",
                SkillModel.graph_spec.isnot(None),
            )
            .first()
        )
        if skill and skill.graph_spec:
            return skill.graph_spec
    except Exception as e:  # noqa: BLE001 - resolution must never break execution
        logger.debug("published_graph_spec lookup failed for %s: %s", key, e)
    return None


def build_graph_for(request_type, db=None) -> Any:
    """Build the (uncompiled) graph for a request type.

    Prefers a published DB ``graph_spec`` (compiled via the data loader); on any
    problem, or when no DB spec exists, falls back to the code catalog builder.
    """
    if db is not None:
        spec = published_graph_spec(db, request_type)
        if spec:
            try:
                from app.v2.spec import build_spec_graph
                from app.v2.spec_loader import spec_from_dict

                return build_spec_graph(spec_from_dict(spec))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "DB graph_spec for %s invalid; using code catalog: %s",
                    getattr(request_type, "value", request_type), e,
                )
    return get_graph_builder(request_type)()
