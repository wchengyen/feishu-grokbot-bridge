FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY bridge ./bridge
RUN pip install --no-cache-dir . \
    && useradd --create-home --shell /usr/sbin/nologin bridge \
    && mkdir -p /data/outbox \
    && chown -R bridge:bridge /data \
    && chmod 700 /data /data/outbox

USER bridge
ENV BRIDGE_OUTBOX_DIR=/data/outbox \
    BRIDGE_DB_PATH=/data/bridge.db \
    BRIDGE_HEALTH_FILE=/data/health.json \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

# One container only. Feishu cluster mode delivers each event to a random client.
CMD ["python", "-m", "bridge.main"]
