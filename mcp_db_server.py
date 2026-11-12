# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=1.10,<2"]
# ///
"""
MCP сервер для read-only доступа к базам чатов в databases/.

Запуск (Claude Code подхватывает из .mcp.json):
    uv run --script mcp_db_server.py

Безопасность (опирается на сам SQLite, а не на фильтр подстрок):
- Соединение открывается в режиме mode=ro + PRAGMA query_only
- Авторизатор пропускает только чтение: SELECT, READ, FUNCTION, рекурсивные CTE
- Таймаут выполнения запроса и лимит строк в ответе
"""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DB_ROOT = (Path(__file__).resolve().parent / "databases").resolve()
QUERY_TIMEOUT_SEC = 30
MAX_ROWS = 500

mcp = FastMCP("chatgeist-db")

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


if __name__ == "__main__":
    mcp.run()
