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

## MCP-сервер по HTTP

`mcp_db_server.py --http` отдаёт те же инструменты, что и локальный stdio-вариант, по адресу `https://<домен>/mcp`. Процесс слушает только `127.0.0.1:8766`; TLS даёт nginx (сайт `mcp-chatgeist`, сертификат Let's Encrypt продлевает `certbot.timer`), доступ закрыт токеном (`Authorization: Bearer`). Без токена сервис не стартует.

### Первая установка

Домен должен указывать на сервер (`64-227-118-253.sslip.io` резолвится в IP сам, без настройки DNS). Порт 8766 наружу открывать не нужно. Порт 8765 на сервере занят другим проектом.

```bash
# 1. Токен и домен
umask 077
TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
printf 'MCP_HTTP_TOKEN=%s\nMCP_PUBLIC_HOST=%s\n' "$TOKEN" 64-227-118-253.sslip.io > /etc/chatgeist-mcp.env

# 2. Сервис
cp /home/bot/chatgeist/deploy/chatgeist-mcp.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now chatgeist-mcp

# 3. Сертификат (порт 80 уже обслуживает nginx, challenge идёт через /var/www/html
#    сайта по умолчанию; если он не отдаёт /.well-known, сначала включить только
#    блок "listen 80" из deploy/nginx-mcp-chatgeist.conf)
certbot certonly --webroot -w /var/www/html -d 64-227-118-253.sslip.io

# 4. Сайт nginx
cp /home/bot/chatgeist/deploy/nginx-mcp-chatgeist.conf /etc/nginx/sites-available/mcp-chatgeist
ln -s /etc/nginx/sites-available/mcp-chatgeist /etc/nginx/sites-enabled/mcp-chatgeist
nginx -t && systemctl reload nginx
```

При смене домена поменять его в трёх местах: `MCP_PUBLIC_HOST`, `server_name` и пути к сертификату в конфиге nginx.

### Подключение клиента

```bash
claude mcp add --transport http chatgeist-db https://64-227-118-253.sslip.io/mcp \
  --header "Authorization: Bearer ТОКЕН"
```

Токен лежит в `/etc/chatgeist-mcp.env`.

### Проверка и логи

```bash
systemctl status chatgeist-mcp
journalctl -u chatgeist-mcp -n 50 --no-pager
curl -s -o /dev/null -w '%{http_code}\n' https://64-227-118-253.sslip.io/mcp   # без токена: 401
```

Ответ `421 Invalid Host header` значит, что `MCP_PUBLIC_HOST` не совпадает с `server_name` в конфиге nginx.

### Смена токена

Заменить `MCP_HTTP_TOKEN` в `/etc/chatgeist-mcp.env`, выполнить `systemctl restart chatgeist-mcp` и обновить заголовок у клиентов.

---

## Обновление кода

```bash
cd /home/bot/chatgeist
sudo -u bot git pull origin main
systemctl restart chatgeist-bot
systemctl restart chatgeist-mcp   # если установлен
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
