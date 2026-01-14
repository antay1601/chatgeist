"""
Telegram Bot для анализа сообщений из нескольких баз данных SQLite.
Версия с поддержкой переключения между чатами (multi-chat).
Использует Claude CLI в Docker sandbox для безопасного анализа.
"""

import os
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
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from pdf_generator import generate_pdf

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
        "triggers": ["досье", "профиль", "кто такой", "кто такая", "информация о", "расскажи о пользователе"],
        "file": "skills/dossier.md"
    },
    "summary": {
        "triggers": ["саммари", "дайджест", "о чём говорили", "что обсуждали", "главное за", "что было вчера", "что было сегодня"],
        "file": "skills/summary.md"
    },
    "search": {
        "triggers": ["найди", "поиск", "где упоминается", "кто писал про", "найти сообщения"],
        "file": "skills/search.md"
    },
    "top": {
        "triggers": ["топ", "рейтинг", "самые активные", "популярные", "больше всех", "лучшие"],
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


def ask_claude_secure(question: str, history: list[dict], db_filename: str) -> str:
    """
    Отправляет запрос в Claude CLI через Docker sandbox.

    Args:
        question: Вопрос пользователя
        history: История диалога
        db_filename: Имя файла БД (например, "durov.db")

    Returns:
        Ответ от Claude
    """
    # Путь к БД внутри Docker контейнера
    docker_db_path = f"{DB_ROOT_DOCKER}/{db_filename}"

    # Загружаем базовый промпт
    base_prompt = load_prompt("base.md")
    if not base_prompt:
        # Fallback если файл не найден
        base_prompt = """Ты — аналитик данных Telegram-чатов.
Используй базу данных SQLite для анализа.
Таблица messages содержит: id, timestamp, date_iso, message, sender_id, sender_username, sender_display_name, reply_to_msg_id, reactions_count, reactions_detail, views, forwards, permalink."""

    # Заменяем плейсхолдер пути к БД
    base_prompt = base_prompt.replace("{db_path}", docker_db_path)

    # Определяем skill на основе запроса
    skill_name = detect_skill(question)
    skill_prompt = ""

    if skill_name and skill_name in SKILLS:
        skill_file = SKILLS[skill_name]["file"]
        skill_prompt = load_prompt(skill_file)
        if skill_prompt:
            logger.info(f"Загружен skill: {skill_name}")

    # Формируем историю диалога
    history_section = ""
    if history:
        history_text = "\n\n".join([
            f"{'Пользователь' if msg['role'] == 'user' else 'Ассистент'}: {msg['content']}"
            for msg in history
        ])
        history_section = f"\n\n## История диалога\n\n{history_text}"

    # Собираем полный промпт
    full_prompt = base_prompt

    if skill_prompt:
        full_prompt += f"\n\n---\n\n{skill_prompt}"

    if history_section:
        full_prompt += history_section

    full_prompt += f"\n\n## Текущий запрос\n\n{question}"

    logger.info(f"Запрос к Claude (БД: {db_filename}, история: {len(history)})")

    # Выполняем Claude CLI в Docker (от пользователя node, не root)
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
        timeout=1200  # 20 минут
    )

    return result.stdout.strip()


# ============================================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================================

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start"""
    welcome_text = """
👋 Добро пожаловать в ChatGeist Multi-Chat Bot!

Этот бот анализирует историю Telegram-чатов с помощью AI.

📋 Команды:
  /chats — выбрать чат для анализа
  /current — показать текущий выбранный чат
  /help — справка

💡 Выберите чат через /chats, затем задавайте вопросы!
    """
    await message.answer(welcome_text)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help"""
    help_text = """
📖 Справка по боту

🔹 /chats — показать список доступных чатов
🔹 /current — какой чат сейчас выбран
🔹 /start — приветствие

💡 Как пользоваться:
1. Выберите чат через /chats
2. Задайте вопрос текстом
3. Для уточнения — ответьте на сообщение бота

📝 Примеры вопросов:
• Сколько всего сообщений?
• Кто самый активный участник?
• О чём говорили вчера?
• Найди сообщения про Python

🔒 Безопасность: все запросы обрабатываются в изолированном Docker-контейнере.
    """
    await message.answer(help_text)


@dp.message(Command("chats"))
async def cmd_chats(message: Message):
    """Команда /chats — показать список доступных БД"""
    databases = get_available_databases()

    if not databases:
        await message.answer(
            "❌ Нет доступных баз данных.\n\n"
            f"Убедитесь, что папка `{DB_ROOT_HOST}/` содержит .db файлы.\n"
            "Используйте `python update_manager.py` для загрузки чатов."
        )
        return

    builder = InlineKeyboardBuilder()
    for db in databases:
        label = f"{db['name']} ({db['size_mb']} MB)"
        builder.button(text=label, callback_data=f"select_db:{db['filename']}")

    builder.adjust(1)  # По 1 кнопке в ряд

    text = f"📂 Доступно баз данных: {len(databases)}\n\nВыберите чат для анализа:"
    await message.answer(text, reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("select_db:"))
async def on_db_select(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора БД"""
    selected_db = callback.data.split(":")[1]

    # Проверяем существование файла
    db_path = DB_ROOT_HOST / selected_db
    if not db_path.exists():
        await callback.answer("❌ База данных не найдена", show_alert=True)
        return

    # Сохраняем выбор в FSM state
    await state.update_data(current_db=selected_db)

    chat_name = selected_db.replace('.db', '')
    await callback.message.edit_text(
        f"✅ Выбран чат: {chat_name}\n\n"
        f"Теперь вы можете задавать вопросы об этом чате.\n"
        f"Для смены чата используйте /chats"
    )
    await callback.answer()


@dp.message(Command("current"))
async def cmd_current(message: Message, state: FSMContext):
    """Команда /current — показать текущий выбранный чат"""
    user_data = await state.get_data()
    current_db = user_data.get("current_db")

    if not current_db:
        await message.answer(
            "⚠️ Чат не выбран.\n\n"
            "Используйте /chats чтобы выбрать базу данных."
        )
        return

    chat_name = current_db.replace('.db', '')
    db_path = DB_ROOT_HOST / current_db

    if db_path.exists():
        size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)
        await message.answer(
            f"📊 Текущий чат: {chat_name}\n"
            f"   Размер БД: {size_mb} MB\n\n"
            f"Для смены используйте /chats"
        )
    else:
        await message.answer(f"⚠️ БД {current_db} не найдена. Выберите другой чат: /chats")
        await state.update_data(current_db=None)


@dp.message(F.text)
async def handle_query(message: Message, state: FSMContext):
    """Обработчик текстовых запросов"""
    user_query = message.text.strip()

    if not user_query:
        await message.answer("❌ Пожалуйста, отправьте непустой запрос.")
        return

    # Получаем текущий выбранный чат
    user_data = await state.get_data()
    current_db = user_data.get("current_db")

    if not current_db:
        await message.answer(
            "⚠️ Чат не выбран!\n\n"
            "Сначала выберите чат через /chats"
        )
        return

    # Проверяем существование БД
    db_path = DB_ROOT_HOST / current_db
    if not db_path.exists():
        await message.answer(f"❌ База данных {current_db} не найдена.\nВыберите другой чат: /chats")
        await state.update_data(current_db=None)
        return

    # Проверяем Docker контейнер
    if not check_docker_container():
        await message.answer(
            "❌ Docker контейнер не запущен.\n\n"
            "Запустите: `docker compose up -d`"
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
    if is_reply and history:
        status_msg = await message.answer(f"🔄 Анализирую [{chat_name}] с учётом контекста...")
    else:
        status_msg = await message.answer(f"🔄 Анализирую [{chat_name}], подождите...")

    try:
        # Запрос к Claude
        import asyncio
        report = await asyncio.to_thread(ask_claude_secure, user_query, history, current_db)

        # Отправляем ответ (PDF для длинных ответов > 2500 символов)
        logger.info(f"Длина ответа: {len(report)} символов")
        if len(report) <= 2500:
            await status_msg.edit_text(report)
        else:
            logger.info(f"Генерирую PDF (ответ {len(report)} > 2500)")
            # Генерируем PDF для длинных ответов
            pdf_buffer = generate_pdf(report, title=f"Отчёт: {chat_name}")

            # Превью (первые 500 символов)
            preview = report[:500] + "...\n\n📄 Полный ответ в PDF файле:"
            await status_msg.edit_text(preview)

            # Отправляем PDF
            pdf_file = BufferedInputFile(
                pdf_buffer.read(),
                filename=f"report_{chat_name}.pdf"
            )
            await message.answer_document(
                document=pdf_file,
                caption="📊 Полный отчёт"
            )

    except subprocess.TimeoutExpired:
        logger.error("Таймаут при обработке запроса")
        await status_msg.edit_text("❌ Превышено время ожидания (20 минут).\nПопробуйте упростить запрос.")

    except subprocess.CalledProcessError as e:
        error_output = e.stderr if e.stderr else e.stdout
        if not error_output:
            error_output = f"Exit code: {e.returncode}"
        logger.error(f"Ошибка Claude CLI: {error_output}")
        await status_msg.edit_text(f"❌ Ошибка API Claude:\n\n{error_output[:500]}")

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Ошибка:\n\n{str(e)[:500]}")


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
