"""MLflow "models from code" entry point for the Atlas ResponsesAgent.

This module is logged verbatim as the model's code by
``scripts/register_responses_agent.py`` (``mlflow.pyfunc.log_model(python_model=<this file>)``).
At load time — in Model Serving, batch eval, or the Playground — MLflow executes
this module and serves the object handed to :func:`mlflow.models.set_model`.

Keeping the agent as code (rather than a pickled object) is the Databricks
best-practice for ``ResponsesAgent``: the served model is exactly the in-app
:class:`~app.agents.responses_agent.AtlasResponsesAgent`, so the governed
ToolExecutor, OBO identity, and MLflow tracing behave identically on every
surface.
"""
import mlflow

from app.agents.responses_agent import AtlasResponsesAgent

# The default tool set (AGENT_TOOLS) is bound inside AtlasResponsesAgent.__init__.
mlflow.models.set_model(AtlasResponsesAgent())
