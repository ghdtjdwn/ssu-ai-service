# ssu-ai-service

[![CI](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml)
[![Security](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/security.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/security.yml)
[![CodeQL](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/codeql.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/codeql.yml)

[한국어](README.md) · **English**

An independent FastAPI gateway that exposes Gemini `gemini-embedding-001` as a
768-dimensional text-embedding API. It keeps provider credentials behind an inbound API-key
boundary and centralizes input limits, concurrency control, and upstream error isolation.

[Production health](https://ssu-ai-service.duckdns.org/health) ·
[API docs (/docs)](https://ssu-ai-service.duckdns.org/docs) ·
[Deployment troubleshooting](docs/deployment-troubleshooting.md) · [Campus AI platform](https://ssuai.vercel.app)

## Role in the platform

| Service | Responsibility | Repository |
| --- | --- | --- |
| ssuAI | User interface, same-origin BFF, auth state and SSE/HITL UX | [ghdtjdwn/ssuAI](https://github.com/ghdtjdwn/ssuAI) |
| ssuAgent | LangGraph routing, conversation state, HITL orchestration | [ghdtjdwn/ssuAgent](https://github.com/ghdtjdwn/ssuAgent) |
| ssuMCP | Campus domain logic, MCP/REST contracts, authentication and state changes | [ghdtjdwn/ssuMCP](https://github.com/ghdtjdwn/ssuMCP) |
| **ssu-ai-service** | **Isolated embedding request gateway** | this repository |

This is an unofficial project with no affiliation to Soongsil University.

## Service boundary

`ssu-ai-service` is a single-endpoint embedding proxy protected by one shared service key. It has no
per-caller keys, quotas, or usage metering. Production requests use the real Gemini API. Tests use an
HTTP transport double only to verify the contract without network access or provider charges.

No platform code currently calls this service. The ssuAI chat path runs through `ssuAgent → ssuMCP`,
and ssuMCP's optional hybrid RAG (off by default) calls the Gemini embeddings API directly. An outage
here therefore never becomes a campus-chat outage, and the service is deployed and operated on its own.

```text
authorized caller
  → X-API-Key gate
  → service-wide request and concurrency guard
  → FastAPI validation
  → Gemini embeddings API
  → bounded 768-dimensional response
```

## API

| Method | Path | Authentication | Contract |
| --- | --- | --- | --- |
| `GET` | `/health` | None | Process liveness without an upstream call; reports the commit of the running image as `build_sha`. |
| `GET` | `/ready` | None | Required-key and protection-config readiness without an upstream call. |
| `POST` | `/v1/embeddings` | `X-API-Key` | Converts `{"text":"..."}` into a 768-dimensional embedding. |

```bash
curl https://ssu-ai-service.duckdns.org/v1/embeddings \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${SSUAI_SERVICE_API_KEY}" \
  -d '{"text":"Soongsil University library"}'
```

The response is `{"embedding":[...],"dimension":768}`. Do not place real keys in shell history,
documentation, or logs. The production instance serves its OpenAPI documentation at
[`/docs`](https://ssu-ai-service.duckdns.org/docs).

## Security and reliability

- Missing or mismatched inbound credentials return 401; an unconfigured service key fails closed.
- The Gemini key is sent only through the `Authorization` header, never in a URL.
- Blank text is rejected and input is capped at 8,000 characters by default.
- A 60-request/minute sliding window and four concurrent requests apply by default. There is one inbound
  key, so these limits are service-wide. Limiter state holds a fixed identifier, not the key or a hash of it.
- Raw Gemini errors and response bodies are never reflected to callers or logs. Network errors become
  503, non-200 and malformed responses become 502, and only the status code is logged.
- `/health` and `/ready` never consume provider quota.
- Runtime and development dependencies are exactly pinned, and CI installs with `pip install --no-deps`
  followed by `pip check` so an incomplete lock fails the build. Dependabot (pip, GitHub Actions, Docker
  base image), pip-audit, Gitleaks, and CodeQL run in CI.
- Both credentials are injected from a server-side environment file that is not in this repository,
  and rotation happens only through the server operations procedure.

Production runs one container, so the process-local limiter is also the service-wide limit. Add a
shared limiter such as Redis before scaling horizontally.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `SSUAI_GEMINI_API_KEY` | None | Required Gemini upstream credential |
| `SSUAI_SERVICE_API_KEY` | None | Required inbound `X-API-Key` credential |
| `SSUAI_MAX_TEXT_LENGTH` | `8000` | Maximum input characters |
| `SSUAI_RATE_LIMIT_REQUESTS` | `60` | Service-wide requests in one window |
| `SSUAI_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate-limit window |
| `SSUAI_MAX_CONCURRENT_REQUESTS` | `4` | Service-wide concurrent requests |

Invalid or non-positive protection settings fall back to safe values, while `/ready` returns 503 so
the misconfigured instance receives no traffic.

## Local development and verification

Python 3.11 or newer is required (the production image and CI use 3.11).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
ruff check . && ruff format --check .
pytest -q
python -m pip install pip-audit==2.10.1
pip-audit -r requirements-dev.txt
docker build --build-arg VCS_REF=$(git rev-parse HEAD) -t ssu-ai-service .
```

`VCS_REF` is required. The Dockerfile records it as the OCI revision label and as the
`SSUAI_BUILD_SHA` environment variable, which `/health` returns as `build_sha`. The
[2026-09-03 entry](docs/deployment-troubleshooting.md#2026-09-03-cached-image-timestamp-stalled-an-automated-rollout)
explains why the argument exists.

The test suite covers fail-closed authentication, liveness/readiness separation, the settings parser,
input boundaries, rate and concurrency limits, a valid 768-dimensional response, and redaction of
upstream network errors (503), non-200 responses (502), and malformed responses (502).

## Deployment

After a `main` push, CI publishes an ARM64 image as `ghcr.io/ghdtjdwn/ssu-ai-service:sha-<40hex>`
only after tests pass. Production is a single ARM64 VM (1 vCPU, about 6GB RAM) where the service runs
under Docker Compose behind one Caddy instance that terminates TLS. A systemd timer on the server runs
every 15 minutes, picks the newest `main` commit that has a `sha-<commit>` tag on GHCR, pins its
digest, pulls and replaces the container, and rolls back to the previous image if the public
`GET /ready` does not return 200. A merge usually lands within 15 to 16 minutes, and the deployed
commit is visible as `build_sha` in `GET /health`. The service runs as one container with a 256MiB
memory limit and a non-root user.

Until 2026-10-15 the stack was k3s + ArgoCD + Image Updater. It was replaced so that every personal
service fits on one 1 OCPU / 6GB server inside the free tier.

- Current deployment path and what to check when a deploy did not land: [`deploy/README.md`](deploy/README.md)
- Decision record and trade-offs: [ADR 0001](docs/adr/0001-single-vm-docker-compose-deploy.md)
- Retired Helm chart and ArgoCD Application (reference only): [`deploy/archive/k3s/`](deploy/archive/k3s/)
- Deployment failure analysis from the k3s/ArgoCD era: [`docs/deployment-troubleshooting.md`](docs/deployment-troubleshooting.md)
- Private vulnerability reporting scope and process: [`.github/SECURITY.md`](.github/SECURITY.md)

The runtime definition (Compose, timer, secrets) lives in a private infrastructure repository. Secret
rotation and manual restarts are production changes and follow the server operations procedure.

## License

[MIT](LICENSE)
