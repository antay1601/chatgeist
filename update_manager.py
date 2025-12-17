#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
update_manager.py
Оркестратор обновлений: скачивает историю чатов и конвертирует в SQLite.

Запуск:
    python update_manager.py                    # Обновить все чаты из конфига
    python update_manager.py --chat durov       # Обновить конкретный чат
    python update_manager.py --full             # Полный дамп (игнорировать min_id)

Конфигурация чатов в файле targets.json или в CHATS_CONFIG внутри скрипта.
"""

import asyncio
import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from telethon import TelegramClient

# Импортируем наши модули
from tg_dump_with_reactions import dump_chat_history
from jsonl_to_sqlite import convert_jsonl_to_sqlite, get_last_id

load_dotenv()

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_NAME = "update_manager_session"

# Папки для данных
EXPORT_DIR = Path("export_data")    # JSONL дампы
DB_DIR = Path("databases")          # SQLite базы для бота

# Файл конфигурации чатов (альтернатива CHATS_CONFIG)
TARGETS_FILE = Path("targets.json")

# Встроенная конфигурация чатов (используется если нет targets.json)
# Формат: "alias": "target" (target = @username, t.me/link, или числовой ID)
CHATS_CONFIG = {
    # "durov": "durov",           # t.me/durov
    # "python": "python_ru",      # t.me/python_ru
    # "my_group": -1001234567890, # ID приватной группы
}


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def load_targets() -> dict:
    """
    Загружает конфигурацию чатов из targets.json или CHATS_CONFIG.
    """
    if TARGETS_FILE.exists():
        try:
            with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                print(f"📋 Загружена конфигурация из {TARGETS_FILE}")
                return data.get("chats", data)
        except Exception as e:
            print(f"⚠️ Ошибка чтения {TARGETS_FILE}: {e}")

    if CHATS_CONFIG:
        print("📋 Используется встроенная конфигурация CHATS_CONFIG")
        return CHATS_CONFIG

    print("⚠️ Нет конфигурации чатов. Создайте targets.json или заполните CHATS_CONFIG")
    return {}


def save_targets(targets: dict) -> None:
    """
    Сохраняет конфигурацию чатов в targets.json.
    """
    with open(TARGETS_FILE, 'w', encoding='utf-8') as f:
        json.dump({"chats": targets, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    print(f"✅ Конфигурация сохранена в {TARGETS_FILE}")


def get_db_last_id(db_path: Path) -> int:
    """
    Получает последний ID сообщения из БД для инкрементального обновления.
    """
    if not db_path.exists():
        return 0

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT MAX(id) FROM messages")
        result = cur.fetchone()
        conn.close()
        return result[0] if result and result[0] else 0
    except Exception:
        return 0


def get_db_stats(db_path: Path) -> dict:
    """
    Получает статистику БД.
    """
    if not db_path.exists():
        return {"exists": False}

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM messages")
        total = cur.fetchone()[0]

        cur.execute("SELECT MAX(id) FROM messages")
        last_id = cur.fetchone()[0]

        cur.execute("SELECT MIN(date_iso), MAX(date_iso) FROM messages")
        dates = cur.fetchone()

        conn.close()
        return {
            "exists": True,
            "total": total,
            "last_id": last_id,
            "date_from": dates[0],
            "date_to": dates[1],
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


# ============================================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================================

async def update_chat(
    client: TelegramClient,
    alias: str,
    target: str,
    full_dump: bool = False,
    limit: Optional[int] = None
) -> dict:
    """
    Обновляет один чат: скачивает новые сообщения и конвертирует в SQLite.

    Args:
        client: Telethon клиент
        alias: Короткое имя для файлов (например, "durov")
        target: Цель (@username, t.me/link, ID)
        full_dump: Если True, игнорирует min_id и качает всё заново
        limit: Ограничение количества сообщений

    Returns:
        dict со статистикой обновления
    """
    print(f"\n{'='*60}")
    print(f"🔧 Обработка: {alias} -> {target}")
    print(f"{'='*60}")

    # Пути к файлам
    jsonl_dir = EXPORT_DIR / alias
    jsonl_file = jsonl_dir / "messages.jsonl"
    db_file = DB_DIR / f"{alias}.db"

    # Определяем min_id для инкрементального обновления
    min_id = 0
    if not full_dump:
        min_id = get_db_last_id(db_file)
        if min_id > 0:
            print(f"ℹ️ Инкрементальное обновление с ID > {min_id}")
        else:
            print(f"ℹ️ Первичный дамп (БД не существует)")

    # 1. Скачиваем сообщения
    dump_result = await dump_chat_history(
        client=client,
        chat_target=target,
        out_dir=jsonl_dir,
        limit=limit,
        min_id=min_id,
        with_reactors=False,  # False для скорости
        download_media=False,
    )

    if dump_result.get("error"):
        return {"alias": alias, "error": dump_result["error"]}

    # 2. Конвертируем в SQLite
    if jsonl_file.exists():
        convert_result = convert_jsonl_to_sqlite(jsonl_file, db_file)
    else:
        print(f"⚠️ JSONL не создан, пропускаем конвертацию")
        convert_result = {"imported": 0}

    # 3. Собираем итоговую статистику
    db_stats = get_db_stats(db_file)

    result = {
        "alias": alias,
        "target": target,
        "dumped": dump_result.get("count", 0),
        "last_id": dump_result.get("last_id", 0),
        "db_total": db_stats.get("total", 0),
        "db_path": str(db_file),
    }

    print(f"\n📊 {alias}: +{result['dumped']} сообщений, всего в БД: {result['db_total']}")

    return result


async def update_all(full_dump: bool = False, limit: Optional[int] = None) -> list:
    """
    Обновляет все чаты из конфигурации.
    """
    targets = load_targets()

    if not targets:
        print("❌ Нет чатов для обновления")
        return []

    if not API_ID or not API_HASH:
        raise SystemExit("❌ Нужны TELEGRAM_API_ID и TELEGRAM_API_HASH в .env")

    # Создаём папки
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n🚀 Обновление {len(targets)} чатов...")
    print(f"   Export: {EXPORT_DIR}")
    print(f"   DB: {DB_DIR}")

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()

    results = []
    for alias, target in targets.items():
        try:
            result = await update_chat(
                client=client,
                alias=alias,
                target=str(target),
                full_dump=full_dump,
                limit=limit,
            )
            results.append(result)
        except Exception as e:
            print(f"❌ Ошибка при обработке {alias}: {e}")
            results.append({"alias": alias, "error": str(e)})

    await client.disconnect()

    # Итоговый отчёт
    print(f"\n{'='*60}")
    print("📋 ИТОГИ ОБНОВЛЕНИЯ")
    print(f"{'='*60}")

    total_new = 0
    for r in results:
        if r.get("error"):
            print(f"❌ {r['alias']}: {r['error']}")
        else:
            print(f"✅ {r['alias']}: +{r.get('dumped', 0)}, всего {r.get('db_total', 0)}")
            total_new += r.get("dumped", 0)

    print(f"\n🏁 Загружено новых сообщений: {total_new}")
    print(f"   Базы данных готовы в папке: {DB_DIR}/")

    return results


# ============================================================================
# CLI ИНТЕРФЕЙС
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Оркестратор обновлений ChatGeist",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python update_manager.py                    # Обновить все чаты
  python update_manager.py --chat durov       # Обновить один чат
  python update_manager.py --full             # Полный дамп (игнорировать историю)
  python update_manager.py --list             # Показать список чатов
  python update_manager.py --add durov @durov # Добавить чат в targets.json
        """
    )

    parser.add_argument("--chat", type=str, help="Обновить конкретный чат (alias)")
    parser.add_argument("--full", action="store_true", help="Полный дамп (не инкрементальный)")
    parser.add_argument("--limit", type=int, help="Ограничить количество сообщений")
    parser.add_argument("--list", action="store_true", help="Показать список чатов")
    parser.add_argument("--add", nargs=2, metavar=("ALIAS", "TARGET"), help="Добавить чат в конфиг")
    parser.add_argument("--remove", type=str, metavar="ALIAS", help="Удалить чат из конфига")
    parser.add_argument("--stats", action="store_true", help="Показать статистику баз данных")

    args = parser.parse_args()

    # Команда: показать список
    if args.list:
        targets = load_targets()
        if targets:
            print("\n📋 Список чатов для мониторинга:")
            for alias, target in targets.items():
                db_path = DB_DIR / f"{alias}.db"
                stats = get_db_stats(db_path)
                status = f"{stats.get('total', 0)} сообщений" if stats.get('exists') else "нет БД"
                print(f"   {alias}: {target} ({status})")
        else:
            print("📋 Список чатов пуст")
        return

    # Команда: добавить чат
    if args.add:
        alias, target = args.add
        targets = load_targets()
        targets[alias] = target
        save_targets(targets)
        print(f"✅ Добавлен чат: {alias} -> {target}")
        return

    # Команда: удалить чат
    if args.remove:
        targets = load_targets()
        if args.remove in targets:
            del targets[args.remove]
            save_targets(targets)
            print(f"✅ Удалён чат: {args.remove}")

            # Удаляем файл БД если существует
            db_file = DB_DIR / f"{args.remove}.db"
            if db_file.exists():
                db_file.unlink()
                print(f"✅ Удалён файл: {db_file}")

            # Удаляем папку экспорта если существует
            export_dir = EXPORT_DIR / args.remove
            if export_dir.exists():
                import shutil
                shutil.rmtree(export_dir)
                print(f"✅ Удалена папка: {export_dir}")
        else:
            print(f"⚠️ Чат {args.remove} не найден в конфиге")
        return

    # Команда: статистика
    if args.stats:
        print(f"\n📊 Статистика баз данных в {DB_DIR}/")
        if not DB_DIR.exists():
            print("   Папка не существует")
            return

        for db_file in sorted(DB_DIR.glob("*.db")):
            stats = get_db_stats(db_file)
            if stats.get("exists"):
                print(f"\n   {db_file.name}:")
                print(f"      Сообщений: {stats.get('total', 0)}")
                print(f"      Last ID: {stats.get('last_id', 0)}")
                print(f"      Период: {stats.get('date_from', '?')} — {stats.get('date_to', '?')}")
        return

    # Команда: обновить конкретный чат
    if args.chat:
        targets = load_targets()
        if args.chat not in targets:
            print(f"❌ Чат {args.chat} не найден в конфиге")
            print(f"   Доступные: {', '.join(targets.keys()) or 'нет'}")
            return

        async def run_single():
            if not API_ID or not API_HASH:
                raise SystemExit("❌ Нужны TELEGRAM_API_ID и TELEGRAM_API_HASH")

            DB_DIR.mkdir(parents=True, exist_ok=True)
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)

            client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
            await client.start()
            await update_chat(
                client=client,
                alias=args.chat,
                target=str(targets[args.chat]),
                full_dump=args.full,
                limit=args.limit,
            )
            await client.disconnect()

        asyncio.run(run_single())
        return

    # По умолчанию: обновить все чаты
    asyncio.run(update_all(full_dump=args.full, limit=args.limit))


if __name__ == "__main__":
    main()
