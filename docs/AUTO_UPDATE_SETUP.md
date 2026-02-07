# Автоматическое обновление ChatGeist

Обновлено: 2026-02-07

## Архитектура

Бот использует **Anthropic API** напрямую с API ключом. Не требует Docker, OAuth токенов или синхронизации credentials.

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Telegram Bot   │────▶│  Anthropic API   │────▶│  SQLite DB      │
│  (bot_multi.py) │     │  (API Key)       │     │  (databases/)   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## Что настроено

### 1. Автообновление историй чатов (Mac)

**Агент:** `com.chatgeist.update`
**Интервал:** каждый час
**Файл:** `~/Library/LaunchAgents/com.chatgeist.update.plist`

Запускает `update_manager.py` для инкрементального обновления всех чатов из `targets.json`.

### 2. Автообновление историй чатов (Сервер)

**Механизм:** cron
**Интервал:** каждые 4 часа

```cron
0 */4 * * * cd /home/bot/chatgeist && /home/bot/.local/bin/uv run python update_manager.py >> logs/update.log 2>&1
```

## Файлы

| Файл | Назначение |
|------|------------|
| `bot_multi.py` | Telegram бот с Anthropic API |
| `update_manager.py` | Скачивание истории чатов |
| `.env` | API ключи (TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY) |
| `logs/update.log` | Лог обновления чатов |

## Команды управления (Mac)

```bash
# Статус агента
launchctl list | grep chatgeist

# Принудительный запуск
launchctl start com.chatgeist.update

# Остановить/Запустить
launchctl unload ~/Library/LaunchAgents/com.chatgeist.update.plist
launchctl load ~/Library/LaunchAgents/com.chatgeist.update.plist

# Логи
tail -f logs/update.log
```

## Запуск бота локально

```bash
uv run bot_multi.py
```

Или в фоне:
```bash
nohup uv run bot_multi.py > logs/bot.log 2>&1 &
```

---

# Настройка на сервере Ubuntu

**Сервер:** 104.248.18.118 (hoodyalko-dev-2)
**Пользователь:** bot
**Путь:** `/home/bot/chatgeist`

## Telegram бот (systemd)

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

## Команды управления

```bash
# SSH подключение
ssh -i ~/.ssh/id_hoodyalko_dev root@104.248.18.118

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
├── bot_multi.py              # Telegram бот (Anthropic API)
├── update_manager.py         # Обновление чатов
├── databases/                # SQLite базы
│   └── uaitinvalencia.db
├── prompts/                  # Промпты для Claude
│   ├── base.md
│   └── skills/
├── logs/                     # Логи
│   └── update.log
├── targets.json              # Конфигурация чатов
└── .env                      # TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY
```

## Требования

### Mac
- macOS с активной сессией пользователя
- uv установлен
- Telegram API credentials (для update_manager.py)

### Сервер
- Ubuntu 22.04+
- uv установлен (`/home/bot/.local/bin/uv`)
- systemd для управления ботом
- cron для автообновления чатов
- ANTHROPIC_API_KEY в .env
