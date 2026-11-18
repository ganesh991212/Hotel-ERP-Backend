# API image for Render (staging and production). Same image everywhere; behaviour comes from env vars.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/release.sh scripts/start.sh ./scripts/

RUN useradd --system --uid 10001 api
USER api

EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
