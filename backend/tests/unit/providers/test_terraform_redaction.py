"""Git errors from the Terraform provider never carry the PAT.

GitPython echoes the failing command line, which includes the token-bearing
clone URL; those messages are logged and stored as the request's last_error.
"""

from unittest.mock import patch

import git
import pytest

from app.core.exceptions import RetryableError
from app.providers.terraform.client import TerraformProvider

TOKEN = "ghp_SECRET123"


def _provider(tmp_path):
    return TerraformProvider(
        "https://github.com/org/infra.git",
        config={"git_token": TOKEN, "local_repo_path": str(tmp_path / "repo")},
    )


def test_redact_masks_token_and_url_credentials(tmp_path):
    p = _provider(tmp_path)
    msg = f"Cmd('git') failed: git clone https://x-access-token:{TOKEN}@github.com/org/infra.git"

    out = p._redact(msg)

    assert TOKEN not in out
    assert "https://***@github.com/org/infra.git" in out


def test_redact_masks_other_embedded_credentials(tmp_path):
    p = _provider(tmp_path)
    assert p._redact("fatal: https://user:pw@host/repo") == "fatal: https://***@host/repo"
    assert p._redact("plain error, no url") == "plain error, no url"


def test_clone_failure_error_does_not_leak_token(tmp_path):
    p = _provider(tmp_path)
    auth_url = p._get_authenticated_url()
    assert TOKEN in auth_url  # precondition: the clone URL really carries it

    err = git.GitCommandError(["git", "clone", auth_url], 128, stderr=f"fatal: could not read {auth_url}")
    with patch("app.providers.terraform.client.git.Repo.clone_from", side_effect=err):
        with pytest.raises(RetryableError) as exc_info:
            p._prepare_repo()

    assert TOKEN not in str(exc_info.value)
    assert "***@github.com" in str(exc_info.value)
