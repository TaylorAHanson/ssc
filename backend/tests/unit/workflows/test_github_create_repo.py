"""github_create_repo: placeholder template values mean "no template".

The agent sent template="empty" for an empty repo; the tool generated from a
repo literally named "empty", GitHub 404'd, and the request failed.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workflows.tools import github_create_repo


def _provider():
    provider = MagicMock()
    provider.create_repo = AsyncMock(return_value={"name": "r"})
    provider.create_from_template = AsyncMock(return_value={"name": "r"})
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("template", [None, "", "empty", "None", " no template ", "N/A"])
async def test_placeholder_templates_create_an_empty_repo(template):
    provider = _provider()
    with patch("app.workflows.tools._common._get_github_provider", return_value=provider):
        await github_create_repo.execute(repo_name="r", template=template, visibility="private")
    provider.create_repo.assert_awaited_once_with("r", {"private": True})
    provider.create_from_template.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_real_template_is_still_used():
    provider = _provider()
    with patch("app.workflows.tools._common._get_github_provider", return_value=provider):
        await github_create_repo.execute(repo_name="r", template=" data-engineering ")
    provider.create_from_template.assert_awaited_once_with("data-engineering", "r", {})
    provider.create_repo.assert_not_awaited()
