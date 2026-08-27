FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite + parquet cache live here; mount a persistent volume at this path
ENV STORAGE_DIR=/app/storage

EXPOSE 8600
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8600}"]
