# ssu-ai-service

[![CI](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml)

**한국어** [README.md](README.md) · **English** (this document)

> 🧩 **Soongsil Campus AI Platform** (1 of 4 services) · [ssuMCP](https://github.com/ghdtjdwn/ssuMCP) · [ssuAI](https://github.com/ghdtjdwn/ssuAI) · [ssuAgent](https://github.com/ghdtjdwn/ssuAgent) · **ssu-ai-service** · 🟢 [Live](https://ssuai.vercel.app)

A **B2B embedding serving gateway** for the Soongsil University AI platform. It exposes text embeddings (Gemini `gemini-embedding-001`, Matryoshka 768 dimensions) through a single FastAPI endpoint. It runs as an independent service, decoupled from the ssuMCP/ssuAI core — a portfolio piece demonstrating how to design authentication and key hygiene when the model-serving surface lives in its own service.

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | Health check (k8s liveness). Reports whether keys are configured without exposing their values |
| POST | `/v1/embeddings` | `X-API-Key` required | `{"text": "..."}` → `{"embedding": [...768], "dimension": 768}` |

## Security Design (hardened 2026-06-30)

The initial version of this service shipped with three flaws; rather than scrapping it, all three were corrected in place.

1. **Upstream key moved from URL query string to header** — the Gemini key used to be sent as `?key=...`, risking plaintext exposure in access logs and proxies. It is now sent via the `Authorization: Bearer` header.
2. **Inbound auth gate (fail-closed)** — `/v1/embeddings` requires `X-API-Key` to match `SSUAI_SERVICE_API_KEY`. If the key is **unset, the gate closes with 401 instead of staying open**, shutting down a surface anyone could have called to burn LLM spend. Same principle as the `AGENT_API_KEY` gate on ssuAgent's `/agent`.
3. **No upstream error reflection** — the Gemini response body used to be echoed back to the caller verbatim. Upstream details now go **to server logs only**, and callers receive a generalized message (e.g. `502 embedding upstream error`).

## Environment Variables

| Variable | Description |
|---|---|
| `SSUAI_GEMINI_API_KEY` | (Upstream) Gemini embedding API key. If unset, `/v1/embeddings` returns 503 |
| `SSUAI_SERVICE_API_KEY` | (Inbound) Credential callers present via `X-API-Key`. If unset, the gate fails closed (401) |

## Running

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# or as a container
docker build -t ssu-ai-service . && docker run -p 8000:8000 \
  -e SSUAI_GEMINI_API_KEY=... -e SSUAI_SERVICE_API_KEY=... ssu-ai-service
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest -q
```

Covers 7 cases (real upstream mocked): health check, missing inbound key → 401, wrong key → 401, **non-ASCII key → 401** (the latin-1/UTF-8 compare trap), matching key with upstream unconfigured → 503, **the happy path capping the vector to 768 dims (Matryoshka truncation) end-to-end**, and a malformed upstream response → 502 (no internals leaked).

## Deployment (live in prod since 2026-07-02)

Deployed to a k3s cluster (`ssuai-prod` namespace) via GitOps: push to main → GitHub Actions builds and pushes an arm64 image to ghcr → ArgoCD Image Updater commits the `sha-<hash>` tag back into values.yaml → auto sync.

- **Runtime hardening**: the container runs as non-root (uid 10001, `runAsNonRoot` enforced), drops all capabilities, and blocks privilege escalation.
- **Secrets**: `ssu-ai-service-secrets` (created manually in the cluster, never committed) — key names match the environment variable table above.
- **Exposure**: publicly live — **<https://ssu-ai-service.duckdns.org>** (Let's Encrypt TLS). Unauthenticated calls fail closed with 401. Health: `curl https://ssu-ai-service.duckdns.org/health` → `{"status":"healthy",...}`.
- Chart / ArgoCD manifests: [`deploy/`](deploy/).
