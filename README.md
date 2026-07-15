# ssu-ai-service

[![CI](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml)

> 🇺🇸 English version: [README.en.md](README.en.md)

> 🧩 **숭실대 캠퍼스 AI 플랫폼** (4-서비스 중 하나) · [ssuMCP](https://github.com/ghdtjdwn/ssuMCP) · [ssuAI](https://github.com/ghdtjdwn/ssuAI) · [ssuAgent](https://github.com/ghdtjdwn/ssuAgent) · **ssu-ai-service** · 🟢 [Live](https://ssuai.vercel.app)

숭실대학교 AI 플랫폼의 **B2B 임베딩 서빙 게이트웨이**. FastAPI로 텍스트 임베딩(Gemini `gemini-embedding-001`, Matryoshka 768차원)을 단일 엔드포인트로 노출한다. ssuMCP/ssuAI 본체와 분리된 독립 서비스로, "모델 서빙 표면을 따로 둘 때의 인증·키 위생 설계"를 증명하는 포트폴리오 조각이다.

## 엔드포인트

| 메서드 | 경로 | 인증 | 설명 |
|---|---|---|---|
| GET | `/health` | 없음 | 프로세스 liveness. 기존 호환 필드로 Gemini 설정 여부만 보고하며 업스트림은 호출하지 않음 |
| GET | `/ready` | 없음 | 로컬 readiness. 필수 키가 없거나 보호 설정이 잘못되면 503, 업스트림은 호출하지 않음 |
| POST | `/v1/embeddings` | `X-API-Key` 필수 | `{"text": "..."}` → `{"embedding": [...768], "dimension": 768}` |

## 보안 설계 (2026-06-30, 2026-07-15 하드닝)

이 서비스는 초기 버전의 보안 결함을 교정하고 요청 보호 경계를 추가했다.

1. **업스트림 키를 URL 쿼리스트링에서 헤더로 이동** — 기존에는 Gemini 키를 `?key=...`로 보내 액세스 로그·프록시에 평문 노출 위험이 있었다. 이제 `Authorization: Bearer` 헤더로 전송한다.
2. **인바운드 인증 게이트(fail-closed)** — `/v1/embeddings`는 `X-API-Key`가 `SSUAI_SERVICE_API_KEY`와 일치해야 한다. 키가 **미설정이면 열어두는 게 아니라 401로 닫는다**(누구나 호출해 LLM 비용을 소진하던 표면 차단). cf. ssuAgent `/agent`의 `AGENT_API_KEY` 게이트와 같은 원칙.
3. **업스트림 에러 비반사·비로깅** — 기존에는 Gemini 응답 본문을 그대로 호출자에게 되돌려줬다. 이제 원문은 응답과 애플리케이션 로그 양쪽에 남기지 않고 상태 코드나 malformed 신호만 기록하며, 호출자에게는 일반화된 메시지(`502 embedding upstream error` 등)만 반환한다.
4. **입력 경계** — 공백뿐인 입력을 거부하고 기본 8,000자 상한을 적용한다. 현재 `gemini-embedding-001`의 2,048-token 한도와 무제한 요청 본문에 따른 메모리·비용 위험을 함께 고려한 전단 보호다.
5. **키별 사용량 제한** — 프로세스별 sliding window로 기본 분당 60회, 동시 4회를 허용한다. 제한 상태에는 API 키 원문이 아니라 SHA-256 식별자만 보관하고 초과 시 429를 반환한다.
6. **liveness/readiness 분리** — `/health`는 프로세스 생존만, `/ready`는 두 필수 키와 보호 설정을 검사한다. readiness는 Gemini를 호출하지 않아 프로브가 비용이나 외부 장애 전파를 만들지 않는다.

## 환경 변수

| 변수 | 설명 |
|---|---|
| `SSUAI_GEMINI_API_KEY` | (업스트림) Gemini 임베딩 API 키. 미설정 시 `/v1/embeddings`는 503 |
| `SSUAI_SERVICE_API_KEY` | (인바운드) 호출자가 `X-API-Key`로 제시할 자격증명. 미설정 시 게이트가 fail-closed(401) |
| `SSUAI_MAX_TEXT_LENGTH` | 입력 문자 수 상한. 양의 정수, 기본값 `8000` |
| `SSUAI_RATE_LIMIT_REQUESTS` | 키별 window 내 요청 수. 양의 정수, 기본값 `60` |
| `SSUAI_RATE_LIMIT_WINDOW_SECONDS` | rate-limit window(초). 양의 정수, 기본값 `60` |
| `SSUAI_MAX_CONCURRENT_REQUESTS` | 키별 동시 처리 상한. 양의 정수, 기본값 `4` |

보호 설정이 정수가 아니거나 0 이하이면 안전한 기본값으로 동작하되 `/ready`는 503을 반환한다. 현재 production replica는 1개라 프로세스 로컬 제한이 서비스 전체 제한과 같다. 여러 replica로 확장할 때는 Redis 등 공유 저장소 기반 limiter로 전환해야 전역 한도를 유지할 수 있다.

## 실행

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 또는 컨테이너
docker build -t ssu-ai-service . && docker run -p 8000:8000 \
  -e SSUAI_GEMINI_API_KEY=... -e SSUAI_SERVICE_API_KEY=... ssu-ai-service
```

## 테스트

```bash
pip install -r requirements-dev.txt
pytest -q
```

16개 케이스를 검증한다(실제 업스트림은 목킹): liveness/readiness 분리와 무업스트림 프로브, 인증 fail-closed, 빈 입력·길이 상한, 키별 rate/concurrency 제한, 정상 768차원 응답, 업스트림 오류 비반사를 포함한다.

## 배포 (2026-07-02 prod 라이브, 2026-07-15 하드닝)

k3s 클러스터(`ssuai-prod` 네임스페이스)에 GitOps로 배포되어 있다: main push → GitHub Actions가 arm64 이미지를 ghcr에 빌드/푸시 → ArgoCD Image Updater가 `sha-<hash>` 태그를 values.yaml에 되커밋 → 자동 sync.

- **런타임 하드닝**: 컨테이너는 non-root(uid 10001, `runAsNonRoot` 강제), capability 전부 drop, privilege escalation 차단.
- **시크릿**: `ssu-ai-service-secrets`(클러스터에서 수동 생성, 커밋 금지)는 필수 참조다. 누락 시 Pod가 시작되지 않으며 키 이름은 위 환경 변수 표와 동일하다.
- **재현 가능한 공급망**: runtime/dev Python 의존성은 exact version, Python base image는 official image digest, GitHub Actions는 Node.js 24 기반 major의 full commit SHA로 고정한다.
- **노출 범위**: 외부 공개 라이브 — **<https://ssu-ai-service.duckdns.org>** (Let's Encrypt TLS). 인증 없는 호출은 fail-closed로 401. 헬스: `curl https://ssu-ai-service.duckdns.org/health` → `{"status":"healthy","gemini_configured":true}`.
- 차트/ArgoCD 매니페스트: [`deploy/`](deploy/).
- 배포 장애 분석과 복구 절차: [`docs/deployment-troubleshooting.md`](docs/deployment-troubleshooting.md).
