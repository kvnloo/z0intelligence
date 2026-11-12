FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
WORKDIR /app
COPY src/z0int /app/src/z0int
COPY manifests/worker_routing.v1.json /app/manifests/worker_routing.v1.json
COPY benchmarks/manifests/api-provider-registry.json /app/benchmarks/manifests/api-provider-registry.json
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 Z0INT_HOME=/nonexistent
RUN chmod -R a+rX /app
USER 65532:65532
ENTRYPOINT ["python", "-m", "z0int.remote_executor", "serve"]
