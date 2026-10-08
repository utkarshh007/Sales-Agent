FROM python:3.13-slim

# tesseract for OCR of scanned tender documents
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

# least privilege: run as an unprivileged user; documents live on a mounted volume
RUN useradd --create-home --uid 10001 tender && mkdir -p /data/documents && chown -R tender /data
USER tender
ENV DOCUMENT_STORAGE_DIR=/data/documents

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m app.cli seed && uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
