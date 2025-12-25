#!/bin/bash
# Синхронизация Claude credentials из macOS Keychain в файл для Docker
# Запускать при обновлении токена или перед первым запуском Docker

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CREDENTIALS_DIR="$SCRIPT_DIR/.claude-docker"
CREDENTIALS_FILE="$CREDENTIALS_DIR/credentials.json"

echo "Синхронизация Claude credentials..."

# Создаем директорию если не существует
mkdir -p "$CREDENTIALS_DIR"

# Извлекаем credentials из Keychain
if ! security find-generic-password -s "Claude Code-credentials" -w > "$CREDENTIALS_FILE" 2>/dev/null; then
    echo "Ошибка: Не удалось получить credentials из Keychain"
    echo "Убедитесь, что вы авторизованы в Claude Code: claude login"
    exit 1
fi

# Устанавливаем права доступа
chmod 600 "$CREDENTIALS_FILE"

# Проверяем что файл валидный JSON
if ! python3 -c "import json; json.load(open('$CREDENTIALS_FILE'))" 2>/dev/null; then
    echo "Ошибка: Полученный файл не является валидным JSON"
    rm -f "$CREDENTIALS_FILE"
    exit 1
fi

# Показываем информацию
echo "Credentials сохранены в: $CREDENTIALS_FILE"

# Извлекаем дату истечения токена
EXPIRES_AT=$(python3 -c "import json; print(json.load(open('$CREDENTIALS_FILE')).get('claudeAiOauth', {}).get('expiresAt', 0))" 2>/dev/null || echo "0")
if [ "$EXPIRES_AT" != "0" ]; then
    # Конвертируем timestamp в дату
    EXPIRES_DATE=$(python3 -c "import datetime; print(datetime.datetime.fromtimestamp($EXPIRES_AT/1000).strftime('%Y-%m-%d %H:%M:%S'))" 2>/dev/null || echo "неизвестно")
    echo "Access token истекает: $EXPIRES_DATE"
fi

echo ""
echo "Для применения изменений перезапустите Docker:"
echo "  docker compose down && docker compose up -d"
