# Image with Python and this bot's libraries only.
# The bot's own files (code, .env / config.json, database, logs) are NOT copied in:
# docker-compose.yml links this folder into the container at /app, so edits here
# take effect after a restart:  docker compose restart <bot>
FROM python:3.13-slim

# Fonts for the image-drawing bots (they fall back to DejaVu when Arial/Segoe UI are missing)
# and timezone data so TZ=Europe/Amsterdam works.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
