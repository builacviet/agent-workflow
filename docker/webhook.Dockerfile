FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY src/agent_workflow /app/src/agent_workflow
RUN python -m pip install --no-cache-dir ".[postgres]" \
    && useradd --uid 10002 --create-home --shell /usr/sbin/nologin webhook \
    && mkdir -p /tmp \
    && chown webhook:webhook /tmp

ENV HOME=/home/webhook
USER webhook
EXPOSE 8081
CMD ["python", "-m", "agent_workflow.interfaces.webhook.server"]
