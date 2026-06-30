"""GitHub App authentication helpers.

A GitHub App authenticates by signing a short-lived JWT with its private key,
then exchanging that JWT for an **installation access token** (valid ~1 hour)
scoped to the App's installation permissions. That installation token is what
both the REST provider (repo create/template/PR/etc.) and the GitOps git
clone/push use — there is no personal access token anywhere in the system.

This module centralizes the minting so the GitHub REST provider and the
Terraform/GitOps provider share one implementation.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

import requests

from app.core.exceptions import PermanentError, RetryableError

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

#: Installation tokens are valid for one hour.
_TOKEN_TTL_SECONDS = 3600


def _app_jwt(app_id: str, private_key: str) -> str:
    """Build a signed App JWT (RS256), valid for ~10 minutes."""
    try:
        import jwt
    except ImportError:  # pragma: no cover - dependency is declared
        logger.error("PyJWT not installed. Run: pip install PyJWT")
        raise RetryableError("PyJWT library not available")

    now = int(time.time())
    payload = {
        "iat": now - 60,        # backdate 60s for clock skew
        "exp": now + (10 * 60),  # max 10 minutes per GitHub
        "iss": app_id,
    }
    try:
        return jwt.encode(payload, private_key, algorithm="RS256")
    except Exception as e:  # noqa: BLE001
        key_preview = private_key[:50] if private_key else ""
        logger.error(
            "Failed to sign GitHub App JWT: %s (key_len=%s, has_newlines=%s, "
            "has_escaped_newlines=%s, starts_with=%r)",
            e, len(private_key or ""), "\n" in (private_key or ""),
            "\\n" in (private_key or ""), key_preview,
        )
        raise RetryableError(f"GitHub App JWT generation failed: {e}")


def generate_github_app_token(
    app_id: str, private_key: str, installation_id: Optional[str] = None
) -> str:
    """Mint a GitHub App installation access token.

    If ``installation_id`` is omitted, the App's first installation is used.
    Returns the raw token string. Raises ``PermanentError`` /
    ``RetryableError`` on failure.
    """
    token, _expires_at = generate_github_app_token_with_expiry(
        app_id, private_key, installation_id
    )
    return token


def generate_github_app_token_with_expiry(
    app_id: str, private_key: str, installation_id: Optional[str] = None
) -> Tuple[str, float]:
    """Like :func:`generate_github_app_token` but also returns the epoch expiry.

    The expiry is computed locally (now + 1h) rather than parsed from the API
    response, which is sufficient for cache-refresh decisions.
    """
    if not app_id or not private_key:
        raise PermanentError(
            "GitHub App not configured: GITHUB_APP_ID and a private key are required."
        )

    encoded_jwt = _app_jwt(app_id, private_key)
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if not installation_id:
        resp = requests.get(f"{GITHUB_API}/app/installations", headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.error("Failed to list GitHub App installations: %s", resp.text)
            raise RetryableError(
                f"Failed to list GitHub App installations: {resp.status_code}"
            )
        installations = resp.json()
        if not installations:
            raise PermanentError("GitHub App has no installations.")
        installation_id = installations[0]["id"]
        logger.info("Using GitHub App installation ID: %s", installation_id)

    resp = requests.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers=headers,
        timeout=30,
    )
    if resp.status_code != 201:
        logger.error("Failed to mint installation token: %s", resp.text)
        raise RetryableError(f"Failed to mint installation token: {resp.status_code}")

    return resp.json()["token"], time.time() + _TOKEN_TTL_SECONDS
