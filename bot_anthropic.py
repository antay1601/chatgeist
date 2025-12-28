"""
Telegram Bot для анализа сообщений из нескольких баз данных SQLite.
Версия с прямым использованием Anthropic API (без Docker).
"""

import os
import logging
import warnings
import json
import sqlite3
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import anthropic

# Игнорируем предупреждения о deprecation
warnings.filterwarnings('ignore', category=DeprecationWarning)

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в переменных окружения!")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY не найден в переменных окружения!")

# Пути к базам данных
DB_ROOT = Path("databases")

# Модель Claude
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Инициализация клиентов
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

logger.info("Anthropic API бот готов")

# ============================================================================
# TOOL DEFINITIONS
# ============================================================================

SQL_TOOL = {
    "name": "execute_sql",
    "description": "Выполняет SQL запрос к базе данных SQLite с сообщениями Telegram чата. "
                   "Используй SELECT для чтения данных. "
                   "Таблица messages содержит поля: id, timestamp, date_iso, message (текст), "
                   "sender_id, sender_username, sender_display_name, "
                   "reply_to_msg_id, reactions_count, reactions_detail, views, forwards, permalink.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "SQL запрос для выполнения (только SELECT)"
            }
        },
        "required": ["query"]
    }
}

TOOLS = [SQL_TOOL]

# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def get_available_databases() -> list[dict]:
    """
    Сканирует папку databases/ и возвращает список доступных БД.
    """
    if not DB_ROOT.exists():
        return []

    databases = []
    for db_file in sorted(DB_ROOT.glob("*.db")):
        size_mb = db_file.stat().st_size / (1024 * 1024)
        databases.append({
            "name": db_file.stem,
            "filename": db_file.name,
            "path": str(db_file),
            "size_mb": round(size_mb, 2),
        })

    return databases


def execute_sql(db_path: str, query: str) -> str:
    """
    Выполняет SQL запрос к базе данных.
    Возвращает результат в виде JSON строки.
    """
    query_lower = query.strip().lower()
    if not query_lower.startswith("select"):
        return json.dumps({"error": "Разрешены только SELECT запросы"}, ensure_ascii=False)

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()

        # Конвертируем в список словарей
        result = [dict(row) for row in rows]

        # Ограничиваем размер результата
        result_json = json.dumps(result, ensure_ascii=False, default=str)
        if len(result_json) > 50000:
            # Обрезаем и добавляем предупреждение
            result = result[:100]
            result_json = json.dumps(result, ensure_ascii=False, default=str)
            result_json = result_json[:-1] + ', {"_warning": "Результат обрезан до 100 записей"}]'

        return result_json

    except sqlite3.Error as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def get_conversation_history(message: Message, bot_id: int) -> list[dict]:
    """
    Собирает цепочку сообщений (историю диалога) через reply.
    Возвращает в формате Anthropic API.
    """
    history = []
    current = message.reply_to_message

    while current:
        role = "assistant" if current.from_user.id == bot_id else "user"

        if current.text:
            content = current.text
            if role == "assistant":
                # Пропускаем статусные сообщения
                if content.startswith("🔄") or content.startswith("_(продолжение"):
                    current = current.reply_to_message
                    continue
                if content.startswith("(продолжение"):
                    content = '\n'.join(content.split('\n')[2:])

            history.append({"role": role, "content": content})

        current = current.reply_to_message

    history.reverse()
    return history


async def ask_claude(question: str, history: list[dict], db_path: str) -> str:
    """
    Отправляет запрос в Anthropic API с поддержкой tool use.
    """
    system_prompt = f"""Ты аналитик Telegram-чатов. У тебя есть доступ к базе данных SQLite с сообщениями.

Таблица messages содержит поля:
- id (INTEGER) - ID сообщения
- timestamp (INTEGER) - Unix timestamp
- date_iso (TEXT) - дата в ISO формате
- message (TEXT) - текст сообщения
- sender_id (INTEGER) - ID отправителя
- sender_username (TEXT) - username отправителя
- sender_display_name (TEXT) - отображаемое имя
- reply_to_msg_id (INTEGER) - ID сообщения, на которое ответили
- reactions_count (INTEGER) - количество реакций
- reactions_detail (TEXT) - детали реакций (JSON)
- views (INTEGER) - количество просмотров
- forwards (INTEGER) - количество пересылок
- permalink (TEXT) - ссылка на сообщение

Используй SQL запросы для анализа данных. Отвечай на русском языке.
Форматируй ответы простым текстом без Markdown. Используй эмодзи для структурирования."""

    # Формируем сообщения
    messages = history.copy() if history else []
    messages.append({"role": "user", "content": question})

    logger.info(f"Запрос к Claude API (история: {len(history)})")

    # Первый запрос
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=system_prompt,
        tools=TOOLS,
        messages=messages
    )

    # Обрабатываем tool use в цикле
    while response.stop_reason == "tool_use":
        # Находим tool use блоки
        tool_uses = [block for block in response.content if block.type == "tool_use"]

        # Добавляем ответ ассистента
        messages.append({"role": "assistant", "content": response.content})

        # Выполняем каждый tool call
        tool_results = []
        for tool_use in tool_uses:
            if tool_use.name == "execute_sql":
                query = tool_use.input.get("query", "")
                logger.info(f"SQL запрос: {query[:100]}...")
                result = execute_sql(db_path, query)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result
                })

        # Добавляем результаты
        messages.append({"role": "user", "content": tool_results})

        # Следующий запрос
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages
        )

    # Извлекаем текстовый ответ
    text_blocks = [block.text for block in response.content if hasattr(block, 'text')]
    return "\n".join(text_blocks) if text_blocks else "Не удалось получить ответ"


# ============================================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================================

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start"""
    welcome_text = """
Добро пожаловать в ChatGeist Bot!

Этот бот анализирует историю Telegram-чатов с помощью Claude AI.

Команды:
  /chats - выбрать чат для анализа
  /current - показать текущий выбранный чат
  /help - справка

Выберите чат через /chats, затем задавайте вопросы!
    """
    await message.answer(welcome_text)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help"""
    help_text = """
Справка по боту

/chats - показать список доступных чатов
/current - какой чат сейчас выбран
/start - приветствие

Как пользоваться:
1. Выберите чат через /chats
2. Задайте вопрос текстом
3. Для уточнения - ответьте на сообщение бота

Примеры вопросов:
- Сколько всего сообщений?
- Кто самый активный участник?
- О чём говорили вчера?
- Найди сообщения про Python
    """
    await message.answer(help_text)


@dp.message(Command("chats"))
async def cmd_chats(message: Message):
    """Команда /chats - показать список доступных БД"""
    databases = get_available_databases()

    if not databases:
        await message.answer(
            "Нет доступных баз данных.\n\n"
            f"Убедитесь, что папка `{DB_ROOT}/` содержит .db файлы.\n"
            "Используйте `python update_manager.py` для загрузки чатов."
        )
        return

    builder = InlineKeyboardBuilder()
    for db in databases:
        label = f"{db['name']} ({db['size_mb']} MB)"
        builder.button(text=label, callback_data=f"select_db:{db['filename']}")

    builder.adjust(1)

    text = f"Доступно баз данных: {len(databases)}\n\nВыберите чат для анализа:"
    await message.answer(text, reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("select_db:"))
async def on_db_select(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора БД"""
    selected_db = callback.data.split(":")[1]

    db_path = DB_ROOT / selected_db
    if not db_path.exists():
        await callback.answer("База данных не найдена", show_alert=True)
        return

    await state.update_data(current_db=selected_db)

    chat_name = selected_db.replace('.db', '')
    await callback.message.edit_text(
        f"Выбран чат: {chat_name}\n\n"
        f"Теперь вы можете задавать вопросы об этом чате.\n"
        f"Для смены чата используйте /chats"
    )
    await callback.answer()


@dp.message(Command("current"))
async def cmd_current(message: Message, state: FSMContext):
    """Команда /current - показать текущий выбранный чат"""
    user_data = await state.get_data()
    current_db = user_data.get("current_db")

    if not current_db:
        await message.answer(
            "Чат не выбран.\n\n"
            "Используйте /chats чтобы выбрать базу данных."
        )
        return

    chat_name = current_db.replace('.db', '')
    db_path = DB_ROOT / current_db

    if db_path.exists():
        size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)
        await message.answer(
            f"Текущий чат: {chat_name}\n"
            f"Размер БД: {size_mb} MB\n\n"
            f"Для смены используйте /chats"
        )
    else:
        await message.answer(f"БД {current_db} не найдена. Выберите другой чат: /chats")
        await state.update_data(current_db=None)


@dp.message(F.text)
async def handle_query(message: Message, state: FSMContext):
    """Обработчик текстовых запросов"""
    user_query = message.text.strip()

    if not user_query:
        await message.answer("Пожалуйста, отправьте непустой запрос.")
        return

    user_data = await state.get_data()
    current_db = user_data.get("current_db")

    if not current_db:
        await message.answer(
            "Чат не выбран!\n\n"
            "Сначала выберите чат через /chats"
        )
        return

    db_path = DB_ROOT / current_db
    if not db_path.exists():
        await message.answer(f"База данных {current_db} не найдена.\nВыберите другой чат: /chats")
        await state.update_data(current_db=None)
        return

    # Собираем историю диалога
    is_reply = message.reply_to_message is not None
    history = []

    if is_reply:
        bot_info = await bot.me()
        history = await get_conversation_history(message, bot_info.id)
        logger.info(f"Запрос с контекстом ({len(history)} сообщений): {user_query[:50]}...")
    else:
        logger.info(f"Новый запрос: {user_query[:50]}...")

    # Статусное сообщение
    chat_name = current_db.replace('.db', '')
    if is_reply and history:
        status_msg = await message.answer(f"Анализирую [{chat_name}] с учётом контекста...")
    else:
        status_msg = await message.answer(f"Анализирую [{chat_name}], подождите...")

    try:
        report = await ask_claude(user_query, history, str(db_path))

        if len(report) <= 4096:
            await status_msg.edit_text(report)
        else:
            await status_msg.delete()

            parts = []
            current_part = []
            current_length = 0

            for line in report.split('\n'):
                line_length = len(line) + 1
                if current_length + line_length > 4000:
                    parts.append('\n'.join(current_part))
                    current_part = [line]
                    current_length = line_length
                else:
                    current_part.append(line)
                    current_length += line_length

            if current_part:
                parts.append('\n'.join(current_part))

            for i, part in enumerate(parts):
                if i == 0:
                    await message.answer(part)
                else:
                    await message.answer(f"(продолжение {i+1})\n\n{part}")

    except anthropic.APIError as e:
        logger.error(f"Anthropic API ошибка: {e}")
        await status_msg.edit_text(f"Ошибка API: {str(e)[:500]}")

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"Ошибка: {str(e)[:500]}")


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """Главная функция запуска бота"""
    logger.info("Запуск Anthropic API бота...")

    if not DB_ROOT.exists():
        logger.warning(f"Папка {DB_ROOT} не существует! Создаю...")
        DB_ROOT.mkdir(parents=True, exist_ok=True)

    databases = get_available_databases()
    logger.info(f"Найдено баз данных: {len(databases)}")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
