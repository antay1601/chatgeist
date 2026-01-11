# Автоматическое обновление ChatGeist

Настроено: 2026-01-10 (Mac), 2026-01-11 (Сервер)

## Что настроено

### 1. Автообновление историй чатов

**Агент:** `com.chatgeist.update`
**Интервал:** каждый час
**Файл:** `~/Library/LaunchAgents/com.chatgeist.update.plist`

Запускает `update_manager.py` для инкрементального обновления всех чатов из `targets.json`.

### 2. Синхронизация OAuth токена

**Агент:** `com.chatgeist.sync-token`
**Интервал:** каждые 4 часа
**Файл:** `~/Library/LaunchAgents/com.chatgeist.sync-token.plist`

Запускает `sync_token.py`:
1. Пингует Claude CLI (триггерит обновление токена если истекает)
2. Копирует токен из macOS Keychain в `.env`
3. Перезапускает Docker контейнер (`down` + `up`, не `restart`)

> **Важно:** `docker compose restart` не перечитывает `.env`, поэтому используется `down` + `up -d`.

### 3. Docker compose изменения

В `docker-compose.yml` включена передача токена через переменную окружения:
```yaml
environment:
  - CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}
```

## Файлы

| Файл | Назначение |
|------|------------|
| `sync_token.py` | Скрипт синхронизации токена |
| `logs/update.log` | Лог обновления чатов |
| `logs/sync_token.log` | Лог синхронизации токена |

## Команды управления

```bash
# Статус агентов
launchctl list | grep chatgeist

# Принудительный запуск
launchctl start com.chatgeist.update
launchctl start com.chatgeist.sync-token

# Остановить
launchctl unload ~/Library/LaunchAgents/com.chatgeist.update.plist
launchctl unload ~/Library/LaunchAgents/com.chatgeist.sync-token.plist

# Запустить
launchctl load ~/Library/LaunchAgents/com.chatgeist.update.plist
launchctl load ~/Library/LaunchAgents/com.chatgeist.sync-token.plist

# Логи
tail -f logs/update.log
tail -f logs/sync_token.log
```

## Ручная синхронизация токена

```bash
uv run python sync_token.py --restart
```

## Как работает OAuth токен

```
┌─────────────────────────────────────────────────────────────────┐
│                    sync_token.py (каждые 4 часа)                │
├─────────────────────────────────────────────────────────────────┤
│  1. claude --print "ping"  ──▶  Триггерит обновление токена    │
│  2. Keychain обновляется автоматически если токен почти истёк  │
│  3. Копирует свежий токен в .env                               │
│  4. Перезапускает Docker                                        │
└─────────────────────────────────────────────────────────────────┘
```

- Access Token живёт ~6 часов
- Refresh Token живёт недели/месяцы
- Claude CLI автоматически обновляет Access Token используя Refresh Token
- Токен хранится в macOS Keychain (`Claude Code-credentials`)

## Запуск бота

Бот не запускается автоматически. Запуск вручную:

```bash
uv run bot_multi.py
```

Или в фоне:
```bash
nohup uv run bot_multi.py > logs/bot.log 2>&1 &
```

## Известные особенности

### docker compose restart vs down/up

`docker compose restart` **не перечитывает** `.env` файл. Если токен обновился в `.env`, нужно:

```bash
docker compose down && docker compose up -d
```

Скрипт `sync_token.py` уже делает это правильно.

### Режим сна Mac

Настроено отключение сна при питании от сети:

```bash
sudo pmset -c sleep 0 disksleep 0 displaysleep 0
```

Проверить: `pmset -g | grep sleep`

## Требования (Mac)

- macOS с активной сессией пользователя
- Docker Desktop запущен
- Mac подключён к питанию (для работы 24/7)
- Активная подписка Claude (для OAuth)

---

# Настройка на сервере Ubuntu

**Сервер:** 104.248.18.118 (hoodyalko-dev-2)
**Пользователь:** bot
**Путь:** `/home/bot/chatgeist`

## Что настроено

### Автообновление историй чатов

**Механизм:** cron
**Интервал:** каждые 4 часа (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC)

```cron
0 */4 * * * cd /home/bot/chatgeist && /home/bot/.local/bin/uv run python update_manager.py >> logs/update.log 2>&1
```

### Telegram бот (systemd)

**Сервис:** `chatgeist-bot.service`
**Статус:** enabled, auto-start

```bash
# Статус
systemctl status chatgeist-bot

# Перезапуск
sudo systemctl restart chatgeist-bot

# Логи
journalctl -u chatgeist-bot -f
```

### Docker Claude sandbox

Работает аналогично Mac - Claude CLI с OAuth токеном.

## Команды управления

```bash
# SSH подключение
ssh -i ~/.ssh/id_hoodyalko_dev root@104.248.18.118

# Или как пользователь bot
ssh -i ~/.ssh/id_hoodyalko_dev bot@104.248.18.118

# Проверить crontab
crontab -l

# Лог обновлений
tail -f ~/chatgeist/logs/update.log

# Ручное обновление
cd ~/chatgeist && uv run python update_manager.py

# Статус бота
systemctl status chatgeist-bot
```

## Структура на сервере

```
/home/bot/chatgeist/
├── bot_multi.py              # Telegram бот
├── update_manager.py         # Обновление чатов
├── databases/                # SQLite базы
│   └── uaitinvalencia.db
├── logs/                     # Логи
│   └── update.log
├── targets.json              # Конфигурация чатов
└── .env                      # Секреты
```

## Требования (Сервер)

- Ubuntu 22.04+
- Docker установлен
- uv установлен (`/home/bot/.local/bin/uv`)
- systemd для управления ботом
- cron для автообновления чатов
