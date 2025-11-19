#!/usr/bin/env python3
"""
Тестовый скрипт для проверки интеграции telegram_analyzer с ask_claude_cli
"""

import sys

def test_imports():
    """Тест импорта модулей"""
    print("📦 Тест 1: Проверка импортов...")
    try:
        from ask_claude_cli import ask_claude
        print("   ✅ ask_claude успешно импортирован")

        from telegram_analyzer import TelegramAnalyzer
        print("   ✅ TelegramAnalyzer успешно импортирован")

        return True
    except ImportError as e:
        print(f"   ❌ Ошибка импорта: {e}")
        return False

def test_analyzer_init():
    """Тест инициализации анализатора"""
    print("\n🔧 Тест 2: Инициализация TelegramAnalyzer...")
    try:
        from telegram_analyzer import TelegramAnalyzer
        analyzer = TelegramAnalyzer("telegram_messages.db")
        print("   ✅ TelegramAnalyzer успешно инициализирован")

        # Проверяем подключение к БД
        analyzer.cursor.execute("SELECT COUNT(*) FROM messages")
        count = analyzer.cursor.fetchone()[0]
        print(f"   ✅ Подключение к БД работает (найдено {count} сообщений)")

        return True
    except Exception as e:
        print(f"   ❌ Ошибка инициализации: {e}")
        return False

def test_search():
    """Тест поиска сообщений"""
    print("\n🔍 Тест 3: Поиск сообщений...")
    try:
        from telegram_analyzer import TelegramAnalyzer
        analyzer = TelegramAnalyzer("telegram_messages.db")

        # Простой поиск по ключевым словам
        messages = analyzer.search_messages(["работа", "вакансия"], limit=5)
        print(f"   ✅ Найдено {len(messages)} сообщений")

        if messages:
            print(f"   ✅ Первое сообщение: ID={messages[0].id}, Автор={messages[0].sender_display_name}")

        return True
    except Exception as e:
        print(f"   ❌ Ошибка поиска: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Основная функция тестирования"""
    print("""
╔═══════════════════════════════════════════════════════╗
║   🧪 Тест интеграции telegram_analyzer + Claude CLI  ║
╚═══════════════════════════════════════════════════════╝
""")

    tests = [
        ("Импорты", test_imports),
        ("Инициализация", test_analyzer_init),
        ("Поиск", test_search),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ Критическая ошибка в тесте '{name}': {e}")
            results.append((name, False))

    # Итоги
    print("\n" + "="*60)
    print("📊 ИТОГИ ТЕСТИРОВАНИЯ:")
    print("="*60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {name}")

    print("="*60)
    print(f"Пройдено: {passed}/{total}")

    if passed == total:
        print("\n🎉 Все тесты пройдены успешно!")
        return 0
    else:
        print(f"\n⚠️  Не пройдено тестов: {total - passed}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
