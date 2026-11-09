import asyncio
import logging

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
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
    app opens in its lifespan. Returns a preset response from .post(), or raises
    the given transport error instead."""

    def __init__(self, response: _FakeResponse, error: Exception | None = None):
        self._response = response
        self._error = error
        self.post_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        self.post_calls += 1
        if self._error is not None:
            raise self._error
        return self._response


def _patch_upstream(monkeypatch, response: _FakeResponse, error: Exception | None = None):
    monkeypatch.setattr(main, "SERVICE_API_KEY", "inbound-key")
    monkeypatch.setattr(main, "GEMINI_API_KEY", "upstream-key")
    # The lifespan builds the shared client via httpx.AsyncClient(...); patching
    # the constructor makes it yield the fake, exercising the real app.state wiring.
    monkeypatch.setattr(
        main.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(response, error)
    )


def test_health_endpoint():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert "gemini_configured" in response.json()
    assert "build_sha" in response.json()


def test_health_reports_build_sha_from_image_environment(monkeypatch):
    monkeypatch.setattr(main, "BUILD_SHA", "0123456789abcdef0123456789abcdef01234567")
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.json()["build_sha"] == "0123456789abcdef0123456789abcdef01234567"


def test_health_build_sha_is_null_outside_a_built_image(monkeypatch):
    monkeypatch.setattr(main, "BUILD_SHA", None)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.json()["build_sha"] is None


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


def test_authenticated_key_maps_to_fixed_noncredential_usage_bucket(monkeypatch):
    monkeypatch.setattr(main, "SERVICE_API_KEY", "correct-key")

    assert main.require_api_key("correct-key") == main.AUTHENTICATED_USAGE_KEY
    assert b"correct-key" not in main.AUTHENTICATED_USAGE_KEY


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


def test_embeddings_rate_limit_is_per_service_and_returns_retry_after(monkeypatch):
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
    _patch_upstream(
        monkeypatch, _FakeResponse(200, {"unexpected": "shape"}, text="provider internals")
    )
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


def test_openapi_title_matches_repository_name():
    with TestClient(app) as client:
        info = client.get("/openapi.json").json()["info"]
    assert info["title"] == "ssu-ai-service"
    assert info["version"] == main.app.version


def test_embeddings_unreachable_upstream_returns_generic_503(monkeypatch, caplog):
    # A transport error (DNS, connect, timeout) => 503 whose body and logs carry neither
    # the exception text nor the upstream host.
    _patch_upstream(
        monkeypatch,
        _FakeResponse(200),
        error=httpx.ConnectError("boom generativelanguage.googleapis.com"),
    )
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        with TestClient(app) as client:
            response = client.post(
                "/v1/embeddings",
                json={"text": "test"},
                headers={"X-API-Key": "inbound-key"},
            )
    assert response.status_code == 503
    assert response.json() == {"detail": "embedding upstream unreachable"}
    assert "boom" not in response.text
    assert "googleapis" not in response.text
    assert "googleapis" not in caplog.text


@pytest.mark.parametrize("upstream_status", [400, 401, 429, 500])
def test_embeddings_non_200_upstream_returns_generic_502_and_logs_status_only(
    monkeypatch, caplog, upstream_status
):
    _patch_upstream(
        monkeypatch,
        _FakeResponse(upstream_status, {"error": {"message": "quota blob"}}, text="quota blob"),
    )
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        with TestClient(app) as client:
            response = client.post(
                "/v1/embeddings",
                json={"text": "test"},
                headers={"X-API-Key": "inbound-key"},
            )
    assert response.status_code == 502
    assert response.json() == {"detail": "embedding upstream error"}
    assert "quota blob" not in response.text
    assert f"gemini embeddings failed with status {upstream_status}" in caplog.text
    assert "quota blob" not in caplog.text


@pytest.mark.parametrize(
    ("raw_value", "expected_value", "expected_error"),
    [
        (None, 7, None),
        ("12", 12, None),
        ("abc", 7, "SSUAI_TEST_SETTING"),
        ("0", 7, "SSUAI_TEST_SETTING"),
        ("-5", 7, "SSUAI_TEST_SETTING"),
    ],
)
def test_positive_int_setting_falls_back_and_reports_invalid_values(
    monkeypatch, raw_value, expected_value, expected_error
):
    if raw_value is None:
        monkeypatch.delenv("SSUAI_TEST_SETTING", raising=False)
    else:
        monkeypatch.setenv("SSUAI_TEST_SETTING", raw_value)

    assert main._positive_int_setting("SSUAI_TEST_SETTING", 7) == (expected_value, expected_error)
