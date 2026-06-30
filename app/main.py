import logging
import os
import secrets

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel

app = FastAPI(
    title="SsuAI-B2B-Model-Server",
    description="FastAPI-based AI serving gateway for RAG embeddings",
    version="1.1.0",
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


def require_api_key(x_api_key: str = Header(default="")) -> None:
    """Inbound auth gate. Fails closed when SSUAI_SERVICE_API_KEY is unset.

    Uses a constant-time comparison so the check does not leak the key length or a
    matching prefix through response timing.
    """
    if not SERVICE_API_KEY or not secrets.compare_digest(x_api_key, SERVICE_API_KEY):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing api key")


class EmbeddingRequest(BaseModel):
    text: str


class EmbeddingResponse(BaseModel):
    embedding: list[float]
    dimension: int


@app.get("/health")
def health_check():
    # Liveness probe — no auth, reports config presence only (never the key value).
    return {"status": "healthy", "gemini_configured": bool(GEMINI_API_KEY)}


@app.post(
    "/v1/embeddings",
    response_model=EmbeddingResponse,
    dependencies=[Depends(require_api_key)],
)
async def get_embedding(request: EmbeddingRequest):
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

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=10.0)
    except httpx.RequestError:
        # Do not echo the exception (may carry the upstream URL/host).
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "embedding upstream unreachable"
        )

    if response.status_code != 200:
        # Log the upstream detail server-side for debugging; return a generic message so
        # the upstream body (which can carry provider internals) is never reflected to callers.
        log.warning("gemini embeddings failed: %s %s", response.status_code, response.text[:300])
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "embedding upstream error")

    try:
        embedding = response.json()["data"][0]["embedding"][:EMBEDDING_DIM]
    except (KeyError, IndexError, TypeError, ValueError):
        # Malformed upstream payload — log server-side, return a generic error so the
        # raw upstream body is never reflected to callers.
        log.warning("gemini embeddings malformed response: %s", response.text[:300])
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "embedding upstream error")
    return EmbeddingResponse(embedding=embedding, dimension=len(embedding))
