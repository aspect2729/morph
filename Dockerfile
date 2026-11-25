# Morph — public server image (runs on x86-64 and ARM, e.g. Oracle Cloud Ampere A1).
# Debian 13 (trixie): Pandoc 3, Tesseract 5, FFmpeg 7, LibreOffice 25.
FROM python:3.12-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MORPH_MODE=web \
    MORPH_DATA=/data \
    HF_HOME=/data/models \
    HOME=/tmp

# conversion engines + fonts: metric-compatible twins of Arial/Times/Courier (Liberation) and of
# Calibri/Cambria (Carlito/Caladea, the PowerPoint/Word defaults), plus common deck fonts, so Office files
# convert with the right widths and edited text can use the full version of the document's own font
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui libreoffice-draw-nogui \
        pandoc tesseract-ocr tesseract-ocr-eng ffmpeg ghostscript librsvg2-bin \
        fonts-liberation fonts-dejavu-core \
        fonts-crosextra-carlito fonts-crosextra-caladea \
        fonts-open-sans fonts-lato fonts-roboto-unhinted fonts-montserrat fonts-inter \
        tini curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY app ./app
COPY static ./static
COPY scripts ./scripts

# run as an unprivileged user; /data holds temp files and Recent (docker-compose mounts a volume there)
RUN useradd --system --uid 10001 --home /tmp morph && mkdir -p /data && chown -R morph /data
USER morph
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
# listens on $PORT when the host sets one (Antideploy), else 8000 (docker-compose)
CMD ["sh", "-c", "exec uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips '*' --timeout-keep-alive 30 --limit-concurrency 64"]
