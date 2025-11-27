"""
Telegram Bot для анализа сообщений из базы данных SQLite с AI-анализом.
Расширенная версия с поддержкой контекстных диалогов.
"""

import os
import logging
import warnings
import subprocess
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

logger.info("✅ Расширенный бот с поддержкой контекстных диалогов")

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


def ask_claude_with_context(question: str, history: list[dict], db_path: str = "telegram_messages.db") -> str:
    """
    Отправляет вопрос в Claude CLI с учётом истории диалога.

    Args:
        question: Текущий вопрос пользователя
        history: История предыдущих сообщений
        db_path: Путь к базе данных

    Returns:
        Ответ от Claude
    """
    # Проверяем существование БД
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"База данных {db_path} не найдена")

    # Формируем промпт с учётом истории
    if history:
        # Есть история - формируем контекстный промпт
        history_text = "\n\n".join([
            f"{'Пользователь' if msg['role'] == 'user' else 'Ассистент'}: {msg['content']}"
            for msg in history
        ])

        full_prompt = f"""Используй базу данных telegram_messages.db для поиска ответа на следующий вопрос.

База данных содержит таблицу messages со следующими полями:
- id, timestamp, date_iso, message (текст сообщения)
- sender_username, sender_display_name
- reply_to_msg_id, reactions_count, views, forwards
- permalink и другие поля

ИСТОРИЯ ДИАЛОГА:
{history_text}

ТЕКУЩИЙ ВОПРОС ПОЛЬЗОВАТЕЛЯ: {question}

Пожалуйста, проанализируй данные в БД с учётом контекста предыдущих вопросов и ответов, и предоставь развернутый ответ на текущий вопрос."""
    else:
        # Нет истории - обычный промпт
        full_prompt = f"""Используй базу данных telegram_messages.db для поиска ответа на следующий вопрос.

База данных содержит таблицу messages со следующими полями:
- id, timestamp, date_iso, message (текст сообщения)
- sender_username, sender_display_name
- reply_to_msg_id, reactions_count, views, forwards
- permalink и другие поля

Вопрос: {question}

Пожалуйста, проанализируй данные в БД и предоставь развернутый ответ."""

    logger.info(f"Отправка запроса в Claude CLI (история: {len(history)} сообщений)")

    # Отправляем вопрос в Claude CLI
    result = subprocess.run(
        ['claude', '--print', '--dangerously-skip-permissions', full_prompt],
        text=True,
        capture_output=True,
        check=True,
        cwd=os.path.dirname(os.path.abspath(db_path))
    )

    response = result.stdout.strip()
    return response


# ============================================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """
    Обработчик команды /start
    """
    welcome_text = """
👋 Добро пожаловать в **ChatGeist Analytics Bot**!
Я найду все релевантные сообщения и предоставлю подробный отчет! 📊

💡 **Новая возможность**: Вы можете нажать "Ответить" на моё сообщение, чтобы задать уточняющий вопрос с учётом контекста предыдущего диалога!
    """

    await message.answer(welcome_text, parse_mode="Markdown")


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
        # Отправляем запрос в Claude CLI для анализа БД
        import asyncio
        report = await asyncio.to_thread(ask_claude_with_context, user_query, history, DB_PATH)

        # Telegram имеет ограничение на длину сообщения (4096 символов)
        if len(report) <= 4096:
            # Пытаемся отправить с Markdown, если не получается - отправляем plain text
            try:
                await status_msg.edit_text(report, parse_mode="Markdown")
            except TelegramBadRequest as e:
                if "can't parse entities" in str(e):
                    logger.warning("Ошибка парсинга Markdown, отправляю как plain text")
                    await status_msg.edit_text(report)
                else:
                    raise
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
                try:
                    if i == 0:
                        await message.answer(part, parse_mode="Markdown")
                    else:
                        await message.answer(f"_(продолжение {i+1})_\n\n{part}", parse_mode="Markdown")
                except TelegramBadRequest as e:
                    if "can't parse entities" in str(e):
                        logger.warning(f"Ошибка парсинга Markdown в части {i+1}, отправляю как plain text")
                        if i == 0:
                            await message.answer(part)
                        else:
                            await message.answer(f"(продолжение {i+1})\n\n{part}")
                    else:
                        raise

    except FileNotFoundError as e:
        logger.error(f"База данных не найдена: {e}")
        await status_msg.edit_text(
            f"❌ База данных не найдена: `{DB_PATH}`\n\nПожалуйста, убедитесь, что файл базы данных существует.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса: {e}", exc_info=True)
        error_msg = str(e)
        if "claude" in error_msg.lower() or "command not found" in error_msg.lower():
            await status_msg.edit_text(
                "❌ Ошибка при вызове Claude CLI.\n\nУбедитесь, что Claude CLI установлен и доступен в PATH.",
                parse_mode="Markdown"
            )
        else:
            await status_msg.edit_text(
                f"❌ Произошла ошибка при анализе данных:\n\n`{error_msg}`",
                parse_mode="Markdown"
            )


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """
    Главная функция для запуска бота.
    """
    logger.info("Запуск расширенного бота...")

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
