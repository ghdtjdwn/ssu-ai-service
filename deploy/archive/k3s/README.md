# 폐기된 k3s / ArgoCD 배포 정의

이 폴더의 Helm chart(`charts/ssu-ai-service/`)와 ArgoCD Application(`argocd/`)은 2026-07-01(첫 배포 커밋
`e5280e7`)부터 2026-10-15까지 운영 환경의 desired state였다. 2026-10-15에 서비스가 단일 VM의 Docker Compose로
이관되면서 k3s, ArgoCD, ArgoCD Image Updater, cert-manager는 폐기됐고 이 정의는 더 이상 어디에도 적용되지 않는다.

- 참고용으로만 남긴다. CI는 이 chart를 lint·render하지 않는다.
- `values.yaml`의 `image.tag`는 Image Updater가 마지막으로 기록한 값이며 현재 운영 이미지와 무관하다.
- 현재 배포 경로는 [`deploy/README.md`](../../README.md), 전환 이유와 trade-off는
  [ADR 0001](../../../docs/adr/0001-single-vm-docker-compose-deploy.md)에 있다.
