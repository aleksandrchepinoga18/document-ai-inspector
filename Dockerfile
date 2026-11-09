FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MLFLOW_DISABLE_TELEMETRY=true \
    HOME=/home/inspector

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 inspector \
    && useradd --uid 10001 --gid inspector --create-home inspector \
    && mkdir -p /app /mlflow/artifacts \
    && chown -R inspector:inspector /app /mlflow

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Deliberate allowlist: .env, local results and arbitrary DOCX are never copied.
COPY --chown=inspector:inspector backend/ ./backend/
COPY --chown=inspector:inspector frontend/ ./frontend/
COPY --chown=inspector:inspector evaluation/ ./evaluation/
COPY --chown=inspector:inspector tests/ ./tests/
COPY --chown=inspector:inspector pytest.ini ./pytest.ini

USER inspector
EXPOSE 8000 8501 5000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
