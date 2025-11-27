"""
Telegram Bot для анализа сообщений из базы данных SQLite с AI-анализом.
Использует OpenAI API для обработки запросов.
"""

import os
import logging
import warnings
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
from openai import OpenAI

# Игнорируем предупреждения о deprecation
warnings.filterwarnings('ignore', category=DeprecationWarning)

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в переменных окружения!")

# Настройка OpenAI API
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY не найден в переменных окружения!")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

logger.info("✅ Бот с OpenAI API готов к работе")

# Путь к базе данных
DB_PATH = "telegram_messages.db"

# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

async def get_conversation_history(message: Message, bot_id: int) -> list[dict]:
    """
    Собирает всю цепочку сообщений (историю диалога).

    Args:
        message: Текущее сообщение от пользователя
        bot_id: ID бота для определения роли

    Returns:
        Список словарей с историей в формате:
        [{"role": "user/assistant", "content": "текст"}]
        От старых к новым сообщениям
    """
    history = []
    current = message.reply_to_message

    # Поднимаемся вверх по цепочке reply
    while current:
        # Определяем роль: если от бота - assistant, если от пользователя - user
        role = "assistant" if current.from_user.id == bot_id else "user"

        if current.text:
            # Убираем технические префиксы типа "🔄 Анализирую данные..."
            content = current.text
            if role == "assistant":
                # Убираем статусные сообщения
                if content.startswith("🔄") or content.startswith("_(продолжение"):
                    current = current.reply_to_message
                    continue
                # Убираем префикс "(продолжение N)" если есть
                if content.startswith("(продолжение"):
                    content = '\n'.join(content.split('\n')[2:])  # Пропускаем первые 2 строки

            history.append({
                "role": role,
                "content": content
            })

        # Переходим к следующему сообщению в цепочке
        current = current.reply_to_message

    # Разворачиваем, чтобы история была от старых к новым
    history.reverse()

    return history


def execute_sql_query(db_path: str, query: str) -> str:
    """
    Выполняет SQL запрос к базе данных и возвращает результат в текстовом виде.

    Args:
        db_path: Путь к базе данных
        query: SQL запрос

    Returns:
        Результат запроса в текстовом формате
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(query)

        # Получаем названия колонок
        column_names = [description[0] for description in cursor.description] if cursor.description else []

        # Получаем данные
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "Запрос выполнен, но данных не найдено."

        # Форматируем результат
        result = []
        result.append("Колонки: " + ", ".join(column_names))
        result.append("-" * 50)

        for row in rows[:100]:  # Ограничиваем 100 строками
            result.append(" | ".join(str(val) for val in row))

        if len(rows) > 100:
            result.append(f"\n... и еще {len(rows) - 100} строк")

        return "\n".join(result)

    except Exception as e:
        return f"Ошибка выполнения SQL: {str(e)}"


def ask_openai_with_context(question: str, history: list[dict], db_path: str = "telegram_messages.db") -> str:
    """
    Отправляет вопрос в OpenAI API с контекстом базы данных.

    Args:
        question: Текущий вопрос пользователя
        history: История предыдущих сообщений
        db_path: Путь к базе данных

    Returns:
        Ответ от OpenAI
    """
    # Проверяем существование БД
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"База данных {db_path} не найдена")

    # Получаем схему таблицы
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'")
        schema = cursor.fetchone()
        schema_text = schema[0] if schema else "Схема не найдена"

        # Получаем пример данных
        cursor.execute("SELECT * FROM messages LIMIT 3")
        sample_data = cursor.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка при чтении схемы БД: {e}")
        schema_text = "Ошибка чтения схемы"
        sample_data = []

    # Формируем промпт с учётом истории
    if history:
        # Есть история - формируем контекстный промпт
        history_text = "\n\n".join([
            f"{'Пользователь' if msg['role'] == 'user' else 'Ассистент'}: {msg['content']}"
            for msg in history
        ])

        full_prompt = f"""Ты - аналитик данных из Telegram чата. У тебя есть доступ к SQLite базе данных с сообщениями.

СХЕМА ТАБЛИЦЫ messages:
{schema_text}

ПРИМЕР ДАННЫХ:
{sample_data[:3] if sample_data else 'Нет данных'}

ИСТОРИЯ ДИАЛОГА:
{history_text}

ТЕКУЩИЙ ВОПРОС ПОЛЬЗОВАТЕЛЯ: {question}

ВАЖНО:
1. Если нужно получить данные из БД - сформируй SQL запрос и верни ТОЛЬКО SQL в формате:
   SQL_QUERY: твой_запрос;
   (ВАЖНО: SQL должен быть на ОДНОЙ строке и заканчиваться точкой с запятой)
2. После выполнения SQL я вернусь к тебе с результатами для анализа
3. НЕ пиши ничего после SQL запроса - только сам SQL!
4. Используй ПРОСТОЕ текстовое форматирование (без Markdown)
5. Отвечай кратко и по делу
6. Учитывай контекст предыдущих вопросов и ответов

Проанализируй вопрос и предоставь развернутый ответ."""
    else:
        # Нет истории - обычный промпт
        full_prompt = f"""Ты - аналитик данных из Telegram чата. У тебя есть доступ к SQLite базе данных с сообщениями.

СХЕМА ТАБЛИЦЫ messages:
{schema_text}

ПРИМЕР ДАННЫХ:
{sample_data[:3] if sample_data else 'Нет данных'}

ВОПРОС: {question}

ВАЖНО:
1. Если нужно получить данные из БД - сформируй SQL запрос и верни ТОЛЬКО SQL в формате:
   SQL_QUERY: твой_запрос;
   (ВАЖНО: SQL должен быть на ОДНОЙ строке и заканчиваться точкой с запятой)
2. После выполнения SQL я вернусь к тебе с результатами для анализа
3. НЕ пиши ничего после SQL запроса - только сам SQL!
4. Используй ПРОСТОЕ текстовое форматирование (без Markdown)
5. Отвечай кратко и по делу

Проанализируй вопрос и предоставь развернутый ответ."""

    logger.info(f"Отправка запроса в OpenAI API (история: {len(history)} сообщений)")

    try:
        # Используем GPT-4o-mini (быстрая и недорогая модель)
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты - аналитик данных, который помогает анализировать сообщения из Telegram чата используя SQL запросы."},
                {"role": "user", "content": full_prompt}
            ],
            temperature=0.7,
            max_tokens=2000
        )

        answer = response.choices[0].message.content

        # Проверяем, есть ли SQL запрос в ответе
        if "SQL_QUERY:" in answer:
            # Извлекаем SQL запрос
            sql_start = answer.find("SQL_QUERY:") + len("SQL_QUERY:")
            sql_lines = []
            for line in answer[sql_start:].split('\n'):
                line = line.strip()

                # Останавливаемся на пустой строке или на строке с разделителями
                if not line or line.startswith('===') or line.startswith('---'):
                    break

                # Пропускаем комментарии
                if line.startswith('#') or line.startswith('//'):
                    continue

                sql_lines.append(line)

                # Останавливаемся на точке с запятой
                if ';' in line:
                    break

            sql_query = ' '.join(sql_lines).strip()

            # Очищаем SQL от лишнего текста после точки с запятой
            if ';' in sql_query:
                sql_query = sql_query[:sql_query.find(';') + 1]

            # Выполняем запрос
            logger.info(f"Выполнение SQL: {sql_query}")
            sql_result = execute_sql_query(db_path, sql_query)

            # Отправляем результат обратно в OpenAI для анализа
            analysis_prompt = f"""Вот результат SQL запроса:

{sql_result}

Исходный вопрос был: {question}

Проанализируй данные и предоставь понятный ответ на вопрос пользователя.
Используй ПРОСТОЕ текстовое форматирование (без Markdown).
Для заголовков используй === или ---
Для списков используй цифры или дефисы."""

            analysis_response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Ты - аналитик данных, который анализирует результаты SQL запросов."},
                    {"role": "user", "content": analysis_prompt}
                ],
                temperature=0.7,
                max_tokens=2000
            )
            return analysis_response.choices[0].message.content

        return answer

    except Exception as e:
        logger.error(f"Ошибка OpenAI API: {e}")
        raise RuntimeError(f"Ошибка при обращении к OpenAI API: {str(e)}")


# ============================================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """
    Обработчик команды /start
    """
    welcome_text = """
👋 Добро пожаловать в ChatGeist Analytics Bot!
Я найду все релевантные сообщения и предоставлю подробный отчет! 📊

💡 Новая возможность: Вы можете нажать "Ответить" на моё сообщение, чтобы задать уточняющий вопрос с учётом контекста предыдущего диалога!

🤖 Работаю на базе OpenAI GPT-4o-mini
    """

    await message.answer(welcome_text)


@dp.message(F.text)
async def handle_query(message: Message):
    """
    Обработчик текстовых запросов пользователя.
    Поддерживает контекстные диалоги через reply.
    """
    user_query = message.text.strip()

    if not user_query:
        await message.answer("❌ Пожалуйста, отправьте непустой запрос.")
        return

    # Проверяем, является ли это ответом на предыдущее сообщение
    is_reply = message.reply_to_message is not None
    history = []

    if is_reply:
        # Собираем историю диалога
        bot_info = await bot.me()
        history = await get_conversation_history(message, bot_info.id)
        logger.info(f"Обработка запроса с контекстом (история: {len(history)} сообщений): {user_query}")
    else:
        logger.info(f"Обработка нового запроса: {user_query}")

    # Уведомляем пользователя о начале анализа
    if is_reply and history:
        status_msg = await message.answer("🔄 Анализирую с учётом контекста диалога...")
    else:
        status_msg = await message.answer("🔄 Анализирую данные, пожалуйста подождите...")

    try:
        # Отправляем запрос в OpenAI API
        import asyncio
        report = await asyncio.to_thread(ask_openai_with_context, user_query, history, DB_PATH)

        # Telegram имеет ограничение на длину сообщения (4096 символов)
        if len(report) <= 4096:
            await status_msg.edit_text(report)
        else:
            # Разбиваем на части
            await status_msg.delete()

            parts = []
            current_part = []
            current_length = 0

            for line in report.split('\n'):
                line_length = len(line) + 1  # +1 для \n
                if current_length + line_length > 4000:  # Оставляем запас
                    parts.append('\n'.join(current_part))
                    current_part = [line]
                    current_length = line_length
                else:
                    current_part.append(line)
                    current_length += line_length

            if current_part:
                parts.append('\n'.join(current_part))

            # Отправляем части
            for i, part in enumerate(parts):
                if i == 0:
                    await message.answer(part)
                else:
                    await message.answer(f"(продолжение {i+1})\n\n{part}")

    except FileNotFoundError as e:
        logger.error(f"База данных не найдена: {e}")
        await status_msg.edit_text(
            f"❌ База данных не найдена: {DB_PATH}\n\nПожалуйста, убедитесь, что файл базы данных существует."
        )
    except RuntimeError as e:
        logger.error(f"Ошибка OpenAI API: {e}")
        await status_msg.edit_text(
            f"❌ Ошибка при обращении к OpenAI API:\n\n{str(e)}"
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса: {e}", exc_info=True)
        error_msg = str(e)
        await status_msg.edit_text(
            f"❌ Произошла ошибка при анализе данных:\n\n{error_msg}"
        )


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """
    Главная функция для запуска бота.
    """
    logger.info("Запуск бота с OpenAI API...")

    # Проверяем наличие базы данных
    if not os.path.exists(DB_PATH):
        logger.warning(f"База данных {DB_PATH} не найдена! Бот запустится, но поиск будет невозможен.")

    try:
        # Запускаем polling
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
