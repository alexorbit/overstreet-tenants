FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANG=C.UTF-8

# Sistema: ffmpeg audio, sqlite3 tooling, curl healthcheck, tini signals, ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libsndfile1 sqlite3 curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

# Qdrant binary sidecar
ARG QDRANT_VERSION=v1.12.4
ARG TARGETARCH=amd64
RUN curl -fsSL "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin qdrant \
    && chmod +x /usr/local/bin/qdrant

WORKDIR /app

# Deps Python (camada cacheada)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código
COPY bot.py ingest.py entrypoint.sh ./
COPY overstreet/ ./overstreet/
COPY dashboard/ ./dashboard/
RUN chmod +x entrypoint.sh

# Data dirs
RUN mkdir -p /app/data/global /app/data/tenant /app/qdrant_storage

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
