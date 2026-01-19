#!/bin/bash
# Синхронизация Claude OAuth токена с Mac на сервер
# Запускать вручную или по cron

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# === НАСТРОЙКИ СЕРВЕРА ===
SERVER_USER="root"
SERVER_HOST="104.248.18.118"
SERVER_PATH="/home/bot/chatgeist"
SSH_KEY="$HOME/.ssh/id_hoodyalko_dev"
SSH_OPTS="-i $SSH_KEY"
# =========================

CREDENTIALS_DIR="$SCRIPT_DIR/.claude-docker"
CREDENTIALS_FILE="$CREDENTIALS_DIR/credentials.json"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $1" >&2
}

# Проверка SSH ключа
if [ ! -f "$SSH_KEY" ]; then
    error "SSH ключ не найден: $SSH_KEY"
    exit 1
fi

log "Шаг 1: Извлечение credentials из macOS Keychain..."

mkdir -p "$CREDENTIALS_DIR"

if ! security find-generic-password -s "Claude Code-credentials" -w > "$CREDENTIALS_FILE" 2>/dev/null; then
    error "Не удалось получить credentials из Keychain"
    echo "Выполните: claude login"
    exit 1
fi

chmod 600 "$CREDENTIALS_FILE"

# Проверяем валидность JSON
if ! python3 -c "import json; json.load(open('$CREDENTIALS_FILE'))" 2>/dev/null; then
    error "Credentials не являются валидным JSON"
    rm -f "$CREDENTIALS_FILE"
    exit 1
fi

# Показываем срок действия
EXPIRES_AT=$(python3 -c "import json; print(json.load(open('$CREDENTIALS_FILE')).get('claudeAiOauth', {}).get('expiresAt', 0))" 2>/dev/null || echo "0")
if [ "$EXPIRES_AT" != "0" ]; then
    EXPIRES_DATE=$(python3 -c "import datetime; print(datetime.datetime.fromtimestamp($EXPIRES_AT/1000).strftime('%Y-%m-%d %H:%M:%S'))")
    log "Access token истекает: $EXPIRES_DATE"
fi

log "Шаг 2: Отправка на сервер $SERVER_HOST..."

# Извлекаем access token для .env
ACCESS_TOKEN=$(python3 -c "import json; print(json.load(open('$CREDENTIALS_FILE')).get('claudeAiOauth', {}).get('accessToken', ''))")

# Добавляем lastSyncedAt для отслеживания возраста refresh_token
log "Добавляем lastSyncedAt в credentials..."
python3 << EOF
import json
with open('$CREDENTIALS_FILE', 'r') as f:
    creds = json.load(f)
creds['claudeAiOauth']['lastSyncedAt'] = int(__import__('time').time() * 1000)
with open('$CREDENTIALS_FILE', 'w') as f:
    json.dump(creds, f, indent=2)
EOF

# Создаём директорию на сервере если не существует
ssh $SSH_OPTS "$SERVER_USER@$SERVER_HOST" "mkdir -p $SERVER_PATH/.claude-docker"

# Копируем credentials
scp $SSH_OPTS "$CREDENTIALS_FILE" "$SERVER_USER@$SERVER_HOST:$SERVER_PATH/.claude-docker/credentials.json"

# Устанавливаем права (UID 1000 = node user в контейнере, владелец bot)
ssh $SSH_OPTS "$SERVER_USER@$SERVER_HOST" "chown bot:bot $SERVER_PATH/.claude-docker/credentials.json && chmod 600 $SERVER_PATH/.claude-docker/credentials.json"

# Обновляем .env с токеном (Claude CLI использует переменную окружения)
ssh $SSH_OPTS "$SERVER_USER@$SERVER_HOST" "cd $SERVER_PATH && grep -v CLAUDE_CODE_OAUTH_TOKEN .env > .env.tmp 2>/dev/null || true; mv .env.tmp .env 2>/dev/null || true; echo 'CLAUDE_CODE_OAUTH_TOKEN=$ACCESS_TOKEN' >> .env"

log "Шаг 3: Пересоздание Docker контейнера..."

ssh $SSH_OPTS "$SERVER_USER@$SERVER_HOST" "cd $SERVER_PATH && docker compose down && docker compose up -d" 2>/dev/null || \
ssh $SSH_OPTS "$SERVER_USER@$SERVER_HOST" "cd $SERVER_PATH && docker-compose down && docker-compose up -d" 2>/dev/null || \
log "Docker не перезапущен (возможно, не запущен). Перезапустите вручную."

log "Готово! Токен синхронизирован."

# Проверяем работу Claude на сервере
log "Проверка Claude на сервере..."
if ssh $SSH_OPTS "$SERVER_USER@$SERVER_HOST" "docker exec claude-sandbox claude --print 'say OK'" 2>/dev/null | grep -q "OK"; then
    log "Claude работает!"
else
    log "Claude не отвечает. Проверьте: docker logs claude-sandbox"
fi
