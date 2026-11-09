# ssu-ai-service

[![CI](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml)
[![Security](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/security.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/security.yml)
[![CodeQL](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/codeql.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/codeql.yml)

**한국어** · [English](README.en.md)

Gemini `gemini-embedding-001`을 768차원 텍스트 임베딩 API로 노출하는 독립 FastAPI 게이트웨이다.
외부 caller가 provider key를 직접 소유하지 않도록 inbound API key, 입력·동시성 제한, upstream 오류
격리를 한 경계에서 처리한다.

[운영 health](https://ssu-ai-service.duckdns.org/health) ·
[API 문서 (/docs)](https://ssu-ai-service.duckdns.org/docs) ·
[배포 장애 기록](docs/deployment-troubleshooting.md) · [캠퍼스 AI 플랫폼](https://ssuai.vercel.app)

## 플랫폼에서 맡는 역할

| 서비스 | 책임 | 저장소 |
| --- | --- | --- |
| ssuAI | 사용자 화면, same-origin BFF, 인증 상태와 SSE/HITL UX | [ghdtjdwn/ssuAI](https://github.com/ghdtjdwn/ssuAI) |
| ssuAgent | LangGraph 라우팅, 대화 상태, HITL 오케스트레이션 | [ghdtjdwn/ssuAgent](https://github.com/ghdtjdwn/ssuAgent) |
| ssuMCP | 캠퍼스 도메인 로직, MCP/REST 계약, 인증과 상태 변경 | [ghdtjdwn/ssuMCP](https://github.com/ghdtjdwn/ssuMCP) |
| **ssu-ai-service** | **격리된 임베딩 요청 게이트웨이** | 현재 저장소 |

숭실대학교와 무관한 비공식 프로젝트다.

## 서비스 경계

`ssu-ai-service`는 공유 service key 하나로 보호되는 단일 엔드포인트 embedding 프록시다. 호출자별 key, 쿼터,
사용량 계측은 없다. production 요청은 실제 Gemini API를 사용하고, 테스트만 네트워크·과금 없이 계약을
검증하기 위해 HTTP transport double을 사용한다.

현재 플랫폼 코드에서 이 서비스를 호출하는 곳은 없다. ssuAI의 chat 요청 경로는 `ssuAgent → ssuMCP`이고,
ssuMCP의 선택적 하이브리드 RAG(기본 OFF)도 Gemini 임베딩 API를 직접 호출한다. 따라서 이 서비스 장애가
campus chat 장애로 전파되지 않으며, 독립적으로 배포·운영된다.

```text
authorized caller
  → X-API-Key gate
  → service-wide request and concurrency guard
  → FastAPI validation
  → Gemini embeddings API
  → bounded 768-dimensional response
```

## API

| 메서드 | 경로 | 인증 | 계약 |
| --- | --- | --- | --- |
| `GET` | `/health` | 없음 | 프로세스 liveness. upstream을 호출하지 않고, 실행 중인 이미지의 커밋을 `build_sha`로 보고한다. |
| `GET` | `/ready` | 없음 | 필수 key와 보호 설정을 확인한다. upstream을 호출하지 않는다. |
| `POST` | `/v1/embeddings` | `X-API-Key` | `{"text":"..."}`를 768차원 embedding으로 변환한다. |

```bash
curl https://ssu-ai-service.duckdns.org/v1/embeddings \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${SSUAI_SERVICE_API_KEY}" \
  -d '{"text":"숭실대학교 도서관"}'
```

응답은 `{"embedding":[...],"dimension":768}` 형태다. 실제 key를 shell history, 문서, 로그에 남기지
않는다. 운영 인스턴스의 OpenAPI 문서는 [`/docs`](https://ssu-ai-service.duckdns.org/docs)에서 볼 수 있다.

## 보안과 신뢰성

- inbound key가 없거나 다르면 401이며, 서비스 key가 설정되지 않은 상태도 fail-closed다.
- Gemini key는 URL이 아니라 `Authorization` header로만 전달한다.
- 공백 입력을 거부하고 기본 8,000자 상한을 적용한다.
- sliding window는 기본 60회/분, 동시 요청은 4개다. inbound key가 하나이므로 이 한도는 서비스 전체에 적용된다.
  limiter는 key 값이나 그 해시를 보관하지 않고 고정 식별자 하나를 쓴다.
- Gemini 원문 오류와 응답 body는 caller나 로그에 반사하지 않는다. 네트워크 오류는 503, 비 200 응답과
  잘못된 형식은 502로 바꾸고 로그에는 상태 코드만 남긴다.
- `/health`와 `/ready`는 비용이 드는 upstream probe를 수행하지 않는다.
- runtime/dev dependency는 exact version이고 CI가 `pip install --no-deps` 후 `pip check`로 lock 완전성을
  확인한다. Dependabot(pip, GitHub Actions, Docker 베이스 이미지)·pip-audit·Gitleaks·CodeQL을 CI에서 실행한다.
- 두 credential은 저장소에 없는 서버 측 환경 파일로 컨테이너에 주입하며, 교체는 서버 운영 절차로만 한다.

운영 컨테이너는 1개이므로 process-local limiter가 서비스 전체 한도다. 여러 인스턴스로 확장할 때는 Redis
같은 공유 limiter를 먼저 도입해야 한다.

## 설정

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `SSUAI_GEMINI_API_KEY` | 없음 | Gemini upstream credential. 필수 |
| `SSUAI_SERVICE_API_KEY` | 없음 | caller가 `X-API-Key`로 제시하는 credential. 필수 |
| `SSUAI_MAX_TEXT_LENGTH` | `8000` | 입력 문자 수 상한 |
| `SSUAI_RATE_LIMIT_REQUESTS` | `60` | window당 서비스 전체 요청 수 |
| `SSUAI_RATE_LIMIT_WINDOW_SECONDS` | `60` | limiter window 길이 |
| `SSUAI_MAX_CONCURRENT_REQUESTS` | `4` | 서비스 전체 동시 요청 수 |

보호 설정이 정수가 아니거나 0 이하이면 안전한 기본값으로 동작하지만 `/ready`는 503을 반환해 잘못된
배포를 traffic에서 제외한다.

## 로컬 실행과 검증

Python 3.11 이상이 필요하다(운영 이미지와 CI는 3.11).

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

`VCS_REF`는 필수다. Dockerfile이 이 값을 OCI revision label과 `SSUAI_BUILD_SHA` 환경 변수로 기록하고
`/health`가 `build_sha`로 돌려준다. 이 인자가 생긴 경위는 [2026-09-03 기록](docs/deployment-troubleshooting.md#2026-09-03-cached-image-timestamp-stalled-an-automated-rollout)에
있다.

테스트는 인증 fail-closed, liveness/readiness 분리, 설정 파서, 입력 경계, rate/concurrency 제한, 정상 768차원
응답, upstream 네트워크 오류(503)·비 200 응답(502)·잘못된 형식(502)의 비반사를 검증한다.

## 배포

`main` push 후 CI가 test gate를 통과한 ARM64 이미지를 `ghcr.io/ghdtjdwn/ssu-ai-service:sha-<40hex>`로
게시한다. 운영 환경은 단일 ARM64 VM(1 vCPU, 약 6GB RAM)이며 서비스는 Docker Compose로 실행되고 앞단의 Caddy
하나가 TLS를 종료한다. 서버의 systemd 타이머가 15분마다 `main` 최근 커밋 중 GHCR에 `sha-<커밋>` 태그가
있는 가장 최신 것을 골라 digest를 고정해 pull·교체하고, 공개 주소의 `GET /ready`가 200이 아니면 이전
이미지로 되돌린다. 머지 후 보통 15~16분 안에 반영되며, 반영된 커밋은 `GET /health`의 `build_sha`로
확인한다. 컨테이너는 1개, 메모리 상한 256MiB, non-root 사용자로 실행된다.

2026-10-15까지는 k3s + ArgoCD + Image Updater 구성이었고, 무료 한도 안의 1 OCPU / 6GB 서버 한 대에서 모든
개인 서비스를 돌리기 위해 전환했다.

- 현재 배포 경로와 반영 확인 순서: [`deploy/README.md`](deploy/README.md)
- 전환 결정과 trade-off: [ADR 0001](docs/adr/0001-single-vm-docker-compose-deploy.md)
- 폐기된 Helm chart·ArgoCD Application(참고용): [`deploy/archive/k3s/`](deploy/archive/k3s/)
- k3s/ArgoCD 시절 배포 장애 분석: [`docs/deployment-troubleshooting.md`](docs/deployment-troubleshooting.md)
- 비공개 취약점 제보 범위와 절차: [`.github/SECURITY.md`](.github/SECURITY.md)

컨테이너 실행 정의(Compose, 타이머, 비밀값)는 비공개 인프라 저장소에서 관리한다. 비밀값 교체와 수동
재시작은 production 변경이므로 서버 운영 절차에서만 수행한다.

## 라이선스

[MIT](LICENSE)
