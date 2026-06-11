"""
Serializable workflow-spec catalog — the no-code "workflows as data" source.

The single source of truth is the bundled JSON catalog under ``catalog/``: one
file per default workflow, each an object with a string ``key`` (the request-type
value it governs) plus a declarative spec (see :mod:`app.workflows.spec_loader`
for the schema and :mod:`app.workflows.expr` for the expression mini-language).

Those JSON dicts compile to runtime ``WorkflowSpec``s (and durable LangGraph
graphs) via :func:`spec_from_dict`, *and* they are what the seed writes into a
Workflow's ``graph_spec`` so an admin can edit a workflow's gates and steps in
the UI instead of changing code and redeploying.

Adding a workflow therefore requires **no enum entry and no code change** — drop
a JSON file in ``catalog/`` (or, at runtime, author + publish one in the UI/agent;
a published DB ``graph_spec`` overrides this seed). Every executable workflow —
including data access, whose runtime multi-owner resolution is expressed with a
``resolve_data_owners`` step + a gate ``approvers_from`` expression — lives here
as data, never as a dedicated code graph.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from typing import Dict

from app.workflows.spec import build_spec_graph
from app.workflows.spec_loader import (
    SpecError,
    spec_from_dict,
    stage_specs_from_dict,
    validate_spec_dict,
)

logger = logging.getLogger(__name__)

_CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog")


def _load_catalog(catalog_dir: str = _CATALOG_DIR) -> Dict[str, dict]:
    """Load every ``catalog/*.json`` workflow into a ``{key: spec}`` mapping.

    Each file must be a JSON object with a non-empty string ``key`` (the
    request-type value it governs) and an otherwise-valid workflow spec. Specs
    are validated at load time so a malformed bundled default fails fast at
    startup rather than at run time.
    """
    specs: Dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(catalog_dir, "*.json"))):
        fname = os.path.basename(path)
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise SpecError(f"catalog file {fname} is not valid JSON: {e}")
        if not isinstance(data, dict):
            raise SpecError(f"catalog file {fname} must be a JSON object")
        key = data.pop("key", None)
        if not isinstance(key, str) or not key.strip():
            raise SpecError(f"catalog file {fname} is missing a non-empty string 'key'")
        if key in specs:
            raise SpecError(f"duplicate catalog key '{key}' ({fname})")
        try:
            validate_spec_dict(data)
        except SpecError as e:
            raise SpecError(f"catalog file {fname} ('{key}') is invalid: {e}")
        specs[key] = data
    logger.debug("Loaded %d workflow specs from %s", len(specs), catalog_dir)
    return specs


# request_type value -> spec dict. The bundled JSON catalog is the source of truth.
SPECS: Dict[str, dict] = _load_catalog()


def make_spec_builder(spec_dict):
    """Wrap a spec dict into a no-arg graph builder for the registry."""
    def builder():
        return build_spec_graph(spec_from_dict(spec_dict))
    return builder


# request_type value -> no-arg graph builder. Every type is spec-generated.
SPEC_FACTORIES = {rt: make_spec_builder(spec) for rt, spec in SPECS.items()}


def ui_stage_ids(request_type) -> list:
    """Ordered UI states for a request type: pending -> stages... -> completed."""
    key = getattr(request_type, "value", request_type)
    stage_names = [s["name"] for s in SPECS[key].get("stages", [])] if key in SPECS else []
    return ["pending"] + stage_names + ["completed"]


def stage_specs(request_type) -> list:
    """Introspect a type's stages for the UI renderer.

    Returns ordered dicts: ``{name, kind: gate|step, gate_type, success_fact}``.
    """
    key = getattr(request_type, "value", request_type)
    if key in SPECS:
        return stage_specs_from_dict(SPECS[key])
    return []


def editable_states(request_type) -> list:
    """Stages from which a platform_admin may Edit & Restart (platform_admin gates)."""
    key = getattr(request_type, "value", request_type)
    if key not in SPECS:
        return []
    return [s["name"] for s in SPECS[key].get("stages", [])
            if s.get("kind") == "gate" and s.get("type") == "platform_admin"]
