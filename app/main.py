import asyncio
import hashlib
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, field_validator


def _positive_int_setting(name: str, default: int) -> tuple[int, str | None]:
    """Read a positive integer without making a bad environment value unsafe."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default, None
    try:
        value = int(raw_value)
    except ValueError:
        return default, name
    if value <= 0:
        return default, name
    return value, None


MAX_TEXT_LENGTH, _max_text_error = _positive_int_setting(
    "SSUAI_MAX_TEXT_LENGTH", 8_000
)
RATE_LIMIT_REQUESTS, _rate_requests_error = _positive_int_setting(
    "SSUAI_RATE_LIMIT_REQUESTS", 60
)
RATE_LIMIT_WINDOW_SECONDS, _rate_window_error = _positive_int_setting(
    "SSUAI_RATE_LIMIT_WINDOW_SECONDS", 60
)
MAX_CONCURRENT_REQUESTS, _concurrency_error = _positive_int_setting(
    "SSUAI_MAX_CONCURRENT_REQUESTS", 4
)
CONFIG_ERRORS = tuple(
    setting
    for setting in (
        _max_text_error,
        _rate_requests_error,
        _rate_window_error,
        _concurrency_error,
    )
    if setting is not None
)


class RequestUsageGuard:
    """Process-local, per-key sliding-window and concurrency guard."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._request_times: dict[bytes, deque[float]] = defaultdict(deque)
        self._active_requests: dict[bytes, int] = defaultdict(int)

    async def acquire(self, key_id: bytes) -> None:
        now = time.monotonic()
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        async with self._lock:
            request_times = self._request_times[key_id]
            while request_times and request_times[0] <= cutoff:
                request_times.popleft()

            if len(request_times) >= RATE_LIMIT_REQUESTS:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "request rate limit exceeded",
                    headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
                )
            if self._active_requests[key_id] >= MAX_CONCURRENT_REQUESTS:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "concurrent request limit exceeded",
                    headers={"Retry-After": "1"},
                )

            request_times.append(now)
            self._active_requests[key_id] += 1

    async def release(self, key_id: bytes) -> None:
        async with self._lock:
            remaining = self._active_requests[key_id] - 1
            if remaining > 0:
                self._active_requests[key_id] = remaining
            else:
                self._active_requests.pop(key_id, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One shared AsyncClient for the process lifetime so requests reuse pooled
    # keep-alive TCP/TLS connections to the upstream instead of paying a full
    # connect + TLS handshake on every embedding call.
    async with httpx.AsyncClient(timeout=10.0) as client:
        app.state.http_client = client
        app.state.usage_guard = RequestUsageGuard()
        yield


app = FastAPI(
    title="SsuAI-B2B-Model-Server",
    description="FastAPI-based AI serving gateway for RAG embeddings",
    version="1.1.0",
    lifespan=lifespan,
)

log = logging.getLogger("uvicorn.error")

# Upstream credential — sent to Gemini as an Authorization header, never in the URL.
GEMINI_API_KEY = os.getenv("SSUAI_GEMINI_API_KEY", "")
# Inbound credential — callers must present this as X-API-Key. Empty => closed (401),
# so an unset key fails safe instead of leaving the gateway open.
SERVICE_API_KEY = os.getenv("SSUAI_SERVICE_API_KEY", "")

EMBEDDING_MODEL = "gemini-embedding-001"
# gemini-embedding-001 is a Matryoshka (MRL) model whose vectors stay meaningful when
# truncated, so we cap to 768 dims to match the sibling RAG store.
EMBEDDING_DIM = 768


def require_api_key(x_api_key: str = Header(default="")) -> bytes:
    """Inbound auth gate. Fails closed when SSUAI_SERVICE_API_KEY is unset.

    Uses a constant-time comparison so the check does not leak the key length or a
    matching prefix through response timing. Compares UTF-8 bytes rather than str:
    Starlette decodes header values as latin-1, so a non-ASCII X-API-Key would make
    secrets.compare_digest raise TypeError (str compare rejects non-ASCII) and surface
    a confusing 500 instead of this clean 401.
    """
    if not SERVICE_API_KEY or not secrets.compare_digest(
        x_api_key.encode("utf-8"), SERVICE_API_KEY.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing api key")
    # Keep only an irreversible identifier in limiter state, never the credential.
    return hashlib.sha256(x_api_key.encode("utf-8")).digest()


async def enforce_usage_limits(
    raw_request: Request,
    key_id: Annotated[bytes, Depends(require_api_key)],
) -> AsyncIterator[None]:
    guard: RequestUsageGuard = raw_request.app.state.usage_guard
    await guard.acquire(key_id)
    try:
        yield
    finally:
        await guard.release(key_id)


class EmbeddingRequest(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        if len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"text must contain at most {MAX_TEXT_LENGTH} characters")
        return value


class EmbeddingResponse(BaseModel):
    embedding: list[float]
    dimension: int


@app.get("/health")
def health_check():
    # Liveness stays independent of configuration while preserving the existing
    # response field for callers; it never exposes the credential value.
    return {"status": "healthy", "gemini_configured": bool(GEMINI_API_KEY)}


@app.get("/ready")
def readiness_check():
    # Readiness is local and deterministic: never spend quota or depend on upstream
    # network health merely to decide whether this pod may receive traffic.
    if not GEMINI_API_KEY or not SERVICE_API_KEY or CONFIG_ERRORS:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "service not ready")
    return {"status": "ready"}


@app.post(
    "/v1/embeddings",
    response_model=EmbeddingResponse,
    dependencies=[Depends(enforce_usage_limits)],
)
async def get_embedding(request: EmbeddingRequest, raw_request: Request):
    if not GEMINI_API_KEY:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "embedding upstream not configured"
        )

    # Key goes in the Authorization header, not the query string — query strings land in
    # access logs and proxies, which would leak the upstream credential.
    url = "https://generativelanguage.googleapis.com/v1beta/openai/embeddings"
    headers = {
        "Authorization": f"Bearer {GEMINI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"input": request.text, "model": EMBEDDING_MODEL}

    client: httpx.AsyncClient = raw_request.app.state.http_client
    try:
        response = await client.post(url, json=payload, headers=headers)
    except httpx.RequestError:
        # Do not echo the exception (may carry the upstream URL/host).
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "embedding upstream unreachable"
        )

    if response.status_code != 200:
        # Log only the status for debugging; the upstream body can contain provider
        # internals and must not reach either caller responses or application logs.
        log.warning("gemini embeddings failed with status %s", response.status_code)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "embedding upstream error")

    try:
        embedding = response.json()["data"][0]["embedding"][:EMBEDDING_DIM]
    except (KeyError, IndexError, TypeError, ValueError):
        # Record only the malformed shape signal and keep the raw body out of logs.
        log.warning("gemini embeddings returned a malformed response")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "embedding upstream error")
    return EmbeddingResponse(embedding=embedding, dimension=len(embedding))
