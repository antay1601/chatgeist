#!/usr/bin/env python3
"""
Скрипт для отправки вопросов в Claude CLI с поиском ответов в telegram_messages.db.
"""

import sys
import subprocess
import os
from datetime import datetime


def ask_claude(question: str, db_path: str = "telegram_messages.db", return_only: bool = False) -> str:
    """
    Отправляет вопрос в Claude CLI и возвращает ответ.

    Args:
        question: Вопрос для Claude
        db_path: Путь к базе данных (по умолчанию telegram_messages.db)
        return_only: Если True, только возвращает результат без вывода (для использования как библиотека)

    Returns:
        Ответ от Claude

    Raises:
        FileNotFoundError: Если БД или Claude CLI не найдены
        subprocess.CalledProcessError: Если возникла ошибка при выполнении Claude CLI
    """
    # Проверяем существование БД
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"База данных {db_path} не найдена")

    if not return_only:
        print(f"Отправляю вопрос в Claude CLI...")

    # Формируем полный промпт с указанием на БД
    full_prompt = f"""Используй базу данных telegram_messages.db для поиска ответа на следующий вопрос.

База данных содержит таблицу messages со следующими полями:
- id, timestamp, date_iso, message (текст сообщения)
- sender_username, sender_display_name
- reply_to_msg_id, reactions_count, views, forwards
- permalink и другие поля

Вопрос: {question}

Пожалуйста, проанализируй данные в БД и предоставь развернутый ответ."""

    # Отправляем вопрос в Claude CLI в неинтерактивном режиме
    # --dangerously-skip-permissions: автоматически разрешает выполнение команд
    result = subprocess.run(
        ['claude', '--print', '--dangerously-skip-permissions', full_prompt],
        text=True,
        capture_output=True,
        check=True,
        cwd=os.path.dirname(os.path.abspath(db_path))
    )

    response = result.stdout.strip()
    return response


def ask_claude_cli(question: str, output_file: str = "claude_response.md", db_path: str = "telegram_messages.db"):
    """
    Отправляет вопрос в Claude CLI, который ищет ответ в базе данных telegram_messages.db.
    Сохраняет результат в файл.

    Args:
        question: Вопрос для Claude
        output_file: Имя файла для сохранения ответа (по умолчанию claude_response.md)
        db_path: Путь к базе данных (по умолчанию telegram_messages.db)
    """
    try:
        response = ask_claude(question, db_path, return_only=False)

        # Формируем содержимое markdown файла
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        markdown_content = f"""# Claude CLI Response

**Дата:** {timestamp}

## Вопрос

{question}

## Ответ

{response}
"""

        # Сохраняем в файл
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        print(f"✓ Ответ сохранен в файл: {output_file}")
        print(f"\nОтвет Claude:\n{'-'*50}\n{response}\n{'-'*50}")

    except subprocess.CalledProcessError as e:
        print(f"Ошибка при вызове Claude CLI: {e}")
        if e.stderr:
            print(f"Stderr: {e.stderr}")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"Ошибка: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Неожиданная ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python ask_claude_cli.py 'ваш вопрос' [имя_файла.md] [путь_к_бд]")
        print("\nПримеры:")
        print("  python ask_claude_cli.py 'Сколько всего сообщений в базе?' output.md")
        print("  python ask_claude_cli.py 'Кто самый активный автор?' analysis.md telegram_messages.db")
        sys.exit(1)

    question = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "claude_response.md"
    db_path = sys.argv[3] if len(sys.argv) > 3 else "telegram_messages.db"

    ask_claude_cli(question, output_file, db_path)
