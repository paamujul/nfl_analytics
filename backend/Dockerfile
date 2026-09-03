FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

# app.config mkdirs STORAGE_DIR at import time, so it must exist and be writable
# before dropping privileges or every process dies on import. On Cloud Run this
# path is tmpfs and counts against the memory limit; with Postgres the only
# thing that lands here is the nflverse parquet cache, in the refresh job.
ENV STORAGE_DIR=/app/storage
RUN useradd --system --create-home --uid 10001 nflapp \
    && mkdir -p "$STORAGE_DIR" \
    && chown -R nflapp:nflapp /app
USER nflapp

# Cloud Run injects PORT; 8600 is the local default. Jobs override the command
# entirely (python -m app.cli poll-once, refresh-nflverse, seed).
EXPOSE 8600
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8600}"]
