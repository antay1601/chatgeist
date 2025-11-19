# --- text_to_sql_final_v29_new_schema.py ---

import os
import json
from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import create_engine
import vanna
from vanna.openai import OpenAI_Chat
from vanna.chromadb import ChromaDB_VectorStore
import pandas as pd

# --- 0. НАСТРОЙКА ОКРУЖЕНИЯ ---

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
print("⚠️ Токен OpenAI не найден."); exit()
print("✅ Токен OpenAI успешно загружен.")
client = OpenAI(api_key=OPENAI_API_KEY)

# --- 1. НАСТРОЙКА VANNA ---

class MyVanna(ChromaDB_VectorStore, OpenAI_Chat):
def **init**(self, config=None):
ChromaDB_VectorStore.**init**(self, config={'path': './vanna_telegram_db'}) # Новая папка для знаний
OpenAI_Chat.**init**(self, config={'api_key': OPENAI_API_KEY, 'model': 'gpt-4-turbo-preview'}) # Используем GPT-4 для лучшего понимания сложной схемы

vn = MyVanna()

# --- 2. ПОДКЛЮЧЕНИЕ И ОБУЧЕНИЕ VANNA НА НОВОЙ СХЕМЕ ---

DB_NAME = 'telegram_messages.db' # Имя вашей новой базы данных
vn.connect_to_sqlite(DB_NAME)

# Переобучаем Vanna, если база знаний пуста

if vn.get_training_data().empty:
print(f"⏳ Обучаем Vanna на новой схеме из {DB_NAME}...")

    # Извлекаем информацию обо ВСЕХ таблицах в базе
    df_schema = vn.run_sql("SELECT sql FROM sqlite_master WHERE type='table'")
    for ddl in df_schema['sql']:
        vn.train(ddl=ddl)
    print("👍 Схемы таблиц (DDL) добавлены в базу знаний.")

    # Добавляем документацию, объясняющую ключевые поля
    vn.train(documentation="Поле 'message' в таблице `messages` содержит текст сообщения.")
    vn.train(documentation="Поле 'sender_display_name' в таблице `messages` содержит имя автора сообщения.")
    vn.train(documentation="Поле 'timestamp' в таблице `messages` - это Unix timestamp, который можно использовать для фильтрации по дате.")
    print("👍 Документация добавлена.")

    # Добавляем ПРИМЕРЫ СЛОЖНЫХ ЗАПРОСОВ, чтобы показать Vanna, на что способна новая схема
    print("Добавляем примеры 'вопрос-SQL'...")
    vn.train(
        question="Найди сообщения про кальян за последний месяц",
        sql="SELECT sender_display_name, message, date_iso FROM messages WHERE message LIKE '%кальян%' AND timestamp >= strftime('%s', 'now', '-1 month')"
    )
    vn.train(
        question="Кто самый активный пользователь?",
        sql="SELECT sender_display_name, COUNT(*) as msg_count FROM messages WHERE NOT is_service GROUP BY sender_id ORDER BY msg_count DESC LIMIT 10"
    )
    vn.train(
        question="Покажи самые популярные сообщения (по реакциям)",
        sql="SELECT message, reactions_count, permalink FROM messages WHERE reactions_count > 0 ORDER BY reactions_count DESC LIMIT 5"
    )
    vn.train(
        question="Какие эмодзи самые популярные?",
        sql="SELECT reaction, COUNT(*) as usage_count FROM reactions GROUP BY reaction ORDER BY usage_count DESC LIMIT 10"
    )

    print("✅ Обучение Vanna на новой схеме завершено.")

else:
print("✅ Vanna уже обучена на новой схеме.")

# --- 3. "СУПЕР-ПРОМПТ" ДЛЯ АНАЛИТИКА (можно оставить без изменений) ---

summarizer_prompt_template = """
Ты — ИИ-аналитик высочайшего уровня... (полный текст вашего промпта)
"""

# --- 4. ОСНОВНОЙ ЦИКЛ ПРИЛОЖЕНИЯ ---

if **name** == "**main**":
print(f"\n💬 Vanna.AI Analyst (схема: {DB_NAME}) готова к работе. Введите ваш запрос:")
while True:
user_input = input("\nВаш запрос: ")
if user_input.lower() in ["выход", "exit", "quit"]: break

        # --- ЭТАП 1: ПОИСК ДАННЫХ С ПОМОЩЬЮ VANNA ---
        df_result = None
        try:
            print("\n⏳ Этап 1: Vanna ищет релевантные данные...")
            # Теперь мы можем доверять Vanna и на более сложных запросах
            df_result = vn.ask(user_input, print_results=False)

            if df_result is not None and not df_result.empty:
                print(f"✅ Найдено {len(df_result)} релевантных записей.")
            else:
                print("🔹 Vanna ничего не нашла в базе данных.")

        except Exception as e:
            print(f"💥 Ошибка на этапе 1 (Vanna): {e}"); continue

        if df_result is None or df_result.empty:
            continue

        # --- ЭТАП 2: ГЛУБОКИЙ АНАЛИЗ И СИНТЕЗ ---
        try:
            print("\n⏳ Этап 2: Главный аналитик готовит отчет...")
            raw_data_text = df_result.to_markdown(index=False)
            MAX_CHARS = 15000
            if len(raw_data_text) > MAX_CHARS:
                raw_data_text = raw_data_text[:MAX_CHARS] + "\n\n... (данные обрезаны)..."

            prompt = summarizer_prompt_template.format(user_input=user_input, raw_data=raw_data_text)

            response = client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4
            )
            final_summary = response.choices[0].message.content

            print("\n" + "="*20 + " ИТОГОВЫЙ АНАЛИТИЧЕСКИЙ ОТЧЕТ " + "="*20)
            print(final_summary)

        except Exception as e:
            print(f"💥 Ошибка на этапе 2 (Анализ): {e}")
