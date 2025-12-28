# Эксплуатация ChatGeist на сервере

## Данные сервера

- **IP:** 104.248.18.118
- **SSH:** `ssh -i ~/.ssh/id_hoodyalko_dev root@104.248.18.118`
- **Путь к проекту:** `/home/bot/chatgeist`
- **Пользователь бота:** `bot`

---

## После перезагрузки сервера

Все сервисы настроены на автозапуск. Проверь статус:

```bash
ssh -i ~/.ssh/id_hoodyalko_dev root@104.248.18.118

# Проверить Docker
docker ps | grep claude-sandbox

# Проверить бота
systemctl status chatgeist-bot
```

Если что-то не запустилось:

```bash
# Запустить Docker
cd /home/bot/chatgeist && docker compose up -d

# Запустить бота
systemctl start chatgeist-bot
```

---

## Проверка работоспособности

### Claude в контейнере

```bash
docker exec claude-sandbox claude --print "скажи OK"
```

Ожидаемый ответ: `OK`

### Бот

```bash
systemctl status chatgeist-bot
journalctl -u chatgeist-bot -n 20 --no-pager
```

Ожидаемый статус: `active (running)`

### Тест в Telegram

Напиши боту @bestchatanalyzer_bot команду `/chats`

---

## Частые проблемы

### Бот не отвечает

```bash
# Проверить статус
systemctl status chatgeist-bot

# Посмотреть логи
journalctl -u chatgeist-bot -n 50 --no-pager

# Перезапустить
systemctl restart chatgeist-bot
```

### "Invalid API key" или "OAuth token expired"

Токен действителен 1 год (до декабря 2026). Если истёк:

```bash
# Получить новый токен
docker exec -it claude-sandbox claude setup-token

# Скопировать токен и обновить .env
nano /home/bot/chatgeist/.env
# Заменить CLAUDE_CODE_OAUTH_TOKEN=новый_токен

# Перезапустить
docker compose restart claude-sandbox
systemctl restart chatgeist-bot
```

### Docker контейнер не запускается

```bash
cd /home/bot/chatgeist

# Посмотреть логи
docker logs claude-sandbox

# Пересобрать если нужно
docker compose down
docker compose build --no-cache
docker compose up -d
```

### "Conflict: terminated by other getUpdates request"

Бот уже запущен где-то ещё (на Mac или другом сервере).

```bash
# Остановить локально (на Mac)
pkill -f "bot_multi\|bot_anthropic"

# Подождать 30 секунд и перезапустить на сервере
systemctl restart chatgeist-bot
```

### База данных не найдена

```bash
# Проверить наличие БД
ls -la /home/bot/chatgeist/databases/

# Проверить что Docker видит
docker exec claude-sandbox ls -la /workspace/dbs/
```

---

## Обновление кода

```bash
cd /home/bot/chatgeist

# Получить обновления
git pull

# Перезапустить бота
systemctl restart chatgeist-bot

# Если изменился Dockerfile
docker compose down
docker compose build
docker compose up -d
systemctl restart chatgeist-bot
```

---

## Обновление данных чатов

```bash
cd /home/bot/chatgeist

# Обновить все чаты
sudo -u bot /home/bot/.local/bin/uv run python update_manager.py

# Обновить конкретный чат
sudo -u bot /home/bot/.local/bin/uv run python update_manager.py --chat alias_name
```

---

## Логи

```bash
# Логи бота
journalctl -u chatgeist-bot -f

# Логи Docker
docker logs claude-sandbox -f

# Логи за последний час
journalctl -u chatgeist-bot --since "1 hour ago"
```

---

## Полный перезапуск всего

```bash
cd /home/bot/chatgeist

# Остановить всё
systemctl stop chatgeist-bot
docker compose down

# Запустить всё
docker compose up -d
sleep 5
systemctl start chatgeist-bot

# Проверить
docker exec claude-sandbox claude --print "OK"
systemctl status chatgeist-bot
```

---

## Контакты и ресурсы

- **Репозиторий:** github.com/antay1601/chatgeist
- **Бот:** @bestchatanalyzer_bot
- **Anthropic Console:** https://console.anthropic.com
