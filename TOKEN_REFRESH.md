# Обновление OAuth токена Claude

## Как работает авторизация

### Структура токенов

Claude CLI использует OAuth для авторизации:

| Токен | Время жизни | Назначение |
|-------|-------------|------------|
| Access Token | ~8 часов | Авторизация API запросов |
| Refresh Token | ~30 дней | Получение нового Access Token |

### Где хранятся токены

**На Mac (локальная разработка):**
- macOS Keychain → `Claude Code-credentials`
- Claude CLI автоматически обновляет токены через браузер

**На сервере Ubuntu:**
- Переменная окружения `CLAUDE_CODE_OAUTH_TOKEN` в `.env`
- Файл `.claude-docker/credentials.json` (для автообновления)

---

## Текущая архитектура на сервере

```
Хост (Ubuntu сервер)
├── /home/bot/chatgeist/
│   ├── .env                              ← CLAUDE_CODE_OAUTH_TOKEN (начальный токен)
│   └── .claude-docker/credentials.json   ← refresh_token (для автообновления)

Docker контейнер (claude-sandbox)
├── /home/node/.claude/.credentials.json  ← монтируется, обновляется cron-ом
├── /usr/local/bin/claude                 ← wrapper-скрипт (читает токен из credentials.json)
├── /usr/local/bin/claude-real            ← настоящий Claude CLI
├── /opt/token-refresh/
│   └── refresh_token_browser.js          ← скрипт автообновления (curl)
└── cron (каждый час)                     ← запускает скрипт
```

### Как работает wrapper

При каждом вызове `claude`:
1. Wrapper читает свежий `accessToken` из `credentials.json`
2. Устанавливает `CLAUDE_CODE_OAUTH_TOKEN`
3. Вызывает настоящий Claude CLI

Это позволяет cron обновлять токен без перезапуска контейнера.

---

## Как работает автообновление

### 1. Скрипт обновления токена

Файл: `/opt/token-refresh/refresh_token_browser.js`

**Алгоритм:**
1. Читает `credentials.json`
2. Проверяет `expiresAt` — если до истечения > 30 минут, пропускает
3. Делает POST запрос к `https://console.anthropic.com/api/oauth/token`:
   ```json
   {
     "grant_type": "refresh_token",
     "refresh_token": "<refresh_token>",
     "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
   }
   ```
4. Получает новый `access_token` и `expires_in`
5. Обновляет `credentials.json`

**Особенность:** Используется `curl -L` для следования редиректам Cloudflare (302).

### 2. Cron задача

```cron
0 * * * * root CREDENTIALS_FILE=/home/node/.claude/.credentials.json node /opt/token-refresh/refresh_token_browser.js >> /var/log/token_refresh.log 2>&1
```

Запускается каждый час в 00 минут.

### 3. Проверка логов

```bash
# На сервере
docker exec claude-sandbox cat /var/log/token_refresh.log

# Последние записи
docker exec claude-sandbox tail -20 /var/log/token_refresh.log
```

---

## Синхронизация с Mac

### Когда нужна синхронизация

1. **Refresh token истёк** (~30 дней) — автообновление перестаёт работать
2. **Первоначальная настройка** — токенов на сервере ещё нет
3. **После `claude login`** — получены новые токены

### Как синхронизировать

На Mac:
```bash
cd /path/to/chatgeist
./sync_to_server.sh
```

**Что делает скрипт:**
1. Извлекает credentials из macOS Keychain
2. Копирует `credentials.json` на сервер (для refresh_token)
3. Обновляет `.env` с `CLAUDE_CODE_OAUTH_TOKEN` (для Claude CLI)
4. Пересоздаёт Docker контейнер
5. Проверяет работу Claude

### Автоматическая синхронизация (опционально)

На Mac добавить в crontab (`crontab -e`):
```bash
# Каждые 6 часов синхронизировать токен
0 */6 * * * cd /path/to/chatgeist && ./sync_to_server.sh >> /tmp/sync_claude.log 2>&1
```

---

## Диагностика проблем

### Проверить текущий токен

```bash
# Срок действия токена
docker exec claude-sandbox cat /home/node/.claude/.credentials.json | \
  python3 -c "import sys,json,datetime; d=json.load(sys.stdin); \
  print('Expires:', datetime.datetime.fromtimestamp(d['claudeAiOauth']['expiresAt']/1000))"

# Проверить переменную окружения
docker exec claude-sandbox printenv CLAUDE_CODE_OAUTH_TOKEN | head -c 50
```

### Проверить работу Claude

```bash
docker exec claude-sandbox claude --print 'say OK'
```

### Принудительное обновление токена

```bash
docker exec claude-sandbox node /opt/token-refresh/refresh_token_browser.js
```

### Мониторинг возраста refresh_token

Скрипт автоматически отслеживает время с последней синхронизации:

```bash
# В логах будут сообщения:
# OK (< 25 дней):
[token-refresh] Refresh token OK. Synced 5 days ago, ~25 days left.

# WARNING (25-30 дней):
[token-refresh] WARNING: Refresh token expires in ~3 days (synced 27 days ago).
[token-refresh] WARNING: Plan to run sync_to_server.sh from Mac soon.

# ERROR (> 30 дней):
[token-refresh] ERROR: REFRESH TOKEN EXPIRED! Last synced 32 days ago.
[token-refresh] ERROR: Run sync_to_server.sh from Mac immediately!
```

### Типичные ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| `401 authentication_error` | Access token истёк | Подождать — cron обновит автоматически |
| `not_found_error` | Refresh token истёк | На Mac: `claude login`, затем `./sync_to_server.sh` |
| `Token still valid for X minutes` | Токен ещё действителен | Всё в порядке |
| `lastSyncedAt not set` | Первая синхронизация | Запустить `./sync_to_server.sh` |
| `WARNING: Refresh token expires in ~X days` | Скоро истечёт | Запланировать синхронизацию |

---

## Важные файлы

| Файл | Назначение |
|------|------------|
| `sync_to_server.sh` | Синхронизация токенов с Mac на сервер |
| `token-refresh/refresh_token_browser.js` | Автообновление токена (curl) |
| `claude-wrapper.sh` | Wrapper для чтения свежего токена |
| `docker-compose.yml` | Конфигурация контейнера |
| `Dockerfile.claude-sandbox` | Образ с cron, wrapper и скриптом обновления |
| `.env` | Начальный access token |
| `.claude-docker/credentials.json` | Токены для автообновления |

---

## Схема работы

```
┌─────────────────────────────────────────────────────────────────┐
│                         Mac (разработка)                        │
│                                                                 │
│  Claude CLI ←→ Keychain ←→ Anthropic OAuth                     │
│       ↓                                                         │
│  sync_to_server.sh (раз в 2 недели по launchd)                 │
│       ↓                                                         │
└───────┼─────────────────────────────────────────────────────────┘
        │ SSH + SCP
        ↓
┌─────────────────────────────────────────────────────────────────┐
│                      Сервер Ubuntu                              │
│                                                                 │
│  .claude-docker/credentials.json                                │
│       ↓                                                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Docker: claude-sandbox                      │   │
│  │                                                          │   │
│  │  Telegram Bot → claude (wrapper)                         │   │
│  │                    ↓                                     │   │
│  │            читает credentials.json                       │   │
│  │                    ↓                                     │   │
│  │            claude-real ──→ Anthropic API                 │   │
│  │                                                          │   │
│  │  cron (каждый час)                                       │   │
│  │       ↓                                                  │   │
│  │  refresh_token_browser.js                                │   │
│  │       ↓                                                  │   │
│  │  curl POST /api/oauth/token                              │   │
│  │       ↓                                                  │   │
│  │  Обновляет credentials.json (accessToken + expiresAt)    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Итог

| Компонент | Что делает | Когда |
|-----------|------------|-------|
| `sync_to_server.sh` | Синхронизирует токены с Mac | Вручную или по cron |
| `refresh_token_browser.js` | Автообновляет access_token | Каждый час (cron) |
| `CLAUDE_CODE_OAUTH_TOKEN` | Авторизует Claude CLI | При каждом запросе |

**Важно:** При истечении refresh_token (раз в ~30 дней) нужно:
1. На Mac выполнить `claude login`
2. Запустить `./sync_to_server.sh`
