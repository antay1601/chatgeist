# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=1.10,<2"]
# ///
"""
MCP сервер для read-only доступа к базам чатов в databases/.

Локально (stdio, Claude Code подхватывает из .mcp.json):
    uv run --script mcp_db_server.py

Удалённо (streamable HTTP на 127.0.0.1, наружу только через HTTPS-прокси):
    MCP_HTTP_TOKEN=... MCP_PUBLIC_HOST=mcp.example.com uv run --script mcp_db_server.py --http

    MCP_HTTP_TOKEN   обязателен; клиент шлёт Authorization: Bearer <токен>
    MCP_PUBLIC_HOST  домен, с которым приходит заголовок Host от прокси
    MCP_HTTP_PORT    порт на 127.0.0.1 (по умолчанию 8766)

Безопасность (опирается на сам SQLite, а не на фильтр подстрок):
- Соединение открывается в режиме mode=ro + PRAGMA query_only
- Авторизатор пропускает только чтение: SELECT, READ, FUNCTION, рекурсивные CTE
- Таймаут выполнения запроса и лимит строк в ответе
"""

import hmac
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

DB_ROOT = (Path(__file__).resolve().parent / "databases").resolve()
QUERY_TIMEOUT_SEC = 30
MAX_ROWS = 500

HTTP_PORT = int(os.environ.get("MCP_HTTP_PORT", "8766"))
PUBLIC_HOST = os.environ.get("MCP_PUBLIC_HOST", "").strip()

# Защита от DNS rebinding: Host должен быть локальным или публичным доменом прокси
_local_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_local_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]

mcp = FastMCP(
    "chatgeist-db",
    host="127.0.0.1",
    port=HTTP_PORT,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_local_hosts + ([PUBLIC_HOST] if PUBLIC_HOST else []),
        allowed_origins=_local_origins + ([f"https://{PUBLIC_HOST}"] if PUBLIC_HOST else []),
    ),
)

ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _authorizer(action, arg1, arg2, db_name, trigger):
    if action in ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    # FTS5 сам вызывает PRAGMA data_version (только чтение) при MATCH
    if action == sqlite3.SQLITE_PRAGMA and arg1 == "data_version" and arg2 is None:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _db_path(database: str) -> Path:
    path = (DB_ROOT / f"{Path(database).stem}.db").resolve()
    if path.parent != DB_ROOT or not path.exists():
        available = ", ".join(p.stem for p in sorted(DB_ROOT.glob("*.db")))
        raise ValueError(f"База '{database}' не найдена. Доступны: {available}")
    return path


def _connect(database: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{_db_path(database)}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def list_databases() -> str:
    """Список баз чатов: имя, размер, число сообщений."""
    result = []
    for path in sorted(DB_ROOT.glob("*.db")):
        with closing(_connect(path.stem)) as conn:
            count = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        result.append({
            "name": path.stem,
            "size_mb": round(path.stat().st_size / (1024 * 1024), 1),
            "messages": count,
        })
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def get_schema(database: str) -> str:
    """CREATE-выражения таблиц, индексов и представлений базы (без служебных таблиц FTS)."""
    with closing(_connect(database)) as conn:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'messages_fts_%' ORDER BY type, name"
        ).fetchall()
    return "\n\n".join(row["sql"] for row in rows)


@mcp.tool()
def query(database: str, sql: str, max_rows: int = 100) -> str:
    """
    Выполняет read-only SQL (SELECT / WITH) в одной базе.

    Полнотекстовый поиск: messages_fts MATCH 'слово'.
    Возвращает JSON со строками; не более max_rows (до 500).
    """
    max_rows = max(1, min(max_rows, MAX_ROWS))
    deadline = time.monotonic() + QUERY_TIMEOUT_SEC
    with closing(_connect(database)) as conn:
        conn.set_authorizer(_authorizer)
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        try:
            cursor = conn.execute(sql)
            rows = cursor.fetchmany(max_rows + 1)
        except sqlite3.DatabaseError as e:
            if time.monotonic() > deadline:
                return json.dumps({"error": f"Таймаут {QUERY_TIMEOUT_SEC} с"}, ensure_ascii=False)
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    truncated = len(rows) > max_rows
    return json.dumps(
        {
            "rows": [dict(row) for row in rows[:max_rows]],
            "truncated": truncated,
        },
        ensure_ascii=False,
        default=str,
    )


class BearerAuth:
    """ASGI-обёртка: пропускает только запросы с верным Authorization: Bearer."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                })
                await send({"type": "http.response.body", "body": b'{"error": "unauthorized"}'})
                return
        await self.app(scope, receive, send)


def _run_http() -> None:
    import uvicorn

    token = os.environ.get("MCP_HTTP_TOKEN", "")
    if len(token) < 32:
        sys.exit("MCP_HTTP_TOKEN не задан или короче 32 символов: без токена HTTP-режим не запускается")
    app = BearerAuth(mcp.streamable_http_app(), token)
    uvicorn.run(app, host="127.0.0.1", port=HTTP_PORT, log_level="info")


if __name__ == "__main__":
    if "--http" in sys.argv[1:]:
        _run_http()
    else:
        mcp.run()
