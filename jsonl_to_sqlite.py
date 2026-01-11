#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
jsonl_to_sqlite.py
Конвертер JSONL (выход tg_dump_with_reactions.py) в SQLite (вход бота).

Использование:
    python jsonl_to_sqlite.py <input.jsonl> <output.db>

Пример:
    python jsonl_to_sqlite.py export/durov/messages.jsonl databases/durov.db
"""

import sqlite3
import json
import sys
from pathlib import Path
from typing import Optional


def init_db(conn: sqlite3.Connection) -> None:
    """
    Инициализирует структуру базы данных.
    Схема оптимизирована для LLM-анализа.
    """
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            timestamp INTEGER,
            date_iso TEXT,
            message TEXT,
            sender_id INTEGER,
            sender_username TEXT,
            sender_display_name TEXT,
            reply_to_msg_id INTEGER,
            reactions_count INTEGER DEFAULT 0,
            reactions_detail TEXT,
            views INTEGER,
            forwards INTEGER,
            permalink TEXT,
            media_path TEXT,
            is_service INTEGER DEFAULT 0
        )
    ''')

    # Индексы для ускорения запросов
    c.execute('CREATE INDEX IF NOT EXISTS idx_date ON messages(date_iso)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_sender ON messages(sender_username)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_reactions ON messages(reactions_count)')

    # Полнотекстовый поиск
    c.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            message,
            sender_display_name,
            content='messages',
            content_rowid='id'
        )
    ''')

    # Триггеры для синхронизации FTS
    c.execute('''
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, message, sender_display_name)
            VALUES (new.id, new.message, new.sender_display_name);
        END
    ''')

    c.execute('''
        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, message, sender_display_name)
            VALUES('delete', old.id, old.message, old.sender_display_name);
        END
    ''')

    c.execute('''
        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, message, sender_display_name)
            VALUES('delete', old.id, old.message, old.sender_display_name);
            INSERT INTO messages_fts(rowid, message, sender_display_name)
            VALUES (new.id, new.message, new.sender_display_name);
        END
    ''')

    # Таблица метаданных
    c.execute('''
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    conn.commit()


def format_reactions(reactions_counts: dict) -> tuple[int, str]:
    """
    Форматирует словарь реакций в читаемую строку и общий счётчик.

    Returns:
        (total_count, formatted_string)
    """
    if not reactions_counts:
        return 0, ""

    total = sum(reactions_counts.values())
    parts = [f"{emoji} ({count})" for emoji, count in reactions_counts.items()]
    return total, ", ".join(parts)


def parse_timestamp(date_str: str) -> Optional[int]:
    """
    Конвертирует ISO дату в Unix timestamp.
    """
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return int(dt.timestamp())
    except:
        return None


def convert_jsonl_to_sqlite(jsonl_path: Path, db_path: Path) -> dict:
    """
    Конвертирует JSONL файл в SQLite базу данных.

    Args:
        jsonl_path: Путь к входному JSONL файлу
        db_path: Путь к выходной SQLite базе

    Returns:
        dict с статистикой: imported, skipped, errors, last_id
    """
    print(f"🔄 Конвертация: {jsonl_path} -> {db_path}")

    # Создаём папку для БД если нет
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    init_db(conn)
    c = conn.cursor()

    stats = {
        "imported": 0,
        "skipped": 0,
        "errors": 0,
        "last_id": 0
    }

    if not jsonl_path.exists():
        print(f"   ⚠️ Файл {jsonl_path} не найден")
        conn.close()
        return stats

    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue

            try:
                msg = json.loads(line)

                # Извлекаем данные
                msg_id = msg.get('id')
                if not msg_id:
                    stats["skipped"] += 1
                    continue

                date_iso = msg.get('date', '')
                timestamp = parse_timestamp(date_iso)

                # Отправитель
                sender_id = msg.get('sender_id')
                sender_username = msg.get('sender_username')
                sender_display_name = msg.get('sender_display_name')
                post_author = msg.get('post_author', '')

                # Fallback для display_name если не заполнено
                if not sender_display_name:
                    sender_display_name = post_author or (str(sender_id) if sender_id else None)

                # Реакции
                reactions_count, reactions_detail = format_reactions(
                    msg.get('reactions_counts', {})
                )

                # Вставка/обновление
                c.execute('''
                    INSERT OR REPLACE INTO messages
                    (id, timestamp, date_iso, message, sender_id, sender_username,
                     sender_display_name, reply_to_msg_id, reactions_count,
                     reactions_detail, views, forwards, permalink, media_path, is_service)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    msg_id,
                    timestamp,
                    date_iso,
                    msg.get('message', ''),
                    sender_id,
                    sender_username,
                    sender_display_name,
                    msg.get('reply_to_msg_id'),
                    reactions_count,
                    reactions_detail,
                    msg.get('views'),
                    msg.get('forwards'),
                    msg.get('permalink'),
                    msg.get('media_path'),
                    1 if msg.get('is_service') else 0
                ))

                stats["imported"] += 1
                stats["last_id"] = max(stats["last_id"], msg_id)

            except json.JSONDecodeError as e:
                print(f"   ⚠️ Ошибка JSON в строке {line_num}: {e}")
                stats["errors"] += 1
            except Exception as e:
                print(f"   ⚠️ Ошибка обработки строки {line_num}: {e}")
                stats["errors"] += 1

    # Сохраняем метаданные
    from datetime import datetime
    c.execute('''
        INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)
    ''', ('last_updated', datetime.now().isoformat()))

    c.execute('''
        INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)
    ''', ('last_id', str(stats["last_id"])))

    c.execute('''
        INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)
    ''', ('total_messages', str(stats["imported"])))

    conn.commit()
    conn.close()

    print(f"✅ Импортировано: {stats['imported']}, пропущено: {stats['skipped']}, ошибок: {stats['errors']}")
    print(f"   Last ID: {stats['last_id']}")

    return stats


def get_last_id(db_path: Path) -> int:
    """
    Получает последний ID сообщения из существующей БД.
    Используется для инкрементального обновления.
    """
    if not db_path.exists():
        return 0

    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute('SELECT MAX(id) FROM messages')
        result = c.fetchone()
        conn.close()
        return result[0] if result and result[0] else 0
    except:
        return 0


def get_db_stats(db_path: Path) -> dict:
    """
    Получает статистику по базе данных.
    """
    if not db_path.exists():
        return {"exists": False}

    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        c.execute('SELECT COUNT(*) FROM messages')
        total = c.fetchone()[0]

        c.execute('SELECT MIN(date_iso), MAX(date_iso) FROM messages')
        dates = c.fetchone()

        c.execute('SELECT MAX(id) FROM messages')
        last_id = c.fetchone()[0]

        conn.close()

        return {
            "exists": True,
            "total_messages": total,
            "date_from": dates[0],
            "date_to": dates[1],
            "last_id": last_id
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


# CLI интерфейс
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python jsonl_to_sqlite.py <input.jsonl> <output.db>")
        print("")
        print("Примеры:")
        print("  python jsonl_to_sqlite.py export/messages.jsonl databases/chat.db")
        print("  python jsonl_to_sqlite.py export/durov/messages.jsonl databases/durov.db")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    # Показываем статистику существующей БД
    existing_stats = get_db_stats(output_path)
    if existing_stats.get("exists") and existing_stats.get("total_messages"):
        print(f"ℹ️ Существующая БД: {existing_stats['total_messages']} сообщений, last_id={existing_stats['last_id']}")

    # Конвертируем
    result = convert_jsonl_to_sqlite(input_path, output_path)

    # Итоговая статистика
    final_stats = get_db_stats(output_path)
    if final_stats.get("exists"):
        print(f"\n📊 Итого в БД: {final_stats.get('total_messages', 0)} сообщений")
        print(f"   Период: {final_stats.get('date_from', '?')} — {final_stats.get('date_to', '?')}")
