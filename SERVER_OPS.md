# Эксплуатация ChatGeist на сервере

## Данные сервера

- **IP:** 64.227.118.253 (hermes-bot)
- **SSH:** `ssh -i ~/.ssh/id_ed25519_remote root@64.227.118.253`
- **Путь к проекту:** `/home/bot/chatgeist`
- **Пользователь бота:** `bot`
- **Бот:** systemd-сервис `chatgeist-bot` (Anthropic API напрямую)
- **Обновление чатов:** cron пользователя `bot`, каждые 4 часа

Бот и обновление чатов должны работать **только здесь**. Тот же токен бота в другом месте даёт `Conflict: terminated by other getUpdates request`, та же Telethon-сессия — `AUTH_KEY_DUPLICATED`.

Старый сервер 104.248.18.118 выведен из эксплуатации 2026-11-12.

---

## После перезагрузки сервера

Бот и cron стартуют автоматически. Проверить:

```bash
systemctl status chatgeist-bot
crontab -u bot -l
```

Если бот не запустился:

```bash
systemctl start chatgeist-bot
```

---

## Проверка работоспособности

```bash
systemctl status chatgeist-bot
journalctl -u chatgeist-bot -n 20 --no-pager
```

Ожидаемый статус: `active (running)`. Ошибки `Bad Gateway`, `Request timeout` и `Flood control` от Telegram — временные, бот переподключается сам.

Тест в Telegram: напиши боту @bestchatanalyzer_bot команду `/chats`.

---

## Частые проблемы

### Бот не отвечает

```bash
systemctl status chatgeist-bot
journalctl -u chatgeist-bot -n 50 --no-pager
systemctl restart chatgeist-bot
```

### "Conflict: terminated by other getUpdates request"

Бот запущен где-то ещё. Найти и остановить второй экземпляр, затем:

```bash
systemctl restart chatgeist-bot
```

### База данных не найдена / устарела

```bash
ls -la /home/bot/chatgeist/databases/
tail -20 /home/bot/chatgeist/logs/update.log
```

---

## Обновление кода

```bash
cd /home/bot/chatgeist
sudo -u bot git pull origin main
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

Список чатов — `targets.json`.

---

## Логи

```bash
# Бот
journalctl -u chatgeist-bot -f
journalctl -u chatgeist-bot --since "1 hour ago"

# Обновление чатов
tail -f /home/bot/chatgeist/logs/update.log
```

---

## Контакты и ресурсы

- **Репозиторий:** github.com/antay1601/chatgeist
- **Бот:** @bestchatanalyzer_bot
- **Anthropic Console:** https://console.anthropic.com
