"""
Telegram Bot для анализа сообщений из нескольких баз данных SQLite.
Версия с поддержкой переключения между чатами (multi-chat).
Использует Anthropic API для анализа.
"""

import os
import asyncio
import logging
import warnings
import json
import sqlite3
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from pdf_generator import generate_pdf
import anthropic

# Игнорируем предупреждения о deprecation
warnings.filterwarnings('ignore', category=DeprecationWarning)

# ============================================================================
# СИСТЕМА ЛИМИТОВ
# ============================================================================

# Whitelist пользователей (без лимитов)
WHITELIST_USER_IDS = {
    435878873,  # @tarados
    354910522,  # @dadonius
}

# Лимит запросов для обычных пользователей
DAILY_LIMIT = 5

# Хранилище использования: {user_id: {"date": "2026-01-22", "count": 3}}
usage_storage: dict[int, dict] = {}


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """
    Проверяет лимит запросов для пользователя.

    Returns:
        (allowed, remaining) - разрешён ли запрос и сколько осталось
    """
    # Whitelist - без лимитов
    if user_id in WHITELIST_USER_IDS:
        return True, -1  # -1 означает безлимит

    from datetime import date
    today = date.today().isoformat()

    # Получаем или создаём запись
    if user_id not in usage_storage:
        usage_storage[user_id] = {"date": today, "count": 0}

    user_usage = usage_storage[user_id]

    # Сброс счётчика если новый день
    if user_usage["date"] != today:
        user_usage["date"] = today
        user_usage["count"] = 0

    # Проверяем лимит
    remaining = DAILY_LIMIT - user_usage["count"]

    if remaining <= 0:
        return False, 0

    return True, remaining


def increment_usage(user_id: int) -> None:
    """Увеличивает счётчик использования."""
    if user_id in WHITELIST_USER_IDS:
        return

    from datetime import date
    today = date.today().isoformat()

    if user_id not in usage_storage:
        usage_storage[user_id] = {"date": today, "count": 0}

    user_usage = usage_storage[user_id]

    if user_usage["date"] != today:
        user_usage["date"] = today
        user_usage["count"] = 0

    user_usage["count"] += 1


# Хранилище активных запросов для возможности отмены
# Ключ: message_id статусного сообщения
# Значение: {"process": subprocess.Popen, "cancelled": bool}
active_requests: dict[int, dict] = {}

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

# Пути к промптам
PROMPTS_DIR = Path("prompts")

# Модель Claude
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Anthropic клиент
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# SQL Tool для Claude
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

# Конфигурация Skills
SKILLS = {
    "dossier": {
        "triggers": ["досье", "профиль", "кто такой", "кто такая", "информація про", "розкажи про користувача"],
        "file": "skills/dossier.md"
    },
    "search": {
        "triggers": ["найди", "пошук", "де згадується", "хто писав про", "знайти повідомлення"],
        "file": "skills/search.md"
    },
    "top": {
        "triggers": ["топ", "рейтинг", "найкращі", "кращі", "лучшие", "популярні"],
        "file": "skills/top.md"
    }
}

# Инициализация бота с FSM хранилищем
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

logger.info("✅ Multi-chat бот с Anthropic API готов")

# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def get_available_databases() -> list[dict]:
    """
    Сканирует папку databases/ и возвращает список доступных БД.

    Returns:
        Список словарей: [{"name": "durov", "path": "databases/durov.db", "size_mb": 1.5}, ...]
    """
    if not DB_ROOT.exists():
        return []

    databases = []
    for db_file in sorted(DB_ROOT.glob("*.db")):
        size_mb = db_file.stat().st_size / (1024 * 1024)
        databases.append({
            "name": db_file.stem,  # имя без .db
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
            result = result[:100]
            result_json = json.dumps(result, ensure_ascii=False, default=str)
            result_json = result_json[:-1] + ', {"_warning": "Результат обрезан до 100 записей"}]'

        return result_json

    except sqlite3.Error as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def load_prompt(filename: str) -> str:
    """
    Загружает промпт из файла.

    Args:
        filename: Имя файла относительно PROMPTS_DIR (например, "base.md" или "skills/dossier.md")

    Returns:
        Содержимое файла или пустую строку если файл не найден
    """
    prompt_path = PROMPTS_DIR / filename
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    logger.warning(f"Промпт не найден: {prompt_path}")
    return ""


def detect_skill(query: str) -> str | None:
    """
    Определяет, какой skill использовать на основе запроса.

    Args:
        query: Текст запроса пользователя

    Returns:
        Имя skill или None если подходящий не найден
    """
    query_lower = query.lower()

    for skill_name, skill_config in SKILLS.items():
        for trigger in skill_config["triggers"]:
            if trigger in query_lower:
                logger.info(f"Обнаружен skill: {skill_name} (триггер: '{trigger}')")
                return skill_name

    return None


async def get_conversation_history(message: Message, bot_id: int) -> list[dict]:
    """
    Собирает цепочку сообщений (историю диалога) через reply.
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


def build_system_prompt(db_filename: str, question: str) -> tuple[str, str | None]:
    """
    Формирует системный промпт для Claude.

    Returns:
        Tuple (system_prompt, skill_name)
    """
    # Загружаем базовый промпт
    base_prompt = load_prompt("base.md")
    if not base_prompt:
        base_prompt = """Ты — аналитик данных Telegram-чатов.
Используй инструмент execute_sql для выполнения SQL запросов к базе данных.
Таблица messages содержит: id, timestamp, date_iso, message, sender_id, sender_username, sender_display_name, reply_to_msg_id, reactions_count, reactions_detail, views, forwards, permalink.
Отвечай на украинском языке. Форматируй ответы простым текстом. Используй эмодзи для структурирования."""

    # Убираем {db_path} если есть - теперь используем tool
    base_prompt = base_prompt.replace("{db_path}", "через execute_sql")

    # Определяем skill
    skill_name = detect_skill(question)
    skill_prompt = ""

    if skill_name and skill_name in SKILLS:
        skill_file = SKILLS[skill_name]["file"]
        skill_prompt = load_prompt(skill_file)
        if skill_prompt:
            logger.info(f"Загружен skill: {skill_name}")

    # Собираем промпт
    system_prompt = base_prompt
    if skill_prompt:
        system_prompt += f"\n\n---\n\n{skill_prompt}"

    return system_prompt, skill_name


from typing import Callable

# Етапи аналізу для відображення прогресу
ANALYSIS_STAGES = [
    (5, "🔍 Аналізую запит..."),
    (15, "📊 Виконую SQL-запити..."),
    (30, "🤔 Обробляю дані..."),
    (60, "✏️ Формую відповідь..."),
    (120, "📝 Фінальна обробка..."),
]


def get_stage_status(elapsed_seconds: int) -> str:
    """Повертає статус на основі часу, що минув."""
    for threshold, status in ANALYSIS_STAGES:
        if elapsed_seconds < threshold:
            return status
    return "⏳ Майже готово..."


def get_cancel_keyboard(status_msg_id: int) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру с кнопкой отмены."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати", callback_data=f"cancel_request:{status_msg_id}")]
    ])


async def ask_claude_api(
    question: str,
    history: list[dict],
    db_filename: str,
    status_msg_id: int,
    status_callback: Callable[[str], None] | None = None
) -> str:
    """
    Отправляет запрос в Anthropic API с поддержкой tool use.

    Args:
        question: Вопрос пользователя
        history: История диалога
        db_filename: Имя файла БД
        status_msg_id: ID сообщения со статусом (для отмены)
        status_callback: Async callback для обновления статуса

    Returns:
        Ответ от Claude
    """
    import time

    db_path = str(DB_ROOT / db_filename)
    system_prompt, skill_name = build_system_prompt(db_filename, question)

    logger.info(f"Запрос к Claude API (БД: {db_filename}, история: {len(history)})")

    # Регистрируем запрос для возможности отмены
    active_requests[status_msg_id] = {"cancelled": False}

    try:
        # Формируем сообщения
        messages = []

        # Добавляем историю
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Добавляем текущий вопрос
        messages.append({"role": "user", "content": question})

        start_time = time.time()

        # Первое обновление статуса
        if status_callback:
            try:
                await status_callback("🔍 Аналізую запит...\n⏱ 0 сек")
            except Exception as e:
                logger.warning(f"Не удалось обновить статус: {e}")

        # Запрос к API в отдельном потоке (чтобы не блокировать event loop)
        loop = asyncio.get_event_loop()

        def make_api_call():
            return anthropic_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=[SQL_TOOL],
                messages=messages
            )

        response = await loop.run_in_executor(None, make_api_call)

        # Обрабатываем tool use в цикле
        iteration = 0
        while response.stop_reason == "tool_use":
            iteration += 1

            # Проверяем отмену
            if active_requests.get(status_msg_id, {}).get("cancelled"):
                raise asyncio.CancelledError("Запрос отменён пользователем")

            # Обновляем статус
            elapsed = int(time.time() - start_time)
            if status_callback:
                try:
                    status = get_stage_status(elapsed)
                    time_str = f"{elapsed // 60}:{elapsed % 60:02d}" if elapsed >= 60 else f"{elapsed} сек"
                    await status_callback(f"{status}\n⏱ {time_str}")
                except Exception:
                    pass

            # Находим tool use блоки
            tool_uses = [block for block in response.content if block.type == "tool_use"]

            # Добавляем ответ ассистента
            messages.append({"role": "assistant", "content": response.content})

            # Выполняем каждый tool call
            tool_results = []
            for tool_use in tool_uses:
                if tool_use.name == "execute_sql":
                    query = tool_use.input.get("query", "")
                    logger.info(f"SQL запрос #{iteration}: {query[:100]}...")
                    result = execute_sql(db_path, query)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result
                    })

            # Добавляем результаты
            messages.append({"role": "user", "content": tool_results})

            # Следующий запрос
            response = await loop.run_in_executor(None, lambda: anthropic_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=[SQL_TOOL],
                messages=messages
            ))

        # Извлекаем текстовый ответ
        text_blocks = [block.text for block in response.content if hasattr(block, 'text')]
        return "\n".join(text_blocks) if text_blocks else "Не удалось получить ответ"

    finally:
        # Очищаем запись о запросе
        active_requests.pop(status_msg_id, None)


# ============================================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================================

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start"""
    # Автоматично обираємо базу даних
    databases = get_available_databases()
    if databases:
        await state.update_data(current_db=databases[0]["filename"])
        chat_name = databases[0]["name"]
        welcome_text = f"""
👋 Ласкаво просимо до ChatGeist Bot!

Цей бот аналізує історію Telegram-чату за допомогою AI.

📊 Підключено чат: {chat_name}

💡 Просто ставте запитання!

📝 Приклади:
• Скільки всього повідомлень?
• Хто найактивніший учасник?
• Знайди повідомлення про Python
• Досьє на @username
    """
    else:
        welcome_text = "❌ Немає доступних баз даних."
    await message.answer(welcome_text)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help"""
    help_text = """
📖 Довідка по боту

💡 Як користуватися:
1. Поставте запитання текстом
2. Для уточнення — дайте відповідь на повідомлення бота

📝 Приклади запитань:
• Скільки всього повідомлень?
• Хто найактивніший учасник?
• Про що говорили вчора?
• Знайди повідомлення про Python
• Досьє на @username
• Топ кафе / ресторанів

🔒 Безпека: всі запити обробляються через захищений Anthropic API.
    """
    await message.answer(help_text)


# Команда /chats видалена — бот працює з одним чатом


@dp.callback_query(F.data.startswith("cancel_request:"))
async def on_cancel_request(callback: CallbackQuery):
    """Обработчик отмены запроса"""
    try:
        msg_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Помилка скасування", show_alert=True)
        return

    if msg_id in active_requests:
        active_requests[msg_id]["cancelled"] = True
        await callback.answer("⏹ Скасування запиту...")
        logger.info(f"Пользователь запросил отмену запроса {msg_id}")
    else:
        await callback.answer("Запит вже завершено", show_alert=False)


# Команда /current видалена — бот працює з одним чатом


@dp.message(F.text)
async def handle_query(message: Message, state: FSMContext):
    """Обработчик текстовых запросов"""
    user_query = message.text.strip()

    if not user_query:
        await message.answer("❌ Будь ласка, надішліть непорожній запит.")
        return

    # Проверяем лимит запросов
    user_id = message.from_user.id
    allowed, remaining = check_rate_limit(user_id)

    if not allowed:
        await message.answer(
            "⚠️ Ви вичерпали денний ліміт запитів.\n\n"
            f"Ліміт: {DAILY_LIMIT} запитів на день.\n"
            "Спробуйте завтра!"
        )
        logger.info(f"Пользователь {user_id} превысил лимит запросов")
        return

    # Отримуємо поточний чат (автовибір якщо не обрано)
    user_data = await state.get_data()
    current_db = user_data.get("current_db")

    if not current_db:
        # Автоматично обираємо першу доступну базу
        databases = get_available_databases()
        if not databases:
            await message.answer("❌ Немає доступних баз даних.")
            return
        current_db = databases[0]["filename"]
        await state.update_data(current_db=current_db)

    # Перевіряємо існування БД
    db_path = DB_ROOT / current_db
    if not db_path.exists():
        await message.answer("❌ Базу даних не знайдено.")
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
    skill_name = detect_skill(user_query)
    skill_label = f" [{skill_name}]" if skill_name else ""

    if is_reply and history:
        status_msg = await message.answer(
            f"🔄 Аналізую [{chat_name}]{skill_label} з урахуванням контексту...",
            reply_markup=get_cancel_keyboard(0)  # Временный ID, обновим ниже
        )
    else:
        status_msg = await message.answer(
            f"🔄 Аналізую [{chat_name}]{skill_label}, зачекайте...",
            reply_markup=get_cancel_keyboard(0)  # Временный ID, обновим ниже
        )

    # Обновляем клавиатуру с правильным message_id
    cancel_keyboard = get_cancel_keyboard(status_msg.message_id)

    # Callback для обновления статуса
    async def update_status(new_status: str):
        try:
            await status_msg.edit_text(
                f"🔄 [{chat_name}]{skill_label}\n{new_status}",
                reply_markup=cancel_keyboard
            )
        except TelegramBadRequest:
            pass  # Игнорируем ошибки редактирования (например, текст не изменился)

    try:
        # Запрос к Claude API
        report = await ask_claude_api(user_query, history, current_db, status_msg.message_id, update_status)

        # Увеличиваем счётчик использования после успешного запроса
        increment_usage(user_id)
        if remaining > 0:
            logger.info(f"Пользователь {user_id}: использовано запросов, осталось {remaining - 1}")

        # Відправляємо відповідь
        # PDF генерируется для: 1) skill "dossier" (всегда), 2) длинных ответов > 2500
        logger.info(f"Довжина відповіді: {len(report)} символів, skill: {skill_name}")
        force_pdf = skill_name == "dossier"

        if not force_pdf and len(report) <= 2500:
            # Короткі відповіді — текстом з форматуванням
            try:
                await status_msg.edit_text(report, reply_markup=None, parse_mode="Markdown")
            except TelegramBadRequest:
                # Якщо Markdown не парситься — відправляємо без форматування
                await status_msg.edit_text(report, reply_markup=None)
        else:
            # PDF для досьє або довгих відповідей
            reason = "skill=dossier" if force_pdf else f"довжина {len(report)} > 2500"
            logger.info(f"Генерую PDF ({reason})")
            pdf_buffer = generate_pdf(report, title=f"Звіт: {chat_name}")

            # Видаляємо статусне повідомлення
            try:
                await status_msg.delete()
            except Exception:
                pass  # Ігноруємо помилки видалення

            # Відправляємо тільки PDF
            pdf_file = BufferedInputFile(
                pdf_buffer.read(),
                filename=f"report_{chat_name}.pdf"
            )
            await message.answer_document(
                document=pdf_file,
                caption="📊 Звіт готовий"
            )

    except asyncio.CancelledError:
        logger.info(f"Запрос отменён пользователем (msg_id={status_msg.message_id})")
        await status_msg.edit_text("⏹ Запит скасовано.", reply_markup=None)

    except anthropic.APIError as e:
        logger.error(f"Anthropic API ошибка: {e}")
        await status_msg.edit_text(f"❌ Помилка API Claude:\n\n{str(e)[:500]}", reply_markup=None)

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Помилка:\n\n{str(e)[:500]}", reply_markup=None)


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """Главная функция запуска бота"""
    logger.info("🚀 Запуск бота с Anthropic API...")

    # Проверяем папку с БД
    if not DB_ROOT.exists():
        logger.warning(f"Папка {DB_ROOT} не существует! Создаю...")
        DB_ROOT.mkdir(parents=True, exist_ok=True)

    databases = get_available_databases()
    logger.info(f"📂 Найдено баз данных: {len(databases)}")
    logger.info(f"🤖 Модель: {CLAUDE_MODEL}")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
