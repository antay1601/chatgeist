# ChatGeist

Telegram-бот для анализа истории чатов с помощью Claude AI.

## Возможности

- Мониторинг нескольких Telegram-чатов/каналов
- Инкрементальное обновление данных
- AI-анализ через Claude в изолированном Docker-контейнере
- Полнотекстовый поиск по сообщениям

## Архитектура

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Telegram API   │────▶│ update_manager.py│────▶│  databases/*.db │
│  (Telethon)     │     │                  │     │  (SQLite)       │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Telegram Bot   │◀───▶│   bot_multi.py   │────▶│  Docker Claude  │
│  (пользователь) │     │                  │     │  (анализ)       │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## Требования

- Python 3.12+
- Docker
- uv (менеджер пакетов)

## Быстрый старт

### 1. Клонирование и установка

```bash
git clone <repo-url>
cd chatgeist
uv sync
```

### 2. Настройка окружения

Создайте файл `.env`:

```bash
# Telegram User API (https://my.telegram.org)
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash

# Telegram Bot Token (@BotFather)
TELEGRAM_BOT_TOKEN=your_bot_token

# Anthropic API Key (для bot_anthropic.py)
ANTHROPIC_API_KEY=sk-ant-api03-...

# Claude OAuth Token (для bot_multi.py с Docker)
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

### 3. Добавление чатов

```bash
# Добавить публичный канал
uv run python update_manager.py --add durov @durov

# Добавить приватную группу по ID
uv run python update_manager.py --add mygroup -1001234567890

# Посмотреть список
uv run python update_manager.py --list
```

### 4. Скачивание истории

```bash
# Все чаты
uv run python update_manager.py

# Конкретный чат
uv run python update_manager.py --chat durov

# С лимитом (для теста)
uv run python update_manager.py --chat durov --limit 1000
```

При первом запуске потребуется авторизация в Telegram (номер телефона + код).

### 5. Запуск Docker

```bash
docker compose up -d
```

### 6. Запуск бота

Есть две версии бота:

```bash
# Вариант 1: Прямой API (рекомендуется)
# Требует ANTHROPIC_API_KEY, не требует Docker
uv run bot_anthropic.py

# Вариант 2: Docker sandbox
# Требует запущенный Docker и CLAUDE_CODE_OAUTH_TOKEN
uv run bot_multi.py
```

## Использование бота

| Команда | Описание |
|---------|----------|
| `/chats` | Выбрать чат для анализа |
| `/current` | Текущий выбранный чат |
| `/help` | Справка |

После выбора чата просто отправляйте вопросы текстом:
- "Сколько сообщений в базе?"
- "Кто самый активный участник?"
- "О чём писали вчера?"
- "Найди сообщения про Python"

## Команды update_manager.py

```bash
uv run python update_manager.py              # Обновить все чаты
uv run python update_manager.py --chat NAME  # Обновить один чат
uv run python update_manager.py --full       # Полный дамп (не инкрементальный)
uv run python update_manager.py --list       # Список чатов
uv run python update_manager.py --add A T    # Добавить чат (alias, target)
uv run python update_manager.py --remove A   # Удалить чат
uv run python update_manager.py --stats      # Статистика БД
```

## Структура проекта

```
chatgeist/
├── bot_anthropic.py          # Telegram-бот (прямой Anthropic API)
├── bot_multi.py              # Telegram-бот (Docker sandbox)
├── update_manager.py         # Оркестратор обновлений
├── tg_dump_with_reactions.py # Дампер истории Telegram
├── jsonl_to_sqlite.py        # Конвертер JSONL → SQLite
├── targets.json              # Конфигурация чатов
├── docker-compose.yml        # Docker конфигурация
├── Dockerfile.claude-sandbox # Docker образ для Claude
├── pyproject.toml            # Зависимости Python
└── .env                      # Секреты (не в git)
```

## Развёртывание на сервере

```bash
# 1. Клонировать
git clone <repo> && cd chatgeist

# 2. Установить зависимости
uv sync

# 3. Создать .env с секретами
nano .env

# 4. Добавить чаты и скачать историю
uv run python update_manager.py --add channel @channel_name
uv run python update_manager.py

# 5. Запустить Docker
docker compose up -d

# 6. Запустить бота (в screen/tmux или systemd)
uv run bot_multi.py
```

### Автообновление через Cron

```bash
# Каждый час обновлять все чаты
0 * * * * cd /path/to/chatgeist && uv run python update_manager.py >> logs/update.log 2>&1
```

## Обновление OAuth токена

Токен Claude истекает периодически. Для обновления:

```bash
# macOS (из Keychain)
security find-generic-password -s "Claude Code-credentials" -w | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])"

# Обновить в .env и перезапустить Docker
docker compose down && docker compose up -d
```

## Лицензия

MIT
