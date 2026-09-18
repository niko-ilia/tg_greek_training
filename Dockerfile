# Pinned by digest; re-resolve with:
#   docker pull python:3.12-slim && docker inspect python:3.12-slim --format '{{.RepoDigests}}'
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY alembic.ini .
COPY migrations/ migrations/
COPY seeds/ seeds/
COPY src/ src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1

RUN groupadd -g 1000 appuser && useradd -u 1000 -g appuser appuser
USER appuser

CMD ["sh", "-c", "alembic upgrade head && python -m greek_trainer"]
