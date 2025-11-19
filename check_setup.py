"""
Скрипт для проверки настройки проекта ChatGeist Bot.
Запуск: uv run python check_setup.py
"""

import os
import sys
import sqlite3
from pathlib import Path

def check_env():
    """Проверка переменных окружения"""
    print("🔍 Проверка переменных окружения...")

    if not Path('.env').exists():
        print("  ❌ Файл .env не найден!")
        print("  💡 Создайте его: cp .env.example .env")
        return False

    from dotenv import load_dotenv
    load_dotenv()

    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        print("  ❌ TELEGRAM_BOT_TOKEN не найден в .env")
        return False

    if token == 'your_bot_token_here' or token == '1234567890:ABCdefGHIjklMNOpqrsTUVwxyz':
        print("  ⚠️  Используется токен из примера!")
        print("  💡 Получите настоящий токен у @BotFather")
        return False

    print(f"  ✅ Токен найден: {token[:10]}...")
    return True

def check_database():
    """Проверка базы данных"""
    print("\n🔍 Проверка базы данных...")

    db_path = 'telegram_messages.db'
    if not Path(db_path).exists():
        print(f"  ❌ База данных {db_path} не найдена!")
        print("  💡 Поместите файл telegram_messages.db в корень проекта")
        return False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Проверка структуры
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if not cursor.fetchone():
            print("  ❌ Таблица 'messages' не найдена!")
            return False

        # Подсчет сообщений
        cursor.execute('SELECT COUNT(*) FROM messages')
        total = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM messages WHERE is_service = 0')
        non_service = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM messages WHERE is_service = 0 AND message IS NOT NULL')
        with_text = cursor.fetchone()[0]

        print(f"  ✅ База данных подключена")
        print(f"     Всего сообщений: {total:,}")
        print(f"     Не служебных: {non_service:,}")
        print(f"     С текстом: {with_text:,}")

        conn.close()
        return True

    except Exception as e:
        print(f"  ❌ Ошибка при работе с БД: {e}")
        return False

def check_dependencies():
    """Проверка зависимостей"""
    print("\n🔍 Проверка зависимостей...")

    required = ['aiogram', 'aiosqlite', 'dotenv']
    all_ok = True

    for module in required:
        try:
            if module == 'dotenv':
                __import__('dotenv')
                module_name = 'python-dotenv'
            else:
                __import__(module)
                module_name = module
            print(f"  ✅ {module_name} установлен")
        except ImportError:
            print(f"  ❌ {module_name} не установлен!")
            all_ok = False

    if not all_ok:
        print("\n  💡 Установите зависимости: uv sync")

    return all_ok

def main():
    """Главная функция"""
    print("=" * 60)
    print("ChatGeist Bot - Проверка настройки")
    print("=" * 60)

    results = []
    results.append(("Зависимости", check_dependencies()))
    results.append(("Переменные окружения", check_env()))
    results.append(("База данных", check_database()))

    print("\n" + "=" * 60)
    print("Результаты проверки:")
    print("=" * 60)

    all_ok = True
    for name, status in results:
        icon = "✅" if status else "❌"
        print(f"{icon} {name}")
        if not status:
            all_ok = False

    print("=" * 60)

    if all_ok:
        print("\n🎉 Все проверки пройдены! Можно запускать бота:")
        print("   uv run python bot.py")
        print()
        return 0
    else:
        print("\n⚠️  Некоторые проверки не пройдены.")
        print("   Исправьте ошибки выше перед запуском бота.")
        print()
        return 1

if __name__ == '__main__':
    sys.exit(main())
