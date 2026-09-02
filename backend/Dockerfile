# ---- python base ----------------------------------------------------------
# Everything the backend needs at runtime, with no frontend build in the way. The
# test image builds from here so running the suite does not require an npm install.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# tesseract is the OCR engine behind the card scanner (ADR-007). The Debian package
# includes English training data, which is all the title bar needs.
#
# libglib2.0-0 is for opencv-python-headless: the headless wheel drops the GUI
# bindings but still links glib, which python:3.12-slim does not ship.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr libtesseract-dev libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./

# ---- frontend build -------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
# npm ci, not npm install: the image ships exactly the lockfile the gates ran
# against, and two builds of the same commit get the same dependency tree.
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- test image -----------------------------------------------------------
# Built and run by docker-compose.test.yml. Exists because the OCR tests need the
# tesseract binary, and a development machine without it cannot honestly claim the
# scanner works.
FROM base AS tests

# DejaVu is what the synthetic card renderer draws title bars with.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ".[dev]"
COPY backend/tests ./tests

ENV DATA_DIR=/tmp/mtgvault-test \
    ENABLE_SCHEDULER=false

CMD ["python", "-m", "pytest", "-q"]

# ---- runtime --------------------------------------------------------------
FROM base AS app

COPY --from=web /web/dist ./static

RUN useradd --system --uid 10001 --create-home mtgvault \
    && mkdir -p /data \
    && chown -R mtgvault:mtgvault /srv /data
USER mtgvault

ENV DATA_DIR=/data \
    STATIC_DIR=/srv/static

EXPOSE 8000

# One worker only: APScheduler is in-process and has no distributed lock (ADR-014).
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
