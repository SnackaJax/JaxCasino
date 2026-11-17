# JaxCasino: Python, the bot's libraries and the bot's code.
#
# Your settings (.env), the database, logs and voice models are NOT in the image. docker-compose.yml passes .env in
# as environment variables and keeps the database, logs and voices in volumes, so they survive updates.

# The admin panel's page (admin_panel/web, a React app) is built here, so neither the repository nor the bot needs
# Node.js. It's plain HTML/JS/CSS, the same on every platform, so it's built once on the build machine's platform.
FROM --platform=$BUILDPLATFORM node:24-slim AS admin-panel
WORKDIR /panel/web
COPY admin_panel/web/package.json admin_panel/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY admin_panel/web/ ./
RUN npm run build

FROM python:3.13-slim

# Fonts for the images the bot draws (it falls back to DejaVu when Arial/Segoe UI are missing) and timezone data,
# so TZ=Europe/Amsterdam and the like work.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
COPY --from=admin-panel /panel/static admin_panel/static

# Run as a normal user. /data (database and logs) and /app/voices are the folders the bot writes to.
RUN useradd --create-home --uid 1000 jaxcasino \
    && mkdir -p /data /app/voices \
    && chown -R jaxcasino:jaxcasino /data /app/voices
USER jaxcasino

# The Activity's web server (ACTIVITY_PORT) and the admin panel (ADMIN_PANEL_PORT, off unless ADMIN_PANEL_ENABLED=1)
EXPOSE 8765 8780
# The bot shuts down cleanly on Ctrl+C (SIGINT); unfinished bets are refunded on the next start either way.
STOPSIGNAL SIGINT
CMD ["python", "bot.py"]
