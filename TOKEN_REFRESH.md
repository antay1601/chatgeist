# Обновление OAuth токена Claude

## Автоматическое обновление (рекомендуется)

Токен автоматически обновляется внутри Docker контейнера:
- **Cron**: каждый час в 00 минут проверяет и обновляет токен
- **При старте**: проверяет токен при запуске контейнера
- **Порог**: обновление за 30 минут до истечения

### Мониторинг

```bash
# Логи обновления токена
docker exec claude-sandbox cat /var/log/token_refresh.log

# Ручной запуск обновления
docker exec claude-sandbox /usr/local/bin/refresh_token.sh

# Проверка статуса cron
docker exec claude-sandbox service cron status
```

### Проверка срока действия токена

```bash
cat .claude-docker/credentials.json | python3 -c "
import sys,json,datetime
d=json.load(sys.stdin)
ts=d['claudeAiOauth']['expiresAt']
print('Expires:', datetime.datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M:%S'))
"
```

## Ручное обновление (при проблемах)

Если автообновление не сработало:

### 1. Перелогиньтесь в Claude CLI (на Mac)

```bash
claude logout
claude login
```

### 2. Синхронизируйте credentials

```bash
./sync_claude_credentials.sh
```

### 3. Перезапустите Docker

```bash
docker compose down && docker compose up -d
```

## Архитектура

```
Mac (хост)                    Docker контейнер
┌─────────────────────┐      ┌─────────────────────┐
│ macOS Keychain      │      │ /home/node/.claude/ │
│ (Claude credentials)│─────►│ .credentials.json   │
└─────────────────────┘      │                     │
  sync_claude_credentials.sh │ cron (каждый час)   │
                             │   ↓                 │
                             │ refresh_token.sh    │
                             │   ↓                 │
                             │ OAuth API refresh   │
                             └─────────────────────┘
```

## OAuth API

- **Endpoint**: `https://console.anthropic.com/api/oauth/token`
- **Client ID**: `9d1c250a-e61b-44d9-88ed-5944d1962f5e`
- **Время жизни токена**: ~8 часов
