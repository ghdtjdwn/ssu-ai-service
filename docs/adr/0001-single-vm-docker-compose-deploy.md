# ADR 0001 - k3s/ArgoCD에서 단일 VM Docker Compose + 타이머 기반 이미지 반영으로 전환

- **Status**: Accepted (2026-10-15 이관 완료, 운영 중)
- **Date**: 2026-11-09 (결정·이관은 2026-10-15)
- **Scope**: 운영 환경 전체. 이 저장소에서는 `deploy/`(chart·ArgoCD Application 폐기, `deploy/README.md`
  신설), `.github/workflows/helm.yml` 삭제, `Dockerfile`·CI의 Image Updater 전제 제거.
- **Supersedes**: 이 저장소에는 선행 ADR이 없다. k3s 구축과 GitOps 도입 결정은 플랫폼 저장소
  [ssuMCP ADR 0007](https://github.com/ghdtjdwn/ssuMCP/blob/main/docs/adr/0007-prod-deploy-oracle-k3s.md),
  [ADR 0008](https://github.com/ghdtjdwn/ssuMCP/blob/main/docs/adr/0008-gitops-argocd-helm.md)에 있고,
  이 서비스는 그 구성을 그대로 따랐다([`deploy/archive/k3s/`](../../deploy/archive/k3s/)).

## Context

2026-07-01부터 이 서비스는 Oracle Cloud의 k3s 단일 노드(2 OCPU / 12GB / 부트 200GB)에서 Helm chart +
ArgoCD로 배포됐다. CI가 `sha-<커밋>` 이미지를 GHCR에 올리면 ArgoCD Image Updater가 태그를
`values.yaml`에 write-back하고 ArgoCD가 동기화하는 GitOps 경로였다. 이 경로는 두 번의 장애 분석을
남겼다([2026-07-15](../deployment-troubleshooting.md#2026-07-15-stale-argocd-owner-blocked-the-readiness-rollout),
[2026-09-03](../deployment-troubleshooting.md#2026-09-03-cached-image-timestamp-stalled-an-automated-rollout)).

2026-10-15 이관 작업 기록에 적힌 목표는 다음과 같다.

> 목표: 기존 2 OCPU/12GB/200GB k3s 서버의 개인 앱 전부를 무료 한도 안의 1 OCPU/6GB/50GB 서버에서 동시에
> 실제 동작시키기

즉 Oracle Cloud Always Free 한도(A1 코어·메모리·블록 스토리지) 안에서 개인 서비스를 모두 돌리고 남은
한도를 다른 프로젝트에 쓰기 위해 서버를 1 OCPU / 6GB 한 대로 줄이는 것이 이유였다. 기존 서버에서는 CPU
부족으로 ssuAgent의 새 이미지가 Pending 상태로 남아 운영에 반영되지 못한 일도 있었다. 가져가지 않기로
한 것은 다음과 같이 기록돼 있다.

> 모니터링(Prometheus/Grafana/Loki/Tempo/promtail), k3s, ArgoCD, cert-manager는 새 서버로 가져가지 않는다.

## Decision

- 새 VM(1 vCPU / 약 6GB RAM, ARM64) 한 대에서 모든 서비스를 Docker Compose 프로젝트로 실행한다. 진입점은
  Caddy 하나이며 TLS는 Caddy가 Let's Encrypt로 자동 발급·갱신한다(cert-manager, Traefik 대체).
- 이 서비스는 컨테이너 1개, 메모리 상한 256MiB로 실행한다. 비밀값은 서버 측 환경 파일로 주입한다.
- 새 버전 반영은 서버 쪽 systemd 타이머(15분 간격)가 맡는다. 타이머는 `main` 최근 커밋 중 GHCR에
  `sha-<커밋>` 태그가 있는 가장 최신 것을 골라 digest를 고정해 pull·교체하고, 공개 주소의 `GET /ready`가
  200이 아니면 이전 이미지로 되돌린다. 서버에는 GitHub·레지스트리 자격증명이 없다(공개 저장소·공개 패키지만
  읽음). CI가 서버에 접속하는 push 배포도 두지 않는다.
- Helm chart와 ArgoCD Application은 `deploy/archive/k3s/`로 옮겨 참고용으로만 남기고, chart를 lint하던
  CI job은 삭제한다. 이미지 빌드·게시 CI와 `sha-<40hex>` 태그 규칙은 그대로 둔다.
- `/health`가 `build_sha`를 돌려주게 해 어느 커밋이 떠 있는지 공개 엔드포인트에서 확인한다(#50).

## Consequences

얻은 것

- 운영 구성 요소가 k3s + Traefik + cert-manager + ArgoCD + Image Updater에서 Docker + Compose + Caddy +
  systemd 타이머로 줄었다. 컨트롤 플레인과 GitOps 컨트롤러가 차지하던 자원이 없어져 6GB 안에서 모든
  서비스를 함께 돌릴 수 있다. 이 서비스의 실사용 메모리는 약 60MiB다.
- 배포 실패 시 복구가 배포 스크립트 안에 들어 있다. 공개 주소 확인에 실패하면 자동으로 이전 digest로
  돌아가고, 같은 digest는 다시 시도하지 않는다.
- Image Updater의 태그 정렬 문제(2026-09-03)가 사라졌다. 반영 대상은 커밋 순서로 고르고 digest로
  고정하므로 이미지 생성 시각에 의존하지 않는다.

잃은 것

- 선언적 복제·자동 복구가 없다. HPA/PDB는 물론 Deployment rollout도 없고, Compose는 unhealthy 컨테이너를
  자동 재시작하지 않는다(`restart: unless-stopped`는 프로세스 종료에만 반응한다).
- GitOps write-back이 없다. 어느 이미지가 운영 중인지 Git 이력으로 알 수 없고, 서버의 배포 이력 로그와
  `/health`의 `build_sha`로 확인한다.
- 반영까지 최대 15분(+CI 이미지 빌드 시간)이 걸린다. Image Updater의 2분 간격보다 느리다.
- 모니터링 스택이 없다. Docker healthcheck와 공개 주소 응답 외의 관측 수단은 없고 알림도 없다.
- 메모리 예산이 빠듯하다. 서비스별 `mem_limit`과 `oom_score_adj`로 우선순위를 정하고 스왑 2GiB를 OOM 안전망으로
  둔다. 이 서비스는 상태가 없어 OOM 시 먼저 희생되는 쪽이다.
- VM 한 대가 단일 장애점이다. 이전 구성도 단일 노드였으므로 가용성 수준은 같다.

이 저장소의 `README.md` 배포 절, `deploy/README.md`, `Dockerfile` 주석, CI 주석은 이 결정에 맞춰 고쳤다.
실행 정의 자체는 비공개 인프라 저장소에서 관리한다.
