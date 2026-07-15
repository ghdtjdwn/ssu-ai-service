# Deployment troubleshooting

## 2026-07-15: stale ArgoCD owner blocked the readiness rollout

### Context and impact

PR #8 separated `/health` liveness from fail-closed `/ready` readiness and changed the chart probe to `/ready`. The expected delivery sequence was CI test success, ARM64 image publication, Image Updater write-back, and an automated ArgoCD rollout.

The Application synced the new chart through GitHub's repository redirect, but its live `repoURL` and image-list annotation still referenced the previous `hoeongj` owner. Image Updater therefore skipped the application and left the old image tag in place. ArgoCD applied the new `/ready` probe to that old image, which returned 404, so the rollout stayed `Progressing`. The previous Ready pod continued serving traffic; there was no external outage.

### Evidence and root cause

- Main CI run `29382841943` passed pytest and published the ARM64 image for `6410160`.
- The live ImageUpdater reported one matched application but zero images considered.
- The live Application used `ghcr.io/hoeongj/ssu-ai-service`, while CI published `ghcr.io/ghdtjdwn/ssu-ai-service`.
- The replacement pod ran the old image and logged repeated `GET /ready 404` probe failures.
- After the owner correction, Image Updater wrote `861d552`, but the previous Argo operation was still waiting for the old-image Deployment to become healthy and could not start the new revision.

Repository redirects are sufficient for ArgoCD source fetches but do not rewrite container registry coordinates. The stale live Application was the root cause; the stuck operation was a secondary GitOps ordering deadlock.

### Resolution

1. Reapplied the version-controlled `deploy/argocd/application-ssu-ai-service.yaml`, correcting both the repository URL and GHCR image-list owner.
2. Confirmed Image Updater selected `sha-6410160d5c8030d134d81147f143e7d084a03e86` and committed the Helm value update as `861d552`.
3. Terminated only the stale Argo operation and requested a hard refresh so automated sync could reconcile the latest Git revision.
4. Waited for the Deployment rollout and verified the new pod before accepting completion.

### Validation

- Deployment image: `sha-6410160d5c8030d134d81147f143e7d084a03e86`
- Pod: 1/1 Ready, zero restarts
- ArgoCD: `Synced/Healthy`
- `GET /health`: 200 with configured upstream
- `GET /ready`: 200
- Missing and invalid `X-API-Key`: 401
- Valid authenticated embedding: 200 with 768 dimensions

### Prevention and remaining risk

The Application manifest is the source of truth and now carries the current owner in both `repoURL` and image-list. Deployment verification must inspect the running image tag, not only ArgoCD sync status. A readiness-path change should be released with its supporting image available before considering the rollout complete.

The rate and concurrency limiter remains process-local. That is service-wide while production has one replica; horizontal scaling requires a shared limiter such as Redis.

### Interview prompts

- Why did Git source sync work while image discovery failed? GitHub redirected the repository URL, but GHCR image names are independent immutable coordinates.
- Why did the rollout not recover immediately after Image Updater wrote the new tag? The prior Argo operation was still waiting for an impossible old-image readiness condition, so it had to be terminated before the latest revision could reconcile.
- How was downtime avoided? RollingUpdate retained the previous Ready pod until the corrected image passed `/ready`.
