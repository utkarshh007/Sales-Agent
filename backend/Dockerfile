FROM python:3.13-slim

# tesseract for OCR of scanned tender documents
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Headless Chromium for buyer pages that build their tender lists with JavaScript (Phase 3).
# Installed to a shared path so the unprivileged runtime user can launch it.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install --with-deps --only-shell chromium && chmod -R o+rx /ms-playwright

# Local embedding model for semantic matching (Phase 4), baked in so production never downloads at runtime.
ENV EMBEDDING_CACHE_DIR=/models HF_HUB_DISABLE_TELEMETRY=1
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')"  && chmod -R o+rX /models

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

# least privilege: run as an unprivileged user; documents live on a mounted volume
RUN useradd --create-home --uid 10001 tender && mkdir -p /data/documents && chown -R tender /data
USER tender
ENV DOCUMENT_STORAGE_DIR=/data/documents

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m app.cli seed && uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
