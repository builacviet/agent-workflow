FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da
ARG CODEX_VERSION=0.161.0
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates openssl \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global @openai/codex@${CODEX_VERSION} \
    && test "$(codex --version)" = "codex-cli ${CODEX_VERSION}" \
    && python3 -m venv /opt/testenv \
    && /opt/testenv/bin/pip install --no-cache-dir 'pytest>=8,<10' 'mcp==1.30.0' \
    && useradd --uid 10001 --create-home coder
ENV PATH=/opt/testenv/bin:$PATH HOME=/home/coder CODEX_HOME=/home/coder/.codex
COPY src /opt/worker-src
ENV PYTHONPATH=/opt/worker-src
COPY docker/coder_entry.py /opt/coder_entry.py
USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["python", "/opt/coder_entry.py"]
