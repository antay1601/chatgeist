#!/bin/bash
# Wrapper для Claude CLI - читает свежий токен из credentials.json перед каждым вызовом

CREDENTIALS_FILE="/home/node/.claude/.credentials.json"

# Читаем свежий токен из файла (если файл существует)
if [ -f "$CREDENTIALS_FILE" ]; then
    TOKEN=$(node -e "try { console.log(JSON.parse(require('fs').readFileSync('$CREDENTIALS_FILE')).claudeAiOauth.accessToken) } catch(e) { }" 2>/dev/null)
    if [ -n "$TOKEN" ]; then
        export CLAUDE_CODE_OAUTH_TOKEN="$TOKEN"
    fi
fi

# Вызываем настоящий claude с всеми аргументами
exec /usr/local/bin/claude-real "$@"
