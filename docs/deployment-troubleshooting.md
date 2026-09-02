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

## 2026-09-03: cached image timestamp stalled an automated rollout

### Context, expected behavior, and impact

After the GitHub Actions dependency update at `ad0c810`, CI and the ARM64 image publication succeeded. The expected next step was an Image Updater write-back from `sha-cb2b9bf9e44ef4c6404acacc69957cfebd992726` to `sha-ad0c810f3c5149c2091a01daaa0aad4db0f0cca3` within the configured two-minute interval.

The write-back did not occur. Production remained healthy on the preceding `cb2b9bf` image, so there was no external outage, but the new revision was not deployed and the delivery pipeline was no longer advancing automatically.

### Reproduction and evidence

1. Publish two different commit-SHA tags while all Dockerfile application layers are cache hits.
2. Inspect both image configs rather than only their registry push times or OCI labels.
3. Observe that the revision labels differ but the image config `Created` values are identical.

For `cb2b9bf` and `ad0c810`, GHCR recorded distinct publication times and distinct `org.opencontainers.image.revision` labels. Both image configs nevertheless reported `Created=2026-09-02T14:33:14.388763412Z`. The `ad0c810` tag was available and `latest` pointed to it, while the Helm value and main branch remained on `cb2b9bf` for more than ten Image Updater intervals.

### Root cause and alternatives

The `newest-build` strategy compares image build timestamps. BuildKit reused the final cached application layer, so the image config retained its earlier creation time even though metadata labels and registry tags were new. With equal build timestamps, Image Updater could not order the immutable SHA tags reliably.

Changing to an alphabetical strategy was rejected because Git SHAs do not sort chronologically. A mutable deployment tag was rejected because it would weaken revision traceability. Disabling the entire build cache would refresh the timestamp but would make every release slower and more expensive.

### Resolution

The runtime stage now consumes the commit SHA through a build argument in an executed Dockerfile instruction and records the same value as the OCI revision label. Each commit therefore invalidates only the final lightweight step, preserving dependency and application-layer cache reuse while producing an orderable image creation time. The existing immutable `sha-<40-hex>` tags and `newest-build` strategy remain unchanged.

### Validation and regression prevention

Validation requires all of the following before the incident is closed:

- CI tests and the ARM64 image build succeed.
- The new image config has the expected revision label and a creation time later than `cb2b9bf`.
- Image Updater writes the new SHA tag to `deploy/charts/ssu-ai-service/values.yaml`.
- ArgoCD completes the rollout and both `/health` and `/ready` return 200.

Future deployment verification must compare the image config creation time as well as the tag and registry publication time. A green image-publish job alone does not prove that a `newest-build` rollout candidate can be ordered.

### Remaining risk and review questions

Image Updater still depends on registry metadata and its polling loop; the Git write-back remains the authoritative signal that a revision was selected. During an operational review, verify why a changed OCI label did not change the image build timestamp, why a Git SHA cannot use alphabetical ordering, and how a commit-scoped cache-busting step preserves most BuildKit cache value.
