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

CMD ["python", "-m", "uvicorn", "surplus_ai.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
