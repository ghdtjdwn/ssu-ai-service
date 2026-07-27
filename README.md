# ssu-ai-service

[![CI](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml)
[![Security](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/security.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/security.yml)
[![CodeQL](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/codeql.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/codeql.yml)

**한국어** · [English](README.en.md)

Gemini `gemini-embedding-001`을 768차원 텍스트 임베딩 API로 노출하는 독립 FastAPI 게이트웨이다.
외부 caller가 provider key를 직접 소유하지 않도록 inbound API key, 입력·동시성 제한, upstream 오류
격리를 한 경계에서 처리한다.

[운영 endpoint](https://ssu-ai-service.duckdns.org) ·
[배포 장애 기록](docs/deployment-troubleshooting.md) · [캠퍼스 AI 플랫폼](https://ssuai.vercel.app)

## 서비스 경계

`ssu-ai-service`는 독립적으로 호출할 수 있는 B2B embedding API다. 현재 ssuAI의 핵심 chat 요청 경로는
`ssuAgent → ssuMCP`이며 이 서비스에 의존하지 않는다. 따라서 이 서비스 장애가 campus chat 전체 장애로
전파되지는 않는다. production 요청은 실제 Gemini API를 사용하고, 테스트만 네트워크·과금 없이 계약을
검증하기 위해 HTTP transport double을 사용한다.

```text
authorized caller
  → X-API-Key gate
  → per-key request and concurrency guard
  → FastAPI validation
  → Gemini embeddings API
  → bounded 768-dimensional response
```

## API

| 메서드 | 경로 | 인증 | 계약 |
| --- | --- | --- | --- |
| `GET` | `/health` | 없음 | 프로세스 liveness. upstream을 호출하지 않는다. |
| `GET` | `/ready` | 없음 | 필수 key와 보호 설정을 확인한다. upstream을 호출하지 않는다. |
| `POST` | `/v1/embeddings` | `X-API-Key` | `{"text":"..."}`를 768차원 embedding으로 변환한다. |

```bash
curl https://ssu-ai-service.duckdns.org/v1/embeddings \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${SSUAI_SERVICE_API_KEY}" \
  -d '{"text":"숭실대학교 도서관"}'
```

응답은 `{"embedding":[...],"dimension":768}` 형태다. 실제 key를 shell history, 문서, 로그에 남기지
않는다.

## 보안과 신뢰성

- inbound key가 없거나 다르면 401이며, 서비스 key가 설정되지 않은 상태도 fail-closed다.
- Gemini key는 URL이 아니라 `Authorization` header로만 전달한다.
- 공백 입력을 거부하고 기본 8,000자 상한을 적용한다.
- API key별 sliding window는 기본 60회/분, 동시 요청은 4개다. limiter에는 SHA-256 식별자만 남긴다.
- Gemini 원문 오류와 응답 body는 caller나 로그에 반사하지 않는다.
- `/health`와 `/ready`는 비용이 드는 upstream probe를 수행하지 않는다.
- runtime/dev dependency는 exact version이며, Dependabot·pip-audit·Gitleaks·CodeQL을 CI에서 실행한다.
- Kubernetes Secret은 필수 참조다. 회전 시 chart의 `secretRef.revision`을 함께 올려 pod가 새 env 값을
  읽도록 한다.

현재 replica는 1개이므로 process-local limiter가 서비스 전체 한도다. 여러 replica로 확장할 때는 Redis
같은 공유 limiter를 먼저 도입해야 한다.

## 설정

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `SSUAI_GEMINI_API_KEY` | 없음 | Gemini upstream credential. 필수 |
| `SSUAI_SERVICE_API_KEY` | 없음 | caller가 `X-API-Key`로 제시하는 credential. 필수 |
| `SSUAI_MAX_TEXT_LENGTH` | `8000` | 입력 문자 수 상한 |
| `SSUAI_RATE_LIMIT_REQUESTS` | `60` | window당 key별 요청 수 |
| `SSUAI_RATE_LIMIT_WINDOW_SECONDS` | `60` | limiter window 길이 |
| `SSUAI_MAX_CONCURRENT_REQUESTS` | `4` | key별 동시 요청 수 |

보호 설정이 정수가 아니거나 0 이하이면 안전한 기본값으로 동작하지만 `/ready`는 503을 반환해 잘못된
배포를 traffic에서 제외한다.

## 로컬 실행과 검증

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

테스트는 인증 fail-closed, liveness/readiness 분리, 입력 경계, rate/concurrency 제한, 정상 768차원 응답,
upstream 오류 비반사를 검증한다.

## 배포

`main` push 후 CI가 test gate를 통과한 ARM64 이미지를 GHCR에 게시한다. ArgoCD Image Updater가
`sha-<40hex>` tag를 Git에 기록하고 ArgoCD가 `ssuai-prod` namespace로 동기화한다. 컨테이너는 non-root,
capability drop, privilege-escalation 차단, seccomp 설정으로 실행된다.

- Helm chart와 ArgoCD Application: [`deploy/`](deploy/)
- production 절차와 실패 분석: [`docs/deployment-troubleshooting.md`](docs/deployment-troubleshooting.md)

실제 Secret 교체, pod 재시작, rollout은 production 변경이므로 승인된 운영 절차에서만 수행한다.

## 라이선스

[MIT](LICENSE)
