"""
Telegram Bot для анализа сообщений из базы данных SQLite с AI-анализом.
"""

import os
import logging
import warnings
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
from ask_claude_cli import ask_claude

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

logger.info("✅ Бот использует Claude CLI для анализа данных")

# Путь к базе данных
DB_PATH = "telegram_messages.db"

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
    """

    await message.answer(welcome_text, parse_mode="Markdown")


@dp.message(F.text)
async def handle_query(message: Message):
    """
    Обработчик текстовых запросов пользователя.
    """
    user_query = message.text.strip()
    print(user_query)

    if not user_query:
        await message.answer("❌ Пожалуйста, отправьте непустой запрос.")
        return

    # Уведомляем пользователя о начале анализа
    status_msg = await message.answer("🔄 Анализирую данные, пожалуйста подождите...")

    try:
        logger.info(f"Обработка запроса: {user_query}")

        # Отправляем запрос в Claude CLI для анализа БД
        import asyncio
        report = await asyncio.to_thread(ask_claude, user_query, DB_PATH, return_only=True)

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
    logger.info("Запуск бота...")

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
