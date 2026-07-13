# ssu-ai-service

[![CI](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml/badge.svg)](https://github.com/ghdtjdwn/ssu-ai-service/actions/workflows/ci.yml)

> 🇺🇸 English version: [README.en.md](README.en.md)

> 🧩 **숭실대 캠퍼스 AI 플랫폼** (4-서비스 중 하나) · [ssuMCP](https://github.com/ghdtjdwn/ssuMCP) · [ssuAI](https://github.com/ghdtjdwn/ssuAI) · [ssuAgent](https://github.com/ghdtjdwn/ssuAgent) · **ssu-ai-service** · 🟢 [Live](https://ssuai.vercel.app)

숭실대학교 AI 플랫폼의 **B2B 임베딩 서빙 게이트웨이**. FastAPI로 텍스트 임베딩(Gemini `gemini-embedding-001`, Matryoshka 768차원)을 단일 엔드포인트로 노출한다. ssuMCP/ssuAI 본체와 분리된 독립 서비스로, "모델 서빙 표면을 따로 둘 때의 인증·키 위생 설계"를 증명하는 포트폴리오 조각이다.

## 엔드포인트

| 메서드 | 경로 | 인증 | 설명 |
|---|---|---|---|
| GET | `/health` | 없음 | 헬스 체크(k8s liveness). 키 값은 노출하지 않고 설정 여부만 보고 |
| POST | `/v1/embeddings` | `X-API-Key` 필수 | `{"text": "..."}` → `{"embedding": [...768], "dimension": 768}` |

## 보안 설계 (2026-06-30 하드닝)

이 서비스는 초기 버전에 세 가지 결함이 있었고, 살리면서 모두 교정했다.

1. **업스트림 키를 URL 쿼리스트링에서 헤더로 이동** — 기존에는 Gemini 키를 `?key=...`로 보내 액세스 로그·프록시에 평문 노출 위험이 있었다. 이제 `Authorization: Bearer` 헤더로 전송한다.
2. **인바운드 인증 게이트(fail-closed)** — `/v1/embeddings`는 `X-API-Key`가 `SSUAI_SERVICE_API_KEY`와 일치해야 한다. 키가 **미설정이면 열어두는 게 아니라 401로 닫는다**(누구나 호출해 LLM 비용을 소진하던 표면 차단). cf. ssuAgent `/agent`의 `AGENT_API_KEY` 게이트와 같은 원칙.
3. **업스트림 에러 비반사** — 기존에는 Gemini 응답 본문을 그대로 호출자에게 되돌려줬다. 이제 업스트림 상세는 **서버 로그에만** 남기고, 호출자에게는 일반화된 메시지(`502 embedding upstream error` 등)만 반환한다.

## 환경 변수

| 변수 | 설명 |
|---|---|
| `SSUAI_GEMINI_API_KEY` | (업스트림) Gemini 임베딩 API 키. 미설정 시 `/v1/embeddings`는 503 |
| `SSUAI_SERVICE_API_KEY` | (인바운드) 호출자가 `X-API-Key`로 제시할 자격증명. 미설정 시 게이트가 fail-closed(401) |

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

7개 케이스를 검증한다(실제 업스트림은 목킹): 헬스, 인바운드 키 미제시 → 401, 잘못된 키 → 401, **비-ASCII 키 → 401**(latin-1/UTF-8 비교 함정), 키 일치+업스트림 미설정 → 503, **정상 경로에서 768차원으로 캡(Matryoshka 절단) end-to-end**, 업스트림 이상 응답 → 502(내부 노출 없이).

## 배포 (2026-07-02 prod 라이브)

k3s 클러스터(`ssuai-prod` 네임스페이스)에 GitOps로 배포되어 있다: main push → GitHub Actions가 arm64 이미지를 ghcr에 빌드/푸시 → ArgoCD Image Updater가 `sha-<hash>` 태그를 values.yaml에 되커밋 → 자동 sync.

- **런타임 하드닝**: 컨테이너는 non-root(uid 10001, `runAsNonRoot` 강제), capability 전부 drop, privilege escalation 차단.
- **시크릿**: `ssu-ai-service-secrets`(클러스터에서 수동 생성, 커밋 금지) — 키 이름은 위 환경 변수 표와 동일.
- **노출 범위**: 외부 공개 라이브 — **<https://ssu-ai-service.duckdns.org>** (Let's Encrypt TLS). 인증 없는 호출은 fail-closed로 401. 헬스: `curl https://ssu-ai-service.duckdns.org/health` → `{"status":"healthy",...}`.
- 차트/ArgoCD 매니페스트: [`deploy/`](deploy/).
