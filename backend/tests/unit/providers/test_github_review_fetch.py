"""GitHub provider calls used by the app code review: repo lookup, ref -> SHA, tarball."""
import httpx
import pytest

from app.core.exceptions import PermanentError
from app.providers.github.client import GitHubProvider

SHA = "c" * 40


def _provider(handler) -> GitHubProvider:
    provider = GitHubProvider(token="t", org="acme")
    provider.client = httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )
    return provider


@pytest.mark.asyncio
async def test_get_repo_says_missing_or_unreadable_on_404():
    provider = _provider(lambda req: httpx.Response(404))
    with pytest.raises(PermanentError, match="can't read it"):
        await provider.get_repo("sales-app")


@pytest.mark.asyncio
async def test_resolve_commit_sha_returns_none_for_an_unknown_ref():
    def handler(req):
        if req.url.path.endswith("/commits/main"):
            assert req.headers["accept"] == "application/vnd.github.sha"
            return httpx.Response(200, text=SHA)
        return httpx.Response(422)

    provider = _provider(handler)
    assert await provider.resolve_commit_sha("acme/sales-app", "main") == SHA
    assert await provider.resolve_commit_sha("acme/sales-app", "nope") is None


@pytest.mark.asyncio
async def test_download_tarball_follows_the_codeload_redirect():
    def handler(req):
        if req.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": "https://codeload.github.com/acme/x/tar.gz/c"})
        return httpx.Response(200, content=b"tarball-bytes")

    provider = _provider(handler)
    assert await provider.download_tarball("acme/x", SHA, max_bytes=1000) == b"tarball-bytes"


@pytest.mark.asyncio
async def test_download_tarball_stops_at_the_size_cap():
    provider = _provider(lambda req: httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(PermanentError, match="review limit"):
        await provider.download_tarball("acme/x", SHA, max_bytes=1000)


@pytest.mark.asyncio
async def test_get_repo_names_a_bad_token_and_sso():
    with pytest.raises(PermanentError, match="expired or revoked"):
        await _provider(lambda req: httpx.Response(401)).get_repo("acme/x")
    with pytest.raises(PermanentError, match="SSO"):
        await _provider(lambda req: httpx.Response(403)).get_repo("acme/x")
