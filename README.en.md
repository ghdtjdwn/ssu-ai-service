# ssu-ai-service

[![CI](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml)
[![Security](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/security.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/security.yml)
[![CodeQL](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/codeql.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/codeql.yml)

[한국어](README.md) · **English**

An independent FastAPI gateway that exposes Gemini `gemini-embedding-001` as a
768-dimensional text-embedding API. It keeps provider credentials behind an inbound API-key
boundary and centralizes input limits, concurrency control, and upstream error isolation.

[Production endpoint](https://ssu-ai-service.duckdns.org) ·
[Deployment troubleshooting](docs/deployment-troubleshooting.md) · [Campus AI platform](https://ssuai.vercel.app)

## Service boundary

`ssu-ai-service` is a standalone B2B embedding API. The primary ssuAI chat path currently runs
through `ssuAgent → ssuMCP` and does not depend on this service, so an embedding-gateway outage does
not become a platform-wide chat outage. Production requests use the real Gemini API. Tests use an
HTTP transport double only to verify the contract without network access or provider charges.

```text
authorized caller
  → X-API-Key gate
  → per-key request and concurrency guard
  → FastAPI validation
  → Gemini embeddings API
  → bounded 768-dimensional response
```

## API

| Method | Path | Authentication | Contract |
| --- | --- | --- | --- |
| `GET` | `/health` | None | Process liveness without an upstream call. |
| `GET` | `/ready` | None | Required-key and protection-config readiness without an upstream call. |
| `POST` | `/v1/embeddings` | `X-API-Key` | Converts `{"text":"..."}` into a 768-dimensional embedding. |

```bash
curl https://ssu-ai-service.duckdns.org/v1/embeddings \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${SSUAI_SERVICE_API_KEY}" \
  -d '{"text":"Soongsil University library"}'
```

The response is `{"embedding":[...],"dimension":768}`. Do not place real keys in shell history,
documentation, or logs.

## Security and reliability

- Missing or mismatched inbound credentials return 401; an unconfigured service key fails closed.
- The Gemini key is sent only through the `Authorization` header, never in a URL.
- Blank text is rejected and input is capped at 8,000 characters by default.
- Each API key receives a 60-request/minute sliding window and four concurrent requests by default.
  Limiter state stores only a SHA-256 identifier.
- Raw Gemini errors and response bodies are never reflected to callers or logs.
- `/health` and `/ready` never consume provider quota.
- Runtime and development dependencies are exactly pinned; Dependabot, pip-audit, Gitleaks, and
  CodeQL run in CI.
- Kubernetes Secret references are mandatory. A rotation increments `secretRef.revision` so pods
  restart and read the new `envFrom` values.

The production deployment currently has one replica, so the process-local limiter is also the
service-wide limit. Add a shared limiter such as Redis before scaling horizontally.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `SSUAI_GEMINI_API_KEY` | None | Required Gemini upstream credential |
| `SSUAI_SERVICE_API_KEY` | None | Required inbound `X-API-Key` credential |
| `SSUAI_MAX_TEXT_LENGTH` | `8000` | Maximum input characters |
| `SSUAI_RATE_LIMIT_REQUESTS` | `60` | Requests per key in one window |
| `SSUAI_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate-limit window |
| `SSUAI_MAX_CONCURRENT_REQUESTS` | `4` | Concurrent requests per key |

Invalid or non-positive protection settings fall back to safe values, while `/ready` returns 503 so
the misconfigured instance receives no traffic.

## Local development and verification

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
pytest -q
python -m pip install pip-audit==2.10.1
pip-audit -r requirements-dev.txt
docker build -t ssu-ai-service .
helm lint deploy/charts/ssu-ai-service
```

The test suite covers fail-closed authentication, liveness/readiness separation, input boundaries,
rate and concurrency limits, a valid 768-dimensional response, and upstream error redaction.

## Deployment

After a `main` push, CI publishes an ARM64 GHCR image only after tests pass. ArgoCD Image Updater
writes the `sha-<40hex>` tag to Git, and ArgoCD syncs it into the `ssuai-prod` namespace. The
container runs non-root with dropped capabilities, blocked privilege escalation, and seccomp.

- Helm chart and ArgoCD Application: [`deploy/`](deploy/)
- Production procedure and failure analysis: [`docs/deployment-troubleshooting.md`](docs/deployment-troubleshooting.md)
- Private vulnerability reporting scope and process: [`.github/SECURITY.md`](.github/SECURITY.md)

Secret rotation, pod restarts, and rollouts are production changes and require the approved
operations procedure.

## License

[MIT](LICENSE)
