# Build Stage
FROM python:3.11.18-alpine3.24@sha256:bb1d70c1df30d8ee1e05214dbe82d6cdfd29833c1f47ce917f20991aca93acaa AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Runtime Stage
FROM python:3.11.18-alpine3.24@sha256:bb1d70c1df30d8ee1e05214dbe82d6cdfd29833c1f47ce917f20991aca93acaa AS runner

WORKDIR /app
# Numeric uid so Kubernetes runAsNonRoot can verify the user without guessing.
RUN addgroup -S -g 10001 app && adduser -S -u 10001 -G app app
COPY --from=builder /install /usr/local
COPY app/ ./app/

# Argo CD Image Updater's newest-build strategy compares the image config
# creation time. Consume the commit SHA in a real instruction so BuildKit cannot
# reuse a stale final timestamp when every application layer is cached.
ARG VCS_REF
RUN test -n "$VCS_REF"
LABEL org.opencontainers.image.revision="$VCS_REF"

ENV PYTHONUNBUFFERED=1

USER app
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
