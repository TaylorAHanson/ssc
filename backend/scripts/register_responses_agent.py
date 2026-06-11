"""Register (and optionally deploy) the Atlas ResponsesAgent to Model Serving.

This is a **workspace-run** operation: it logs the agent as an MLflow model
(models-from-code via ``app/agents/agent_entry.py``), registers it to Unity
Catalog, and — with ``--deploy`` — provisions a Model Serving endpoint through
the Databricks Agent Framework (``databricks.agents.deploy``).

It needs a live Databricks workspace and credentials, plus the deploy-time deps
``mlflow`` (full, not -skinny) and ``databricks-agents`` which are NOT part of
the app's runtime requirements. Run it from a workspace notebook or an
authenticated shell:

    pip install "mlflow>=3.1" databricks-agents
    python -m scripts.register_responses_agent \
        --uc-model-name main.atlas.self_service_agent \
        --llm-endpoint databricks-gpt-5-4-mini \
        --experiment /Shared/atlas-agent \
        --deploy

Why this is decoupled from the app: per current DABs limitations the new Unity
AI Gateway serving objects can't be declared in a bundle, so the endpoint is
created here against the workspace API rather than in ``databricks.yml``. The
running app routes through the gateway purely via ``AI_GATEWAY_ENDPOINT`` (see
``AgentLLMClient``) and needs no code change to adopt it.
"""
import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("register_responses_agent")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
_ENTRY = os.path.join(_BACKEND_DIR, "app", "agents", "agent_entry.py")
_REQUIREMENTS = os.path.join(_BACKEND_DIR, "requirements.txt")


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uc-model-name", required=True,
                   help="Unity Catalog model name: <catalog>.<schema>.<model>")
    p.add_argument("--llm-endpoint", default=os.environ.get("MODEL_SERVING_AGENT_LLM_ENDPOINT", ""),
                   help="LLM serving endpoint the agent calls (added as a model resource for auth).")
    p.add_argument("--gateway-endpoint", default=os.environ.get("AI_GATEWAY_ENDPOINT", ""),
                   help="Optional AI Gateway endpoint (added as a resource when set).")
    p.add_argument("--experiment", default=os.environ.get("MLFLOW_EXPERIMENT", ""),
                   help="MLflow experiment path for the logging run (optional).")
    p.add_argument("--deploy", action="store_true",
                   help="After registering, deploy to Model Serving via databricks-agents.")
    p.add_argument("--scale-to-zero", action="store_true",
                   help="Deploy with scale-to-zero enabled (cost-friendly for dev).")
    return p.parse_args(argv)


def _resources(llm_endpoint: str, gateway_endpoint: str):
    """Serving-endpoint resource deps so the deployed agent gets auth passthrough."""
    from mlflow.models.resources import DatabricksServingEndpoint

    res = []
    for name in (llm_endpoint, gateway_endpoint):
        if name and name.strip():
            res.append(DatabricksServingEndpoint(endpoint_name=name.strip()))
    return res


def _input_example():
    return {
        "input": [{"role": "user", "content": "What access can I request?"}],
    }


def register(args) -> str:
    """Log + register the agent; return the UC model version (as a string)."""
    try:
        import mlflow
    except ImportError:
        logger.error("mlflow is required. Install with: pip install 'mlflow>=3.1'")
        raise

    if not os.path.exists(_ENTRY):
        raise FileNotFoundError(f"agent entry point not found: {_ENTRY}")

    mlflow.set_registry_uri("databricks-uc")
    if args.experiment:
        mlflow.set_experiment(args.experiment)

    pip_reqs = ["-r", _REQUIREMENTS] if os.path.exists(_REQUIREMENTS) else None
    resources = _resources(args.llm_endpoint, args.gateway_endpoint)
    if not resources:
        logger.warning(
            "No serving-endpoint resources resolved (set --llm-endpoint). The "
            "deployed agent may lack auth passthrough to its LLM endpoint."
        )

    logger.info("Logging agent (models-from-code) from %s ...", _ENTRY)
    with mlflow.start_run(run_name="atlas-responses-agent"):
        logged = mlflow.pyfunc.log_model(
            name="agent",
            python_model=_ENTRY,
            input_example=_input_example(),
            resources=resources,
            pip_requirements=pip_reqs,
        )

    logger.info("Registering %s -> %s", logged.model_uri, args.uc_model_name)
    mv = mlflow.register_model(logged.model_uri, args.uc_model_name)
    logger.info("Registered %s version %s", args.uc_model_name, mv.version)
    return str(mv.version)


def deploy(args, version: str) -> None:
    try:
        from databricks import agents
    except ImportError:
        logger.error(
            "databricks-agents is required for --deploy. Install with: "
            "pip install databricks-agents"
        )
        raise

    logger.info("Deploying %s v%s to Model Serving ...", args.uc_model_name, version)
    agents.deploy(
        args.uc_model_name,
        int(version),
        scale_to_zero=args.scale_to_zero,
    )
    logger.info(
        "Deploy requested. The endpoint may take several minutes to become ready; "
        "check Serving in the workspace UI."
    )


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.uc_model_name.count(".") != 2:
        logger.error("--uc-model-name must be fully qualified: <catalog>.<schema>.<model>")
        return 2
    version = register(args)
    if args.deploy:
        deploy(args, version)
    else:
        logger.info(
            "Skipped deploy (pass --deploy). To route the running app through a "
            "gateway endpoint, set AI_GATEWAY_ENDPOINT — no app code change needed."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
