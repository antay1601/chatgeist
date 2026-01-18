"""
Telegram Bot для анализа сообщений из нескольких баз данных SQLite.
Версия с поддержкой переключения между чатами (multi-chat).
Использует Claude CLI в Docker sandbox для безопасного анализа.
"""

import os
import asyncio
import logging
import warnings
import subprocess
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
# InlineKeyboardBuilder більше не потрібен — бот працює з одним чатом
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from pdf_generator import generate_pdf

# Игнорируем предупреждения о deprecation
warnings.filterwarnings('ignore', category=DeprecationWarning)

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

# Пути к базам данных
DB_ROOT_HOST = Path("databases")          # Папка на хосте
DB_ROOT_DOCKER = "/workspace/dbs"         # Папка внутри контейнера

# Пути к промптам
PROMPTS_DIR = Path("prompts")

# Docker контейнер
DOCKER_CONTAINER = "claude-sandbox"

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

logger.info("✅ Multi-chat бот с Docker sandbox готов")

# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def get_available_databases() -> list[dict]:
    """
    Сканирует папку databases/ и возвращает список доступных БД.

    Returns:
        Список словарей: [{"name": "durov", "path": "databases/durov.db", "size_mb": 1.5}, ...]
    """
    if not DB_ROOT_HOST.exists():
        return []

    databases = []
    for db_file in sorted(DB_ROOT_HOST.glob("*.db")):
        size_mb = db_file.stat().st_size / (1024 * 1024)
        databases.append({
            "name": db_file.stem,  # имя без .db
            "filename": db_file.name,
            "path": str(db_file),
            "size_mb": round(size_mb, 2),
        })

    return databases


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


def check_docker_container() -> bool:
    """
    Проверяет, запущен ли Docker контейнер.
    """
    try:
        result = subprocess.run(
            ['docker', 'ps', '--filter', f'name={DOCKER_CONTAINER}', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            timeout=10
        )
        return DOCKER_CONTAINER in result.stdout
    except Exception:
        return False


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


def build_claude_prompt(question: str, history: list[dict], db_filename: str) -> tuple[str, str | None]:
    """
    Формирует промпт для Claude.

    Returns:
        Tuple (full_prompt, skill_name)
    """
    docker_db_path = f"{DB_ROOT_DOCKER}/{db_filename}"

    # Загружаем базовый промпт
    base_prompt = load_prompt("base.md")
    if not base_prompt:
        base_prompt = """Ты — аналитик данных Telegram-чатов.
Используй базу данных SQLite для анализа.
Таблица messages содержит: id, timestamp, date_iso, message, sender_id, sender_username, sender_display_name, reply_to_msg_id, reactions_count, reactions_detail, views, forwards, permalink."""

    base_prompt = base_prompt.replace("{db_path}", docker_db_path)

    # Определяем skill
    skill_name = detect_skill(question)
    skill_prompt = ""

    if skill_name and skill_name in SKILLS:
        skill_file = SKILLS[skill_name]["file"]
        skill_prompt = load_prompt(skill_file)
        if skill_prompt:
            logger.info(f"Загружен skill: {skill_name}")

    # История диалога
    history_section = ""
    if history:
        history_text = "\n\n".join([
            f"{'Пользователь' if msg['role'] == 'user' else 'Ассистент'}: {msg['content']}"
            for msg in history
        ])
        history_section = f"\n\n## История диалога\n\n{history_text}"

    # Собираем промпт
    full_prompt = base_prompt
    if skill_prompt:
        full_prompt += f"\n\n---\n\n{skill_prompt}"
    if history_section:
        full_prompt += history_section
    full_prompt += f"\n\n## Текущий запрос\n\n{question}"

    return full_prompt, skill_name


import re
import threading
import queue
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


async def ask_claude_streaming(
    question: str,
    history: list[dict],
    db_filename: str,
    status_msg_id: int,
    status_callback: Callable[[str], None] | None = None
) -> str:
    """
    Отправляет запрос в Claude CLI с отображением прогресса и возможностью отмены.

    Args:
        question: Вопрос пользователя
        history: История диалога
        db_filename: Имя файла БД
        status_msg_id: ID сообщения со статусом (для отмены)
        status_callback: Async callback для обновления статуса

    Returns:
        Ответ от Claude

    Raises:
        asyncio.CancelledError: Если запрос был отменён пользователем
    """
    import asyncio
    import time

    full_prompt, skill_name = build_claude_prompt(question, history, db_filename)
    logger.info(f"Запрос к Claude (БД: {db_filename}, история: {len(history)})")

    output_lines: list[str] = []
    error_lines: list[str] = []
    process_done = False
    process_ref: subprocess.Popen | None = None

    def run_process():
        """Запускает процесс и читает вывод."""
        nonlocal process_done, process_ref
        process = subprocess.Popen(
            [
                'docker', 'exec',
                '-u', 'node',
                DOCKER_CONTAINER,
                'claude', '--print', '--dangerously-skip-permissions',
                full_prompt
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        process_ref = process

        # Сохраняем процесс для возможности отмены
        if status_msg_id in active_requests:
            active_requests[status_msg_id]["process"] = process

        # Читаем stderr
        def read_stderr():
            for line in process.stderr:
                error_lines.append(line)

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()

        # Читаем stdout
        for line in process.stdout:
            output_lines.append(line)

        process.wait()
        stderr_thread.join(timeout=1)
        process_done = True

        return process.returncode

    # Регистрируем запрос
    active_requests[status_msg_id] = {"process": None, "cancelled": False}

    try:
        # Запускаем процесс в отдельном потоке
        loop = asyncio.get_event_loop()
        process_task = loop.run_in_executor(None, run_process)

        # Обновляем статус по таймеру
        start_time = time.time()
        last_status = ""
        update_count = 0

        # Первое обновление сразу
        if status_callback:
            try:
                await status_callback("🔍 Аналізую запит...\n⏱ 0 сек")
                logger.info("Статус обновлён: начало")
            except Exception as e:
                logger.warning(f"Не удалось обновить статус (начало): {e}")

        while not process_done:
            await asyncio.sleep(3)

            # Проверяем отмену
            if active_requests.get(status_msg_id, {}).get("cancelled"):
                logger.info(f"Запрос {status_msg_id} отменён пользователем")
                if process_ref:
                    process_ref.terminate()
                    try:
                        process_ref.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process_ref.kill()
                raise asyncio.CancelledError("Запрос отменён пользователем")

            elapsed = int(time.time() - start_time)
            new_status = get_stage_status(elapsed)
            time_str = f"{elapsed // 60}:{elapsed % 60:02d}" if elapsed >= 60 else f"{elapsed} сек"
            full_status = f"{new_status}\n⏱ {time_str}"

            update_count += 1
            logger.info(f"Цикл статуса #{update_count}: {elapsed} сек, process_done={process_done}")

            if status_callback:
                try:
                    await status_callback(full_status)
                    logger.info(f"Статус обновлён: {new_status}")
                except Exception as e:
                    logger.warning(f"Не удалось обновить статус: {e}")

        # Получаем результат
        return_code = await process_task

        if return_code != 0:
            error_text = "".join(error_lines)
            raise subprocess.CalledProcessError(return_code, "claude", stderr=error_text)

        return "".join(output_lines).strip()

    finally:
        # Очищаем запись о запросе
        active_requests.pop(status_msg_id, None)


def ask_claude_secure(question: str, history: list[dict], db_filename: str) -> str:
    """
    Синхронная версия для обратной совместимости.
    """
    full_prompt, _ = build_claude_prompt(question, history, db_filename)
    logger.info(f"Запрос к Claude (БД: {db_filename}, история: {len(history)})")

    result = subprocess.run(
        [
            'docker', 'exec',
            '-u', 'node',
            DOCKER_CONTAINER,
            'claude', '--print', '--dangerously-skip-permissions',
            full_prompt
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=1200
    )

    return result.stdout.strip()


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

🔒 Безпека: всі запити обробляються в ізольованому Docker-контейнері.
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
    db_path = DB_ROOT_HOST / current_db
    if not db_path.exists():
        await message.answer("❌ Базу даних не знайдено.")
        await state.update_data(current_db=None)
        return

    # Проверяем Docker контейнер
    if not check_docker_container():
        await message.answer(
            "❌ Docker контейнер не запущено.\n\n"
            "Запустіть: `docker compose up -d`"
        )
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
        # Запрос к Claude со стримингом статуса
        report = await ask_claude_streaming(user_query, history, current_db, status_msg.message_id, update_status)

        # Відправляємо відповідь (PDF для довгих > 2500 символів)
        logger.info(f"Довжина відповіді: {len(report)} символів")
        if len(report) <= 2500:
            # Короткі відповіді — текстом
            await status_msg.edit_text(report, reply_markup=None)
        else:
            # Довгі відповіді — тільки PDF
            logger.info(f"Генерую PDF (відповідь {len(report)} > 2500)")
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

    except subprocess.TimeoutExpired:
        logger.error("Таймаут при обработке запроса")
        await status_msg.edit_text("❌ Перевищено час очікування (20 хвилин).\nСпробуйте спростити запит.", reply_markup=None)

    except subprocess.CalledProcessError as e:
        error_output = e.stderr if e.stderr else e.stdout
        if not error_output:
            error_output = f"Exit code: {e.returncode}"
        logger.error(f"Ошибка Claude CLI: {error_output}")
        await status_msg.edit_text(f"❌ Помилка API Claude:\n\n{error_output[:500]}", reply_markup=None)

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Помилка:\n\n{str(e)[:500]}", reply_markup=None)


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """Главная функция запуска бота"""
    logger.info("🚀 Запуск Multi-chat бота...")

    # Проверяем папку с БД
    if not DB_ROOT_HOST.exists():
        logger.warning(f"Папка {DB_ROOT_HOST} не существует! Создаю...")
        DB_ROOT_HOST.mkdir(parents=True, exist_ok=True)

    databases = get_available_databases()
    logger.info(f"📂 Найдено баз данных: {len(databases)}")

    # Проверяем Docker
    if check_docker_container():
        logger.info(f"✅ Docker контейнер {DOCKER_CONTAINER} запущен")
    else:
        logger.warning(f"⚠️ Docker контейнер {DOCKER_CONTAINER} не запущен!")
        logger.info("   Запустите: docker compose up -d")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
