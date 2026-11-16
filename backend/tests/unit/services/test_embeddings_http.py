"""The embedding HTTP client: request shape, response parsing, error mapping, caching and the breaker.

Runs against httpx's mock transport, so nothing leaves the process and no credential is read.
"""
import httpx
import pytest

from app.services import embeddings

HOST = "https://example.cloud.databricks.com"
MODEL = "databricks-gte-large-en"          # a bare serving-endpoint name: called directly
GATEWAY_MODEL = "system.ai.gte-large-en"   # a Unity Catalog reference: called through the AI Gateway
DIMS = embeddings.DIMS


def _vec(x):
    return [x] + [0.0] * (DIMS - 1)


@pytest.fixture
def wire(monkeypatch):
    """Route the client's requests to ``wire.handler`` and skip credential lookup."""
    class Wire:
        handler = None
        requests = []

    w = Wire()
    w.requests = []

    def _handle(request):
        w.requests.append(request)
        return w.handler(request)

    monkeypatch.setattr(embeddings, "_client", httpx.Client(transport=httpx.MockTransport(_handle)))
    monkeypatch.setattr(embeddings, "_bearer", lambda host: "test-token")
    embeddings.reset_breaker()
    embeddings._query_cache.clear()
    yield w
    embeddings._query_cache.clear()
    embeddings.reset_breaker()


def _ok(vectors):
    return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]})


def test_request_shape(wire):
    wire.handler = lambda r: _ok([_vec(1.0)])
    embeddings.HttpEmbedder(host=HOST, model=MODEL).embed(["x" * 9000], timeout=5)

    (req,) = wire.requests
    assert str(req.url) == f"{HOST}/serving-endpoints/{MODEL}/invocations"
    assert req.headers["Authorization"] == "Bearer test-token"
    body = __import__("json").loads(req.content)
    assert list(body) == ["input"] and len(body["input"][0]) == embeddings.MAX_INPUT_CHARS


def test_a_unity_catalog_model_reference_goes_through_the_ai_gateway(wire):
    wire.handler = lambda r: _ok([_vec(1.0)])
    embeddings.HttpEmbedder(host=HOST, model=GATEWAY_MODEL).embed(["hello"], timeout=5)

    (req,) = wire.requests
    assert str(req.url) == f"{HOST}/ai-gateway/mlflow/v1/embeddings"
    assert req.headers["Authorization"] == "Bearer test-token"
    assert __import__("json").loads(req.content) == {"model": GATEWAY_MODEL, "input": ["hello"]}


def test_the_route_is_chosen_by_the_model_name():
    assert embeddings.uses_gateway("system.ai.gte-large-en") and embeddings.uses_gateway("main.models.my-embedder")
    assert not embeddings.uses_gateway("databricks-gte-large-en") and not embeddings.uses_gateway("my_endpoint")


def test_results_are_returned_in_input_order_even_if_the_endpoint_shuffles_them(wire):
    wire.handler = lambda r: httpx.Response(200, json={"data": [
        {"index": 1, "embedding": _vec(2.0)}, {"index": 0, "embedding": _vec(1.0)}]})
    out = embeddings.HttpEmbedder(host=HOST, model=MODEL).embed(["a", "b"], timeout=5)
    assert [v[0] for v in out] == [1.0, 2.0]


@pytest.mark.parametrize("status, needle, per_input", [
    (401, "can't use the embedding model", False), (403, "no longer available", False),
    (404, "wasn't found", False),
    (429, "busy", False), (400, "couldn't process", True), (413, "couldn't process", True),
    (503, "returned an error", False),
])
def test_status_codes_map_to_safe_messages(wire, status, needle, per_input):
    wire.handler = lambda r: httpx.Response(status, text="secret internal detail")
    with pytest.raises(embeddings.EmbeddingError) as exc:
        embeddings.HttpEmbedder(host=HOST, model=MODEL).embed(["a"], timeout=5)
    assert needle in exc.value.message and exc.value.per_input is per_input
    assert "secret internal detail" not in exc.value.message and exc.value.status == status


@pytest.mark.parametrize("payload", [{"nope": 1}, {"data": "x"}, {"data": [{"index": 0}]}])
def test_malformed_responses_are_rejected(wire, payload):
    wire.handler = lambda r: httpx.Response(200, json=payload)
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.HttpEmbedder(host=HOST, model=MODEL).embed(["a"], timeout=5)


def test_network_failures_become_embedding_errors(wire):
    def boom(request):
        raise httpx.ConnectError("no route")

    wire.handler = boom
    with pytest.raises(embeddings.EmbeddingError, match="Couldn't reach"):
        embeddings.HttpEmbedder(host=HOST, model=MODEL).embed(["a"], timeout=5)

    def slow(request):
        raise httpx.ReadTimeout("slow")

    wire.handler = slow
    with pytest.raises(embeddings.EmbeddingError, match="timed out"):
        embeddings.HttpEmbedder(host=HOST, model=MODEL).embed(["a"], timeout=5)


def test_query_vectors_are_cached(wire, monkeypatch):
    monkeypatch.setattr(embeddings, "get_embedder", lambda m: embeddings.HttpEmbedder(host=HOST, model=m))
    wire.handler = lambda r: _ok([_vec(1.0)])

    first = embeddings.query_vector(MODEL, "hello")
    second = embeddings.query_vector(MODEL, "hello")

    assert first == second and first.startswith("[1,") and len(wire.requests) == 1


def test_a_failing_endpoint_trips_the_breaker_then_recovers(wire, monkeypatch):
    monkeypatch.setattr(embeddings, "get_embedder", lambda m: embeddings.HttpEmbedder(host=HOST, model=m))
    wire.handler = lambda r: httpx.Response(503)

    assert embeddings.query_vector(MODEL, "one") is None
    assert embeddings.query_vector(MODEL, "two") is None
    assert len(wire.requests) == 1, "the second query must not call the failing endpoint"

    wire.handler = lambda r: _ok([_vec(1.0)])
    embeddings.reset_breaker()
    assert embeddings.query_vector(MODEL, "two") is not None


def test_no_workspace_or_a_bad_model_name_means_not_configured(monkeypatch):
    monkeypatch.setattr(embeddings, "_factory", None)
    monkeypatch.setattr(embeddings, "_base_url", lambda: "")
    assert embeddings.get_embedder(MODEL) is None
    monkeypatch.setattr(embeddings, "_base_url", lambda: HOST)
    assert embeddings.get_embedder("bad name!") is None and embeddings.get_embedder("") is None
    assert embeddings.get_embedder(MODEL).host == HOST and embeddings.get_embedder(GATEWAY_MODEL).host == HOST
