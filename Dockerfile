# Build Stage
FROM python:3.11.18-alpine3.24@sha256:bb1d70c1df30d8ee1e05214dbe82d6cdfd29833c1f47ce917f20991aca93acaa AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Runtime Stage
FROM python:3.11.18-alpine3.24@sha256:bb1d70c1df30d8ee1e05214dbe82d6cdfd29833c1f47ce917f20991aca93acaa AS runner

WORKDIR /app
# Numeric uid so the runtime can verify the non-root user without a name lookup.
RUN addgroup -S -g 10001 app && adduser -S -u 10001 -G app app
COPY --from=builder /install /usr/local
COPY app/ ./app/

# VCS_REF is required. The image records it as the OCI revision label and the
# process reports it as build_sha in GET /health, so the commit a running
# container was built from can be confirmed from the public endpoint.
ARG VCS_REF
RUN test -n "$VCS_REF"
LABEL org.opencontainers.image.revision="$VCS_REF"
ENV SSUAI_BUILD_SHA=$VCS_REF

ENV PYTHONUNBUFFERED=1

USER app
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
