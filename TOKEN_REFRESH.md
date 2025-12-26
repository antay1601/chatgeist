# Обновление OAuth токена Claude

## Как работает авторизация Claude CLI

### Структура credentials

Claude CLI хранит OAuth credentials в файле `~/.claude/.credentials.json`:

```json
{
  "claudeAiOauth": {
    "accessToken": "sk-ant-oat01-...",     // Токен для API запросов (живёт ~8 часов)
    "refreshToken": "sk-ant-ort01-...",    // Токен для обновления (живёт дольше)
    "expiresAt": 1766698175473,            // Время истечения в миллисекундах
    "scopes": [...],
    "subscriptionType": "pro",
    "rateLimitTier": "..."
  }
}
```

### Время жизни токенов

- **Access Token**: ~8 часов
- **Refresh Token**: несколько недель/месяцев

---

## Обновление на Mac (работает)

### Автоматическое (Claude CLI сам обновляет)

При каждом запуске Claude CLI проверяет токен и автоматически обновляет его через браузерную сессию, используя сохранённые cookies.

### Ручное обновление

```bash
# Перелогиниться (открывает браузер)
claude logout
claude login

# Или просто запустить Claude - он сам обновит при необходимости
claude "test"
```

### Синхронизация в файл

```bash
# Скрипт sync_claude_credentials.sh извлекает из macOS Keychain
./sync_claude_credentials.sh
```

Keychain хранит credentials надёжнее чем файл, и Claude CLI на Mac автоматически обновляет их там.

---

## Обновление на сервере (проблема)

### Почему не работает автообновление

1. **Нет браузера** - Claude CLI использует браузер для OAuth flow
2. **Cloudflare блокирует curl** - OAuth API защищён Cloudflare JavaScript challenge
3. **Нет Keychain** - на Linux нет macOS Keychain

### Что мы пробовали

```bash
# Прямой запрос к OAuth API - блокируется Cloudflare
curl -X POST "https://console.anthropic.com/api/oauth/token" \
  -H "Content-Type: application/json" \
  -d '{"grant_type":"refresh_token","refresh_token":"...","client_id":"..."}'

# Результат: HTML страница "Just a moment..." от Cloudflare
```

### Текущая архитектура на сервере

```
Docker контейнер (claude-sandbox)
├── /home/node/.claude/.credentials.json  <- монтируется с хоста
├── /usr/local/bin/refresh_token.sh       <- скрипт обновления (не работает из-за Cloudflare)
└── cron (каждый час)                     <- запускает refresh_token.sh

Хост (сервер)
└── /home/bot/chatgeist/.claude-docker/credentials.json
```

---

## Решения

### Вариант 1: Anthropic API Key (рекомендуется)

Использовать API ключ вместо OAuth. API ключ не истекает.

```bash
# На сервере
echo "ANTHROPIC_API_KEY=sk-ant-api03-..." >> /home/bot/chatgeist/.env
systemctl restart chatgeist-bot
```

**Плюсы:**
- Не требует обновления
- Не блокируется Cloudflare
- Работает везде

**Минусы:**
- Требует платную подписку API (отдельно от Pro/Max)

### Вариант 2: Синхронизация с Mac

Настроить автоматическую синхронизацию credentials с Mac на сервер.

**На Mac (crontab -e):**
```bash
# Каждые 4 часа синхронизировать
0 */4 * * * cd /path/to/chatgeist && ./sync_claude_credentials.sh && scp .claude-docker/credentials.json bot@SERVER_IP:~/chatgeist/.claude-docker/
```

**Плюсы:**
- Использует существующую Pro/Max подписку
- Mac автоматически обновляет токены

**Минусы:**
- Требует работающий Mac
- Требует SSH ключ без пароля

### Вариант 3: Headless браузер на сервере

Установить Puppeteer/Playwright для обхода Cloudflare.

```bash
# Установить в Docker
npm install puppeteer

# Скрипт обновления через headless Chrome
node refresh_token_browser.js
```

**Плюсы:**
- Полностью автономно на сервере

**Минусы:**
- Сложная настройка
- Большой размер Docker образа (+400MB)
- Может сломаться при изменении Cloudflare

### Вариант 4: Ручное обновление

При истечении токена вручную копировать credentials с Mac.

```bash
# На Mac
./sync_claude_credentials.sh
cat .claude-docker/credentials.json | pbcopy

# На сервере
cat > /home/bot/chatgeist/.claude-docker/credentials.json << 'EOF'
<вставить>
EOF
chown 1000:1000 /home/bot/chatgeist/.claude-docker/credentials.json
```

---

## Мониторинг

### Проверить срок действия токена

```bash
# На сервере
docker exec claude-sandbox cat /home/node/.claude/.credentials.json | \
  python3 -c "import sys,json,datetime; d=json.load(sys.stdin); \
  print('Expires:', datetime.datetime.fromtimestamp(d['claudeAiOauth']['expiresAt']/1000))"
```

### Логи обновления

```bash
docker exec claude-sandbox cat /var/log/token_refresh.log
```

### Статус бота

```bash
systemctl status chatgeist-bot
journalctl -u chatgeist-bot -f
```

---

## Итог

| Метод | Автономность | Сложность | Рекомендация |
|-------|-------------|-----------|--------------|
| API Key | Полная | Низкая | Лучший вариант |
| Синхро с Mac | Частичная | Средняя | Если нет API Key |
| Headless браузер | Полная | Высокая | Для продвинутых |
| Ручное | Нет | Низкая | Временное решение |
