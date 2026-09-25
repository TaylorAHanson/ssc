"""Entry point: resolve -> fetch -> pre-scan -> LLM review -> report."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from app.services.app_code_review.prescan import run_prescan
from app.services.app_code_review.report import build_result
from app.services.app_code_review.reviewer import run_reviewer
from app.services.app_code_review.snapshot import extract_snapshot
from app.services.app_code_review.source import resolve_source

logger = logging.getLogger(__name__)


async def review_app_code(
    github,
    repo: str,
    *,
    ref: Optional[str] = None,
    app_path: Optional[str] = None,
    llm=None,
    settings=None,
) -> Dict[str, Any]:
    """Review a Databricks App's source and return the structured result.

    ``github`` is a ``GitHubProvider``; ``llm`` an ``AgentLLMClient`` (built on
    demand when omitted). Resolution and download failures raise
    ``PermanentError`` / ``RetryableError``; a reviewer-model failure does not.
    It degrades to a report from the automated checks alone, so the approver
    still gets something to decide on.
    """
    if settings is None:
        from app.core.config import settings as app_settings
        settings = app_settings

    started = time.monotonic()
    web_base = settings.GITHUB_WEB_BASE_URL or "https://github.com"
    source = await resolve_source(github, repo, ref=ref, app_path=app_path, web_base_url=web_base)
    logger.info("app code review: %s at %s (%s)", source.full_name, source.sha, source.subpath or "root")

    archive = await github.download_tarball(
        source.full_name, source.sha, max_bytes=settings.APP_CODE_REVIEW_MAX_ARCHIVE_MB * 1024 * 1024,
    )
    snapshot = await asyncio.to_thread(
        extract_snapshot,
        archive,
        subpath=source.subpath,
        max_total_bytes=settings.APP_CODE_REVIEW_MAX_TOTAL_KB * 1024,
        max_file_bytes=settings.APP_CODE_REVIEW_MAX_FILE_KB * 1024,
    )
    del archive
    scan = await asyncio.to_thread(run_prescan, snapshot.files)

    verdict = None
    try:
        if llm is None:
            from app.model_serving.agent_llm import AgentLLMClient
            model = (settings.APP_CODE_REVIEW_MODEL or "").strip()
            llm = (
                AgentLLMClient(model=model, reasoning_effort=settings.APP_CODE_REVIEW_REASONING_EFFORT or "")
                if model else AgentLLMClient()
            )
        verdict = await run_reviewer(
            llm, source, snapshot, scan,
            rubric=settings.APP_CODE_REVIEW_RUBRIC,
            max_turns=settings.APP_CODE_REVIEW_MAX_TURNS,
            # The limit covers the whole step, so time spent downloading counts.
            time_limit_seconds=max(
                0.0, settings.APP_CODE_REVIEW_TIME_LIMIT_SECONDS - (time.monotonic() - started)
            ),
        )
    except Exception:  # noqa: BLE001 - any model failure degrades, never fails the step
        logger.exception("app code review: reviewer failed for %s@%s", source.full_name, source.sha)

    result = build_result(source, snapshot, scan, verdict, web_base=web_base)
    logger.info(
        "app code review: %s@%s -> %s (%.2f, %s)", source.full_name, source.sha[:7],
        result["recommendation"], result["confidence"], result["data_access_identity"],
    )
    return result
