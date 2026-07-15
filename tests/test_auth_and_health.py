import asyncio

import pytest
from fastapi import HTTPException
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
        self.post_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        self.post_calls += 1
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
    assert "gemini_configured" in response.json()


def test_readiness_requires_both_credentials_without_calling_upstream(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(200))
    monkeypatch.setattr(main, "SERVICE_API_KEY", "")
    monkeypatch.setattr(main, "GEMINI_API_KEY", "")
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: fake_client)

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "service not ready"}
    assert fake_client.post_calls == 0


def test_readiness_accepts_valid_local_configuration_without_upstream_probe(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(500))
    monkeypatch.setattr(main, "SERVICE_API_KEY", "inbound-key")
    monkeypatch.setattr(main, "GEMINI_API_KEY", "upstream-key")
    monkeypatch.setattr(main, "CONFIG_ERRORS", ())
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: fake_client)

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert fake_client.post_calls == 0


def test_readiness_rejects_invalid_usage_limit_configuration(monkeypatch):
    monkeypatch.setattr(main, "SERVICE_API_KEY", "inbound-key")
    monkeypatch.setattr(main, "GEMINI_API_KEY", "upstream-key")
    monkeypatch.setattr(main, "CONFIG_ERRORS", ("SSUAI_RATE_LIMIT_REQUESTS",))

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503


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


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_embeddings_rejects_empty_text(monkeypatch, text):
    _patch_upstream(monkeypatch, _FakeResponse(200, {"data": [{"embedding": [1.0]}]}))
    with TestClient(app) as client:
        response = client.post(
            "/v1/embeddings",
            json={"text": text},
            headers={"X-API-Key": "inbound-key"},
        )
    assert response.status_code == 422


def test_embeddings_rejects_text_over_configured_maximum(monkeypatch):
    monkeypatch.setattr(main, "MAX_TEXT_LENGTH", 4)
    _patch_upstream(monkeypatch, _FakeResponse(200, {"data": [{"embedding": [1.0]}]}))
    with TestClient(app) as client:
        response = client.post(
            "/v1/embeddings",
            json={"text": "12345"},
            headers={"X-API-Key": "inbound-key"},
        )
    assert response.status_code == 422


def test_embeddings_non_ascii_key_rejected_cleanly(monkeypatch):
    # A non-ASCII X-API-Key (latin-1 decoded by Starlette) must fail closed with 401,
    # not raise a TypeError inside the constant-time compare and surface a 500.
    monkeypatch.setattr(main, "SERVICE_API_KEY", "correct-key")
    with TestClient(app) as client:
        response = client.post(
            "/v1/embeddings",
            json={"text": "test"},
            # Raw latin-1 bytes: a real client can put byte 0xF8 in the header, which
            # Starlette decodes back to the non-ASCII str "wrøng-key" server-side.
            headers={"X-API-Key": "wrøng-key".encode("latin-1")},
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


def test_embeddings_rate_limit_is_per_key_and_returns_retry_after(monkeypatch):
    monkeypatch.setattr(main, "RATE_LIMIT_REQUESTS", 2)
    monkeypatch.setattr(main, "RATE_LIMIT_WINDOW_SECONDS", 30)
    _patch_upstream(monkeypatch, _FakeResponse(200, {"data": [{"embedding": [1.0]}]}))
    with TestClient(app) as client:
        responses = [
            client.post(
                "/v1/embeddings",
                json={"text": "test"},
                headers={"X-API-Key": "inbound-key"},
            )
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [200, 200, 429]
    assert responses[-1].json() == {"detail": "request rate limit exceeded"}
    assert responses[-1].headers["Retry-After"] == "30"


def test_usage_guard_rejects_per_key_concurrency_and_releases_slot(monkeypatch):
    monkeypatch.setattr(main, "RATE_LIMIT_REQUESTS", 10)
    monkeypatch.setattr(main, "MAX_CONCURRENT_REQUESTS", 1)
    guard = main.RequestUsageGuard()
    key_id = b"irreversible-key-identifier"

    async def exercise_guard():
        await guard.acquire(key_id)
        with pytest.raises(HTTPException) as exc_info:
            await guard.acquire(key_id)
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "concurrent request limit exceeded"
        assert exc_info.value.headers == {"Retry-After": "1"}

        await guard.release(key_id)
        await guard.acquire(key_id)
        await guard.release(key_id)

    asyncio.run(exercise_guard())


def test_embeddings_malformed_upstream_returns_generic_502(monkeypatch):
    # Upstream replies 200 but with an unexpected shape => generic 502, no body reflected.
    monkeypatch.setattr(main, "MAX_CONCURRENT_REQUESTS", 1)
    _patch_upstream(monkeypatch, _FakeResponse(200, {"unexpected": "shape"}, text="provider internals"))
    with TestClient(app) as client:
        response = client.post(
            "/v1/embeddings",
            json={"text": "test"},
            headers={"X-API-Key": "inbound-key"},
        )
        # The dependency finalizer must release the concurrency slot on an error path.
        second_response = client.post(
            "/v1/embeddings",
            json={"text": "test"},
            headers={"X-API-Key": "inbound-key"},
        )
    assert response.status_code == 502
    assert second_response.status_code == 502
    assert response.json()["detail"] == "embedding upstream error"
    assert "provider internals" not in response.text
