"""
Scheduled LLM-as-judge quality job (Databricks best practice).

Runs on a schedule (see the ``agent_quality_judge`` job in ``databricks.yml``),
pulls recent agent traces from the configured MLflow experiment, scores each
turn with an LLM judge (helpfulness + groundedness), and writes the score back
to the trace as an ``LLM_JUDGE`` assessment keyed by ``trace_id``. Paired with
the human feedback captured via ``POST /api/v1/agent/feedback``, this gives a
judge-vs-human agreement signal and a continuous quality dashboard over the
inference table.

Run locally for a dry run:
    python -m app.jobs.llm_judge --limit 20 --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """You are evaluating an AI assistant turn for a self-service \
data platform. Given the user request and the assistant's final answer plus the \
tools it called, rate the answer.

Return ONLY compact JSON:
{{"helpfulness": <1-5>, "groundedness": <1-5>, "rationale": "<one sentence>"}}

USER REQUEST:
{request}

ASSISTANT ANSWER:
{response}

TOOLS CALLED:
{tools}
"""


def _judge_one(client, endpoint: str, request_text: str, response_text: str,
               tools_text: str) -> dict:
    prompt = _JUDGE_PROMPT.format(
        request=request_text[:4000], response=response_text[:4000],
        tools=tools_text[:2000],
    )
    import asyncio

    result = asyncio.run(
        client.invoke_endpoint(
            endpoint,
            {"messages": [{"role": "user", "content": prompt}],
             "temperature": 0.0, "max_tokens": 300},
            use_foundation_model_format=True,
        )
    )
    content = ""
    if isinstance(result, dict):
        msg = result.get("message") or {}
        content = msg.get("content") or result.get("content") or ""
    try:
        start, end = content.find("{"), content.rfind("}")
        return json.loads(content[start:end + 1])
    except Exception:  # noqa: BLE001
        return {"helpfulness": None, "groundedness": None, "rationale": "unparseable"}


def run(limit: int = 50, dry_run: bool = False) -> int:
    import mlflow

    from app.agents.tracing import init_tracing
    from app.core.config import settings
    from app.model_serving.client import ModelServingClient

    init_tracing()
    endpoint = settings.AI_GATEWAY_ENDPOINT or settings.MODEL_SERVING_AGENT_LLM_ENDPOINT
    if settings.MLFLOW_EXPERIMENT:
        mlflow.set_experiment(settings.MLFLOW_EXPERIMENT)

    traces = mlflow.search_traces(max_results=limit, order_by=["timestamp DESC"])
    logger.info("LLM judge: scoring %d traces", len(traces))

    client = ModelServingClient()
    scored = 0
    for _, row in traces.iterrows():
        trace_id = row.get("trace_id") or row.get("request_id")
        request_text = json.dumps(row.get("request", ""), default=str)
        response_text = json.dumps(row.get("response", ""), default=str)
        verdict = _judge_one(client, endpoint, request_text, response_text, "")
        logger.info("trace=%s verdict=%s", trace_id, verdict)
        if dry_run:
            continue
        try:
            mlflow.log_feedback(
                trace_id=trace_id,
                name="llm_judge",
                value=verdict,
                source=mlflow.entities.AssessmentSource(
                    source_type="LLM_JUDGE", source_id=endpoint
                ),
                rationale=verdict.get("rationale"),
            )
            scored += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to log judge feedback for %s: %s", trace_id, e)
    return scored


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="LLM-as-judge agent quality job")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    n = run(limit=args.limit, dry_run=args.dry_run)
    logger.info("LLM judge complete: %d traces scored", n)


if __name__ == "__main__":
    main()
