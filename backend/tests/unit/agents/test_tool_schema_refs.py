"""Tool schemas sent to the model carry no ``$ref`` (Gemini rejects them)."""
import json
from typing import List, Optional

from pydantic import BaseModel

from app.agents.runner import AgentRunner, _inline_schema_refs
from app.tools.authoring.workflow_authoring import save_workflow_tests


class _Case(BaseModel):
    name: str
    expect: Optional[str] = None


class _Args(BaseModel):
    cases: List[_Case]
    primary: _Case


class _Node(BaseModel):
    label: str
    children: List["_Node"] = []


def test_nested_models_are_inlined():
    schema = _inline_schema_refs(_Args.model_json_schema())
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    assert schema["properties"]["cases"]["items"]["properties"]["name"] == {"title": "Name", "type": "string"}
    assert schema["properties"]["primary"]["required"] == ["name"]


def test_a_self_referencing_model_terminates():
    schema = _inline_schema_refs(_Node.model_json_schema())
    assert "$ref" not in json.dumps(schema)


def test_the_authoring_tool_is_sent_without_refs():
    formatted = AgentRunner._format_tools_for_llm(None, [save_workflow_tests])
    params = formatted[0]["function"]["parameters"]
    assert "$ref" not in json.dumps(params)
    assert params["properties"]["cases"]["items"]["type"] == "object"
