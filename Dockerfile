# Local P4-A runtime image. DEVELOPMENT / loopback use only — not an internet deployment.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SURPLUS_AI_LOG_DIR=/app/logs

WORKDIR /app

RUN groupadd --system surplus \
    && useradd --system --gid surplus --home-dir /app --no-create-home surplus \
    && mkdir -p /app/logs

COPY pyproject.toml README.md alembic.ini ./
COPY surplus_ai ./surplus_ai

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e ".[api,auth]" \
    && chown -R surplus:surplus /app

USER surplus

EXPOSE 8000

# Explicit launcher: proxy_headers off unless SURPLUS_AI_TRUST_PROXY_HEADERS=true
# with a validated SURPLUS_AI_FORWARDED_ALLOW_IPS allowlist (never Uvicorn defaults).
CMD ["python", "-m", "surplus_ai.api.runtime"]
