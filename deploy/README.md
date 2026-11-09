# 배포

이 저장소는 애플리케이션 코드, `Dockerfile`, 이미지를 빌드해 GHCR에 올리는 CI까지만 담당한다. 컨테이너 실행
정의(Compose 파일, 타이머 유닛, 배포 스크립트, 비밀값)는 비공개 인프라 저장소에서 관리한다. 2026-10-15까지
쓰던 Helm chart와 ArgoCD Application은 [`archive/k3s/`](archive/k3s/)에 참고용으로 남아 있다.

## 운영 환경

- 단일 ARM64 VM(1 vCPU, 약 6GB RAM). 같은 VM에서 ssuMCP, ssuAgent 등 다른 서비스도 Docker Compose로 함께 돈다.
- 앞단 Caddy 하나가 TLS(Let's Encrypt 자동 발급·갱신)를 종료하고 Docker 네트워크를 통해 컨테이너의 8000 포트로
  프록시한다. 이 서비스는 호스트 포트를 열지 않는다.
- 컨테이너 1개, 메모리 상한 256MiB(실사용 약 60MiB), `restart: unless-stopped`, Docker healthcheck, 이미지는
  non-root 사용자로 실행한다(`Dockerfile`의 `USER app`).
- 비밀값(`SSUAI_GEMINI_API_KEY`, `SSUAI_SERVICE_API_KEY`)은 저장소에 없는 서버 측 환경 파일로 주입한다.
- Prometheus/Grafana 같은 모니터링 스택은 없다. 상태 확인은 Docker healthcheck와 공개 주소 응답뿐이다.

## 새 커밋이 운영에 반영되는 경로

```text
main push
  → CI: ruff, pytest
  → CI: linux/arm64 이미지 빌드, ghcr.io/ghdtjdwn/ssu-ai-service:sha-<40hex> 게시
  → 서버 타이머(15분 간격): main 최근 커밋 중 GHCR에 sha-<커밋> 태그가 있는 가장 최신 커밋을 고른다
  → 현재 고정된 digest와 같으면 종료, 다르면 docker pull → digest 고정 → docker compose up -d --wait
  → 공개 주소 GET /ready == 200 확인(최대 3분)
  → 성공 기록. 실패하면 이전 이미지로 되돌리고 같은 확인을 다시 한다
```

- 서버에는 GitHub·레지스트리 자격증명이 없다. 공개 저장소의 커밋 목록과 공개 패키지만 읽는 pull 방식이며,
  CI가 서버에 접속하지 않는다.
- 머지 후 CI 이미지 job이 끝나고 나서 보통 15~16분 안에 교체된다.
- 문서·`deploy/` 변경은 이미지를 만들지 않는다(`ci.yml`의 `paths-ignore`). 타이머는 이미지가 있는 가장 최신
  커밋을 고르므로 이런 커밋은 그냥 건너뛴다.
- 확인에 실패해 되돌린 digest는 다시 시도하지 않는다. 수정 커밋을 올리면 새 태그로 다시 진행된다.

## 배포 확인

```bash
curl -sS https://ssu-ai-service.duckdns.org/health   # build_sha가 머지된 main 커밋과 같은지
curl -sS https://ssu-ai-service.duckdns.org/ready    # {"status":"ready"}
```

`/health`는 `GET`만 받는다(`HEAD`는 405). `build_sha`는 이미지 빌드 때 넘긴 `VCS_REF`이며, CI는 여기에
`github.sha`를 넣는다.

## 반영되지 않았을 때 보는 순서

1. `main`의 CI 실행에서 `Image (ghcr.io, ARM64)` job이 성공했는지, GHCR 패키지에 `sha-<커밋>` 태그가 생겼는지.
   문서만 바뀐 커밋은 이미지가 없는 것이 정상이다.
2. 서버의 배포 이력 로그(JSON Lines, 한 줄에 `time`, `service`, `result`, `from`, `to`). `result`는
   `success`, `rolled_back`, `failed`, `pull_failed` 중 하나다. `rolled_back`이면 새 이미지가 `/ready` 확인에
   실패해 이전 이미지로 돌아간 것이므로 컨테이너 로그에서 원인을 본다.
3. 타이머·서비스 유닛 상태(`systemctl list-timers`, `systemctl --failed`, 유닛 journal). 확인 실패 후 복구까지
   실패하면 유닛이 failed로 남는다.
4. 자동 반영을 잠시 끄는 플래그 파일이 서버에 있다. 켜져 있는지 확인한다.

되돌리기는 위의 자동 복구가 기본이고, 수동으로는 서버에서 이전 이미지 digest를 다시 고정해 `up -d`한다.
비밀값 교체·수동 재시작은 production 변경이므로 서버 운영 절차에서만 수행한다.

## 기록

- 전환 결정과 trade-off: [ADR 0001](../docs/adr/0001-single-vm-docker-compose-deploy.md)
- k3s/ArgoCD 시절 배포 장애 분석: [`docs/deployment-troubleshooting.md`](../docs/deployment-troubleshooting.md)
