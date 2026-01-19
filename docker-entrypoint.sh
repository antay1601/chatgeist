#!/bin/bash
# Docker entrypoint для Claude sandbox
# Запускает cron и основной процесс

set -e

echo "[entrypoint] Starting cron service..."
service cron start

# Запускаем обновление токена при старте
echo "[entrypoint] Running initial token refresh check..."
CREDENTIALS_FILE=/home/node/.claude/.credentials.json node /opt/token-refresh/refresh_token_browser.js || echo "[entrypoint] Warning: Initial token refresh failed (may need valid credentials)"

echo "[entrypoint] Starting main process: $@"
exec "$@"
