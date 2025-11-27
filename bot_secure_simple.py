"""
Telegram Bot для анализа сообщений из базы данных SQLite с AI-анализом.
Безопасная версия с запуском Claude CLI в Docker sandbox.
Версия с упрощенным форматированием (Plain Text) для избежания проблем с рендерингом.
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

logger.info("✅ Безопасный бот (Simple Formatting) с Docker sandbox для Claude CLI")

# Путь к базе данных
DB_PATH = "telegram_messages.db"

# Имя Docker контейнера
DOCKER_CONTAINER = "claude-sandbox"

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


def ask_claude_with_context_secure(question: str, history: list[dict], db_path: str = "telegram_messages.db") -> str:
    """
    Безопасно отправляет вопрос в Claude CLI через Docker sandbox.

    Args:
        question: Текущий вопрос пользователя
        history: История предыдущих сообщений
        db_path: Путь к базе данных (внутри контейнера всегда /workspace/telegram_messages.db)

    Returns:
        Ответ от Claude

    Raises:
        FileNotFoundError: Если БД не найдена на хосте
        subprocess.CalledProcessError: Если Docker контейнер не запущен
    """
    # Проверяем существование БД на хосте
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"База данных {db_path} не найдена")

    # Проверяем, что Docker контейнер запущен
    check_container = subprocess.run(
        ['docker', 'ps', '--filter', f'name={DOCKER_CONTAINER}', '--format', '{{.Names}}'],
        capture_output=True,
        text=True
    )

    if DOCKER_CONTAINER not in check_container.stdout:
        raise RuntimeError(
            f"Docker контейнер {DOCKER_CONTAINER} не запущен. "
            "Запустите его командой: docker-compose up -d"
        )

    # Инструкция по форматированию для Claude
    formatting_instruction = (
        "\nВАЖНО: Не используй Markdown-разметку (жирный шрифт, курсив, заголовки). "
        "Отвечай простым текстом. Используй эмодзи и отступы для структурирования ответа. "
        "Делай ответ легко читаемым в виде простого текста."
    )

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

Пожалуйста, проанализируй данные в БД с учётом контекста предыдущих вопросов и ответов, и предоставь развернутый ответ на текущий вопрос.
{formatting_instruction}"""
    else:
        # Нет истории - обычный промпт
        full_prompt = f"""Используй базу данных telegram_messages.db для поиска ответа на следующий вопрос.

База данных содержит таблицу messages со следующими полями:
- id, timestamp, date_iso, message (текст сообщения)
- sender_username, sender_display_name
- reply_to_msg_id, reactions_count, views, forwards
- permalink и другие поля

Вопрос: {question}

Пожалуйста, проанализируй данные в БД и предоставь развернутый ответ.
{formatting_instruction}"""

    logger.info(f"Отправка запроса в Claude CLI через Docker (история: {len(history)} сообщений)")

    # Выполняем Claude CLI внутри Docker контейнера
    # БД доступна только в режиме read-only внутри /workspace
    result = subprocess.run(
        [
            'docker', 'exec',
            DOCKER_CONTAINER,
            'claude', '--print', '--dangerously-skip-permissions',
            full_prompt
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=300  # 5 минут таймаут
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
👋 Добро пожаловать в ChatGeist Analytics Bot!
Я найду все релевантные сообщения и предоставлю подробный отчет! 📊

💡 Новая возможность: Вы можете нажать "Ответить" на моё сообщение, чтобы задать уточняющий вопрос с учётом контекста предыдущего диалога!

🔒 Безопасность: Все запросы обрабатываются в изолированном Docker окружении с ограниченным доступом.
    """

    await message.answer(welcome_text)  # Без parse_mode


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
        # Отправляем запрос в Claude CLI через Docker sandbox
        import asyncio
        report = await asyncio.to_thread(ask_claude_with_context_secure, user_query, history, DB_PATH)

        # Telegram имеет ограничение на длину сообщения (4096 символов)
        if len(report) <= 4096:
            # Отправляем как обычный текст без Markdown
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
        logger.error(f"Docker контейнер не запущен: {e}")
        await status_msg.edit_text(
            "❌ Ошибка: Docker контейнер не запущен.\n\nЗапустите командой:\ndocker-compose up -d"
        )
    except subprocess.TimeoutExpired:
        logger.error("Таймаут при обработке запроса")
        await status_msg.edit_text(
            "❌ Превышено время ожидания ответа (5 минут).\n\nПопробуйте упростить запрос или попробовать позже."
        )
    except subprocess.CalledProcessError as e:
        # Claude CLI может писать ошибки (например, про баланс) в stdout
        error_output = e.stderr if e.stderr else e.stdout
        if not error_output:
            error_output = "Неизвестная ошибка (exit code {})".format(e.returncode)
            
        logger.error(f"Ошибка выполнения Claude CLI: {error_output}")
        await status_msg.edit_text(
            f"❌ Ошибка API Claude:\n\n{error_output}"
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
    logger.info("Запуск безопасного бота с Docker sandbox...")

    # Проверяем наличие базы данных
    if not os.path.exists(DB_PATH):
        logger.warning(f"База данных {DB_PATH} не найдена! Бот запустится, но поиск будет невозможен.")

    # Проверяем, что Docker контейнер запущен
    try:
        check_container = subprocess.run(
            ['docker', 'ps', '--filter', f'name={DOCKER_CONTAINER}', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            check=True
        )

        if DOCKER_CONTAINER not in check_container.stdout:
            logger.error(f"Docker контейнер {DOCKER_CONTAINER} не запущен!")
            logger.info("Запустите контейнер командой: docker-compose up -d")
            return
        else:
            logger.info(f"✅ Docker контейнер {DOCKER_CONTAINER} запущен")

    except FileNotFoundError:
        logger.error("Docker не установлен или не доступен в PATH!")
        return
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при проверке Docker контейнера: {e}")
        return

    try:
        # Запускаем polling
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
