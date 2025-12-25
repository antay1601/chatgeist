#!/bin/bash
# Скрипт для автоматического обновления OAuth токена Claude
# Запускается по cron каждый час

set -e

CREDENTIALS_FILE="${CREDENTIALS_FILE:-/home/node/.claude/.credentials.json}"
LOG_PREFIX="[token-refresh]"

log() {
    echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') $1"
}

error() {
    echo "$LOG_PREFIX $(date '+%Y-%m-%d %H:%M:%S') ERROR: $1" >&2
}

# Проверяем наличие файла credentials
if [ ! -f "$CREDENTIALS_FILE" ]; then
    error "Credentials file not found: $CREDENTIALS_FILE"
    exit 1
fi

# Извлекаем данные из credentials
REFRESH_TOKEN=$(cat "$CREDENTIALS_FILE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('claudeAiOauth',{}).get('refreshToken',''))" 2>/dev/null)
EXPIRES_AT=$(cat "$CREDENTIALS_FILE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('claudeAiOauth',{}).get('expiresAt',0))" 2>/dev/null)

if [ -z "$REFRESH_TOKEN" ]; then
    error "No refresh token found in credentials"
    exit 1
fi

# Проверяем, нужно ли обновлять токен (за 30 минут до истечения)
CURRENT_TIME=$(python3 -c "import time; print(int(time.time() * 1000))")
THRESHOLD=$((30 * 60 * 1000))  # 30 минут в миллисекундах
TIME_LEFT=$((EXPIRES_AT - CURRENT_TIME))

if [ "$TIME_LEFT" -gt "$THRESHOLD" ]; then
    MINUTES_LEFT=$((TIME_LEFT / 60000))
    log "Token still valid for $MINUTES_LEFT minutes, skipping refresh"
    exit 0
fi

log "Token expires soon (${TIME_LEFT}ms left), refreshing..."

# Claude Code CLI Client ID
CLIENT_ID="9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# Делаем запрос на обновление токена
RESPONSE=$(curl -s -X POST "https://console.anthropic.com/api/oauth/token" \
    -H "Content-Type: application/json" \
    -d "{
        \"grant_type\": \"refresh_token\",
        \"refresh_token\": \"$REFRESH_TOKEN\",
        \"client_id\": \"$CLIENT_ID\"
    }" 2>&1)

# Проверяем успешность запроса
if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if 'access_token' in d else 1)" 2>/dev/null; then
    # Извлекаем новые данные
    NEW_ACCESS_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
    NEW_REFRESH_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('refresh_token', ''))" 2>/dev/null || echo "")
    NEW_EXPIRES_IN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('expires_in', 28800))" 2>/dev/null)

    # Вычисляем новое время истечения
    NEW_EXPIRES_AT=$(python3 -c "import time; print(int(time.time() * 1000) + $NEW_EXPIRES_IN * 1000)")

    # Обновляем credentials.json
    python3 << EOF
import json

with open("$CREDENTIALS_FILE", 'r') as f:
    creds = json.load(f)

creds['claudeAiOauth']['accessToken'] = "$NEW_ACCESS_TOKEN"
creds['claudeAiOauth']['expiresAt'] = $NEW_EXPIRES_AT

# Обновляем refresh token если получили новый
new_refresh = "$NEW_REFRESH_TOKEN"
if new_refresh:
    creds['claudeAiOauth']['refreshToken'] = new_refresh

with open("$CREDENTIALS_FILE", 'w') as f:
    json.dump(creds, f, indent=2)

print("Credentials updated successfully")
EOF

    log "Token refreshed successfully. New expiry: $(python3 -c "import datetime; print(datetime.datetime.fromtimestamp($NEW_EXPIRES_AT/1000).strftime('%Y-%m-%d %H:%M:%S'))")"
else
    ERROR_MSG=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error_description', d.get('error', 'Unknown error')))" 2>/dev/null || echo "$RESPONSE")
    error "Failed to refresh token: $ERROR_MSG"
    exit 1
fi
