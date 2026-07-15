# ssu-ai-service

[![CI](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml)

**한국어** [README.md](README.md) · **English** (this document)

> 🧩 **Soongsil Campus AI Platform** (1 of 4 services) · [ssuMCP](https://github.com/ghdtjdwn/ssuMCP) · [ssuAI](https://github.com/ghdtjdwn/ssuAI) · [ssuAgent](https://github.com/ghdtjdwn/ssuAgent) · **ssu-ai-service** · 🟢 [Live](https://ssuai.vercel.app)

A **B2B embedding serving gateway** for the Soongsil University AI platform. It exposes text embeddings (Gemini `gemini-embedding-001`, Matryoshka 768 dimensions) through a single FastAPI endpoint. It runs as an independent service, decoupled from the ssuMCP/ssuAI core — a portfolio piece demonstrating how to design authentication and key hygiene when the model-serving surface lives in its own service.

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | Process liveness; preserves the compatibility field reporting Gemini configuration presence and never calls upstream |
| GET | `/ready` | None | Local readiness; returns 503 when required keys are absent or protection settings are invalid, without calling upstream |
| POST | `/v1/embeddings` | `X-API-Key` required | `{"text": "..."}` → `{"embedding": [...768], "dimension": 768}` |

## Security Design (hardened 2026-06-30 and 2026-07-15)

The initial security flaws were corrected in place and explicit request-protection boundaries were added.

1. **Upstream key moved from URL query string to header** — the Gemini key used to be sent as `?key=...`, risking plaintext exposure in access logs and proxies. It is now sent via the `Authorization: Bearer` header.
2. **Inbound auth gate (fail-closed)** — `/v1/embeddings` requires `X-API-Key` to match `SSUAI_SERVICE_API_KEY`. If the key is **unset, the gate closes with 401 instead of staying open**, shutting down a surface anyone could have called to burn LLM spend. Same principle as the `AGENT_API_KEY` gate on ssuAgent's `/agent`.
3. **No upstream error reflection or body logging** — the Gemini response body used to be echoed back to the caller verbatim. Raw bodies now stay out of both responses and application logs; only the status code or malformed-shape signal is logged, and callers receive a generalized message (e.g. `502 embedding upstream error`).
4. **Input boundary** — whitespace-only input is rejected and text is capped at 8,000 characters by default. This is a front-door safeguard informed by the current 2,048-token limit of `gemini-embedding-001` and the memory/cost risk of unbounded bodies.
5. **Per-key usage limits** — a process-local sliding window allows 60 requests per minute and 4 concurrent requests by default. Limiter state stores a SHA-256 identifier instead of the API key and returns 429 when a limit is exceeded.
6. **Separate liveness and readiness** — `/health` reports process liveness; `/ready` checks both required keys and protection settings. Readiness never calls Gemini, so probes neither spend quota nor propagate transient upstream failures.

## Environment Variables

| Variable | Description |
|---|---|
| `SSUAI_GEMINI_API_KEY` | (Upstream) Gemini embedding API key. If unset, `/v1/embeddings` returns 503 |
| `SSUAI_SERVICE_API_KEY` | (Inbound) Credential callers present via `X-API-Key`. If unset, the gate fails closed (401) |
| `SSUAI_MAX_TEXT_LENGTH` | Maximum input characters. Positive integer, default `8000` |
| `SSUAI_RATE_LIMIT_REQUESTS` | Requests allowed per key in one window. Positive integer, default `60` |
| `SSUAI_RATE_LIMIT_WINDOW_SECONDS` | Rate-limit window in seconds. Positive integer, default `60` |
| `SSUAI_MAX_CONCURRENT_REQUESTS` | Concurrent requests allowed per key. Positive integer, default `4` |

An invalid or non-positive protection setting falls back to the safe default while `/ready` returns 503. The production deployment currently has one replica, so the process-local limiter is also the service-wide limiter. A multi-replica deployment should move limiter state to a shared store such as Redis to preserve a global limit.

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

Covers 16 cases with the real upstream mocked, including separate liveness/readiness with no upstream probe, fail-closed authentication, empty/oversized input, per-key rate and concurrency limits, the 768-dimensional happy path, and non-reflecting upstream failures.

## Deployment (live since 2026-07-02, hardened 2026-07-15)

Deployed to a k3s cluster (`ssuai-prod` namespace) via GitOps: push to main → GitHub Actions builds and pushes an arm64 image to ghcr → ArgoCD Image Updater commits the `sha-<hash>` tag back into values.yaml → auto sync.

- **Runtime hardening**: the container runs as non-root (uid 10001, `runAsNonRoot` enforced), drops all capabilities, and blocks privilege escalation.
- **Secrets**: `ssu-ai-service-secrets` (created manually in the cluster, never committed) is a required reference. A missing Secret prevents the Pod from starting; key names match the environment variable table above.
- **Reproducible supply chain**: runtime/dev Python dependencies use exact versions, the official Python base image uses a digest, and GitHub Actions pin Node.js 24-based majors by full commit SHA.
- **Exposure**: publicly live — **<https://ssu-ai-service.duckdns.org>** (Let's Encrypt TLS). Unauthenticated calls fail closed with 401. Health: `curl https://ssu-ai-service.duckdns.org/health` → `{"status":"healthy","gemini_configured":true}`.
- Chart / ArgoCD manifests: [`deploy/`](deploy/).
- Deployment failure analysis and recovery: [`docs/deployment-troubleshooting.md`](docs/deployment-troubleshooting.md).
