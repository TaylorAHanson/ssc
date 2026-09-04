"""
Job execution, allowlist exception updates, and reporting workflow tools.
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.workflows.tools import _common

logger = logging.getLogger(__name__)


@tool(
    name="run_notebook_job",
    side_effect_class="infra",
    description="Run a Databricks notebook job and return its output.",
)
async def run_notebook_job(**kwargs) -> Dict[str, Any]:
    provider = _common._get_databricks_provider()
    result = await provider.submit_job(kwargs.get("notebook_path"), kwargs.get("parameters", {}))
    return {"job_result": result}


class SpawnChildInput(BaseModel):
    child_type: str = Field(..., description="RequestType value of the child workflow")
    parameters: Dict[str, Any] = Field(default_factory=dict)


@tool(
    name="spawn_child_request",
    args_schema=SpawnChildInput,
    side_effect_class="app_write",
    description="[DEPRECATED] Create a child request workflow (orchestrator pattern). Use a compound workflow (a 'subworkflow' stage) instead.",
)
async def spawn_child_request(
    child_type: str,
    parameters: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    logger.warning("spawn_child_request is deprecated; use a compound workflow (subworkflow stage). type=%s", child_type)
    return {"spawned": child_type, "parameters": parameters or {}}


@tool(
    name="update_allowlist",
    side_effect_class="data_grant",
    description="Record an approved governance allowlist exception (policy reprieve).",
)
async def update_allowlist(**kwargs) -> Dict[str, Any]:
    """Persist an approved allowlist exception so the Sentinel grants a reprieve."""
    import uuid
    from datetime import datetime

    request_id = kwargs.get("_request_id")
    db, request = _common._load_request(request_id)
    if request is None:
        logger.warning("update_allowlist: no request found for id=%s; nothing recorded", request_id)
        return {"allowlist_updated": False, "resource_id": kwargs.get("resource_id")}

    try:
        from app.db.allowlist import AllowlistModel

        ctx = request.state_context or {}
        resource_id = kwargs.get("resource_id") or ctx.get("resource_id")
        if not resource_id:
            logger.warning("update_allowlist: missing resource_id (request=%s); nothing recorded", request_id)
            return {"allowlist_updated": False, "resource_id": None}

        justification = kwargs.get("justification") or ctx.get("justification") or ""
        resource_type = kwargs.get("resource_type") or ctx.get("resource_type") or "unknown"
        workspace = kwargs.get("workspace") or ctx.get("workspace") or ""
        approved_by = ctx.get("approved_by") or kwargs.get("_user_email")

        expires_at = None
        raw_expiry = kwargs.get("expires_at") or ctx.get("expires_at")
        if raw_expiry:
            try:
                expires_at = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
            except ValueError:
                logger.warning("update_allowlist: bad expires_at %r; ignoring", raw_expiry)

        entry = (
            db.query(AllowlistModel)
            .filter(AllowlistModel.request_id == request_id)
            .first()
        )
        if entry is None:
            entry = AllowlistModel(
                id=str(uuid.uuid4()),
                resource_id=resource_id,
                resource_type=resource_type,
                workspace=workspace,
                justification=justification,
                request_id=request_id,
            )
            db.add(entry)
        entry.status = "approved"
        entry.approved_by = approved_by
        if expires_at is not None:
            entry.expires_at = expires_at
        if justification:
            entry.justification = justification
        db.commit()

        logger.info(
            "update_allowlist: approved exception resource=%s workspace=%s request=%s",
            resource_id,
            workspace,
            request_id,
        )
        return {"allowlist_updated": True, "allowlist_id": entry.id, "resource_id": resource_id}
    finally:
        db.close()


@tool(
    name="execute_report",
    side_effect_class="read",
    description="Run report prompts via the agent and assemble the report body.",
)
async def execute_report(**kwargs) -> Dict[str, Any]:
    """Run each configured report prompt through the agent and assemble HTML."""
    if "_report" in kwargs:
        report = kwargs.get("_report", "")
        return {"report": report, "body": report, "subject": "Report"}

    from datetime import datetime
    from zoneinfo import ZoneInfo

    request_id = kwargs.get("_request_id")
    db, request = _common._load_request(request_id)
    ctx = (request.state_context or {}) if request is not None else {}
    prompts = kwargs.get("prompts") or ctx.get("prompts") or []
    report_name = ctx.get("name", "Report")
    tz = ZoneInfo("America/Los_Angeles")

    try:
        if not prompts:
            logger.warning("execute_report: no prompts configured (request=%s)", request_id)
            body = "<p>No report prompts were configured.</p>"
            return {"report": body, "body": body, "subject": f"Report: {report_name}"}

        from app.agents.runner import AgentRunner
        from app.tools import get_read_only_tools

        system_prompt = (
            "You are a specialized read-only reporting assistant. "
            "Your goal is to fetch real data using your tools and present it clearly. "
            "Always return the final result as a clean HTML snippet (e.g. <table>, <ul>, <p>). "
            "Do not include <html> or <body> tags. "
            "If you cannot find data, state that clearly instead of making it up. "
            f"The current time is {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S %Z')}."
        )
        runner = AgentRunner(system_prompt=system_prompt, tools=get_read_only_tools())

        results: List[Dict[str, str]] = []
        for p in prompts:
            if isinstance(p, str):
                p = {"label": report_name, "prompt": p}
            elif not isinstance(p, dict):
                logger.warning("execute_report: skipping malformed prompt %r (request=%s)", p, request_id)
                continue
            label = p.get("label", "Untitled")
            prompt_text = p.get("prompt", "")
            logger.info("execute_report: running prompt '%s' (request=%s)", label, request_id)
            response = await runner.run(query=prompt_text)
            content = (response.get("content", "") or "").replace("```html", "").replace("```", "").strip()
            results.append({"label": label, "html": content})

        generated_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        sections = "".join(
            f'<div class="report-section" style="margin-bottom: 2rem;">'
            f'<h3 style="color: #444; margin-bottom: 0.5rem;">{r["label"]}</h3>'
            f'<div class="section-content">{r["html"]}</div></div>'
            for r in results
        )
        body = (
            f'<div class="report-header"><h2 style="margin-top: 0;">{report_name}</h2>'
            f'<p style="color: #666; font-size: 0.9rem;">Generated at: {generated_at}</p></div>'
            f'<hr style="border: 0; border-top: 1px solid #eee; margin: 1.5rem 0;" />{sections}'
        )
        subject = f"Report: {report_name}"

        if request is not None:
            from sqlalchemy.orm.attributes import flag_modified

            updated = dict(request.state_context or {})
            updated.update(
                {
                    "report_results": results,
                    "final_report_html": body,
                    "body": body,
                    "subject": subject,
                }
            )
            request.state_context = updated
            flag_modified(request, "state_context")
            db.add(request)
            db.commit()

        logger.info("execute_report: assembled %d section(s) (request=%s)", len(results), request_id)
        return {"report": body, "body": body, "subject": subject, "report_results": results}
    finally:
        if db is not None:
            db.close()
