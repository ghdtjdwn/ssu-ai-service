from fastapi.testclient import TestClient

import app.main as main
from app.main import app


class _FakeResponse:
    """Minimal stand-in for an httpx.Response from the upstream embeddings API."""

    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Async context manager standing in for the shared httpx.AsyncClient the
    app opens in its lifespan. Returns a preset response from .post()."""

    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        return self._response


def _patch_upstream(monkeypatch, response: _FakeResponse):
    monkeypatch.setattr(main, "SERVICE_API_KEY", "inbound-key")
    monkeypatch.setattr(main, "GEMINI_API_KEY", "upstream-key")
    # The lifespan builds the shared client via httpx.AsyncClient(...); patching
    # the constructor makes it yield the fake, exercising the real app.state wiring.
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(response))


def test_health_endpoint():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_embeddings_requires_inbound_api_key():
    # No SSUAI_SERVICE_API_KEY configured (CI default) => gate fails closed with 401,
    # never falling through to an unauthenticated upstream call.
    with TestClient(app) as client:
        response = client.post("/v1/embeddings", json={"text": "숭실대학교 정보 검색 테스트"})
    assert response.status_code == 401


def test_embeddings_authed_but_upstream_unconfigured(monkeypatch):
    # Inbound auth passes (matching key) but no upstream credential => 503, and the
    # response carries a generic message, not provider internals.
    monkeypatch.setattr(main, "SERVICE_API_KEY", "test-inbound-key")
    monkeypatch.setattr(main, "GEMINI_API_KEY", "")
    with TestClient(app) as client:
        response = client.post(
            "/v1/embeddings",
            json={"text": "test"},
            headers={"X-API-Key": "test-inbound-key"},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == "embedding upstream not configured"


def test_embeddings_wrong_key_rejected(monkeypatch):
    monkeypatch.setattr(main, "SERVICE_API_KEY", "correct-key")
    with TestClient(app) as client:
        response = client.post(
            "/v1/embeddings",
            json={"text": "test"},
            headers={"X-API-Key": "wrong-key"},
        )
    assert response.status_code == 401


def test_embeddings_happy_path_returns_capped_vector(monkeypatch):
    # Inbound auth + upstream both configured; upstream returns a long vector that the
    # gateway caps to EMBEDDING_DIM before returning.
    long_vector = [0.01 * i for i in range(main.EMBEDDING_DIM + 256)]
    _patch_upstream(monkeypatch, _FakeResponse(200, {"data": [{"embedding": long_vector}]}))
    with TestClient(app) as client:
        response = client.post(
            "/v1/embeddings",
            json={"text": "숭실대학교 학사 일정"},
            headers={"X-API-Key": "inbound-key"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["dimension"] == main.EMBEDDING_DIM
    assert len(body["embedding"]) == main.EMBEDDING_DIM


def test_embeddings_malformed_upstream_returns_generic_502(monkeypatch):
    # Upstream replies 200 but with an unexpected shape => generic 502, no body reflected.
    _patch_upstream(monkeypatch, _FakeResponse(200, {"unexpected": "shape"}, text="provider internals"))
    with TestClient(app) as client:
        response = client.post(
            "/v1/embeddings",
            json={"text": "test"},
            headers={"X-API-Key": "inbound-key"},
        )
    assert response.status_code == 502
    assert response.json()["detail"] == "embedding upstream error"
    assert "provider internals" not in response.text
