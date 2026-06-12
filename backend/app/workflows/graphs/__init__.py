"""V2 graph registry: request type -> compiled-graph builder.

The durable executor looks a request's type up here to get its graph. In V2
these graphs are the published *Workflows* (M3): a workflow is a ``graph_spec``
(data). Every type is generated from the declarative spec catalog (``specs.py``)
— there is no longer a dedicated hand-authored code-graph path. Code is just the
seed; a published DB ``graph_spec`` overrides it (see ``build_graph_for``).
"""
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)
from app.workflows.graphs.specs import (
    SPEC_FACTORIES,
    editable_states,
    stage_specs,
    ui_stage_ids,
)

# Every request type's graph is generated from the data catalog (specs.py).
GRAPH_BUILDERS: Dict[str, Callable] = dict(SPEC_FACTORIES)


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
    """Return a published workflow's ``graph_spec`` for this type, or ``None``.

    This is the no-code override point: an admin-authored, published workflow
    graph wins over the code catalog. Any lookup error degrades to ``None`` so
    execution always falls back to the code spec.
    """
    key = getattr(request_type, "value", request_type)
    try:
        from app.db.workflow import WorkflowModel

        workflow = (
            db.query(WorkflowModel)
            .filter(
                WorkflowModel.request_type == key,
                WorkflowModel.status == "published",
                WorkflowModel.graph_spec.isnot(None),
            )
            .first()
        )
        if workflow and workflow.graph_spec:
            return workflow.graph_spec
    except Exception as e:  # noqa: BLE001 - resolution must never break execution
        logger.debug("published_graph_spec lookup failed for %s: %s", key, e)
    return None


def make_child_resolver(db=None) -> Callable[[Any], Any]:
    """Build a subworkflow resolver: a workflow key -> its ``WorkflowSpec``.

    Threaded into :func:`app.workflows.spec.build_spec_graph` so compound
    workflows can compose nested graphs without that module importing the DB or
    catalog. A published DB workflow wins over the bundled catalog (same override
    semantics as :func:`build_graph_for`).
    """
    from app.workflows.graphs.specs import SPECS
    from app.workflows.spec_loader import spec_from_dict

    def resolver(key):
        k = getattr(key, "value", key)
        if db is not None:
            spec = published_graph_spec(db, k)
            if spec:
                try:
                    return spec_from_dict(spec)
                except Exception as e:  # noqa: BLE001
                    logger.warning("child workflow %s DB spec invalid: %s", k, e)
        raw = SPECS.get(k)
        return spec_from_dict(raw) if raw is not None else None

    return resolver


def build_graph_for(request_type, db=None) -> Any:
    """Build the (uncompiled) graph for a request type.

    Prefers a published DB ``graph_spec`` (compiled via the data loader); on any
    problem, or when no DB spec exists, falls back to the code catalog builder.
    """
    if db is not None:
        spec = published_graph_spec(db, request_type)
        if spec:
            try:
                from app.workflows.spec import build_spec_graph
                from app.workflows.spec_loader import spec_from_dict

                return build_spec_graph(spec_from_dict(spec), make_child_resolver(db))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "DB graph_spec for %s invalid; using code catalog: %s",
                    getattr(request_type, "value", request_type), e,
                )
    return get_graph_builder(request_type)()
