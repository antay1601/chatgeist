# Система промптов ChatGeist

## Структура файлов

```
prompts/
├── base.md                 # Базовый промпт (схема БД, SQL-примеры, форматирование)
├── README.md               # Эта документация
└── skills/
    ├── _index.md           # Индекс всех skills
    ├── dossier.md          # Досье на пользователя
    ├── summary.md          # Саммари за период
    ├── search.md           # Поиск по сообщениям
    └── top.md              # Топы и рейтинги
```

## Как это работает

1. При получении запроса бот загружает `base.md` — базовый промпт со схемой БД и инструкциями
2. Функция `detect_skill()` анализирует текст запроса на наличие триггеров
3. Если триггер найден — загружается соответствующий skill-промпт
4. Промпты комбинируются: `base + skill + история диалога + вопрос`

## Доступные Skills

| Skill | Триггеры | Описание |
|-------|----------|----------|
| `dossier` | досье, профиль, кто такой, кто такая, информация о | Создание досье на пользователя |
| `summary` | саммари, дайджест, о чём говорили, что обсуждали, главное за | Обзор обсуждений за период |
| `search` | найди, поиск, где упоминается, кто писал про | Поиск по сообщениям |
| `top` | топ, рейтинг, самые активные, популярные, больше всех | Рейтинги и статистика |

## Примеры запросов

| Запрос пользователя | Активируется skill |
|---------------------|-------------------|
| "Досье на @durov" | dossier |
| "Кто такой Павел?" | dossier |
| "О чём говорили вчера?" | summary |
| "Дайджест за неделю" | summary |
| "Найди сообщения про Python" | search |
| "Где упоминается API?" | search |
| "Топ участников" | top |
| "Самые популярные сообщения" | top |
| "Сколько сообщений в базе?" | (базовый промпт) |

## Как добавить новый Skill

### 1. Создать файл промпта

Создайте файл `prompts/skills/my_skill.md`:

```markdown
# Skill: Название

## Триггеры

Этот skill активируется при запросах вида:
- "триггер 1"
- "триггер 2"

## Инструкции

Описание того, что должен делать Claude...

### Структура ответа

Шаблон ответа...

### SQL-запросы

Примеры полезных запросов...
```

### 2. Зарегистрировать в боте

Добавьте в словарь `SKILLS` в файле `bot_multi.py`:

```python
SKILLS = {
    # ... существующие skills ...
    "my_skill": {
        "triggers": ["триггер1", "триггер2", "триггер3"],
        "file": "skills/my_skill.md"
    }
}
```

### 3. Обновить индекс (опционально)

Добавьте описание в `prompts/skills/_index.md` для документации.

## Плейсхолдеры

В `base.md` используется плейсхолдер `{db_path}`, который автоматически заменяется на путь к БД внутри Docker-контейнера.

## Конфигурация в bot_multi.py

```python
# Пути к промптам
PROMPTS_DIR = Path("prompts")

# Конфигурация Skills
SKILLS = {
    "dossier": {
        "triggers": ["досье", "профиль", "кто такой", ...],
        "file": "skills/dossier.md"
    },
    # ...
}
```

## Функции для работы с промптами

### `load_prompt(filename: str) -> str`

Загружает промпт из файла относительно `PROMPTS_DIR`.

```python
base = load_prompt("base.md")
skill = load_prompt("skills/dossier.md")
```

### `detect_skill(query: str) -> str | None`

Определяет подходящий skill на основе триггеров в тексте запроса.

```python
skill = detect_skill("Досье на @durov")  # -> "dossier"
skill = detect_skill("Сколько сообщений?")  # -> None
```

## PDF для длинных ответов

Если ответ превышает **2500 символов**, автоматически генерируется PDF файл:
- Превью (первые 500 символов) отправляется как текст
- Полный ответ отправляется как PDF-документ

Порог настраивается в `bot_multi.py`:
```python
if len(report) <= 2500:
    await status_msg.edit_text(report)
else:
    # Генерируем PDF
    pdf_buffer = generate_pdf(report, title=f"Отчёт: {chat_name}")
```

## Деплой на сервер

### Быстрый деплой

```bash
# 1. Закоммитить и запушить
git add .
git commit -m "feat: описание изменений"
git push origin main

# 2. На сервере (или через SSH)
ssh -i ~/.ssh/id_hoodyalko_dev root@104.248.18.118

# 3. Обновить код
cd /home/bot/chatgeist
sudo -u bot git pull origin main

# 4. Перезапустить бота
systemctl restart chatgeist-bot.service

# 5. Проверить логи
tail -f /home/bot/chatgeist/bot.log
```

### Управление ботом на сервере

```bash
# Статус
systemctl status chatgeist-bot.service

# Перезапуск
systemctl restart chatgeist-bot.service

# Логи
tail -f /home/bot/chatgeist/bot.log

# Логи через journalctl
journalctl -u chatgeist-bot.service -f
```

### Обновление OAuth токена на сервере

```bash
# Токен нужно обновлять периодически
# 1. Получить новый токен локально (macOS)
security find-generic-password -s "Claude Code-credentials" -w | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])"

# 2. Обновить .env на сервере
ssh root@server
nano /home/bot/chatgeist/.env
# Заменить CLAUDE_CODE_OAUTH_TOKEN=...

# 3. Перезапустить Docker и бота
cd /home/bot/chatgeist
docker compose down && docker compose up -d
systemctl restart chatgeist-bot.service
```
