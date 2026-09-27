FROM python:3.11-slim
WORKDIR /app
COPY src/z0int /app/src/z0int
COPY manifests/worker_routing.v1.json /app/manifests/worker_routing.v1.json
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 Z0INT_HOME=/nonexistent
RUN chmod -R a+rX /app
USER 65532:65532
ENTRYPOINT ["python", "-m", "z0int.remote_executor", "serve"]
