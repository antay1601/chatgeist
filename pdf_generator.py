"""
Генератор PDF для длинных ответов бота.
Использует fpdf2 с поддержкой Unicode/кириллицы.
"""

import os
from io import BytesIO
from datetime import datetime
from pathlib import Path
from fpdf import FPDF

# Путь к шрифту DejaVu (поддерживает кириллицу)
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Ubuntu/Debian
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",  # Fedora/RHEL
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",  # macOS
    "/Library/Fonts/Arial Unicode.ttf",  # macOS alt
]


def _find_unicode_font() -> str | None:
    """Ищет Unicode шрифт в системе."""
    for path in FONT_PATHS:
        if os.path.exists(path):
            return path
    return None


def generate_pdf(text: str, title: str = "Отчёт") -> BytesIO:
    """
    Генерирует PDF документ из текста.

    Args:
        text: Текст для конвертации в PDF
        title: Заголовок документа

    Returns:
        BytesIO буфер с PDF данными
    """
    pdf = FPDF()
    pdf.add_page()

    # Пытаемся найти Unicode шрифт
    font_path = _find_unicode_font()
    if font_path:
        pdf.add_font("Unicode", "", font_path)
        pdf.add_font("Unicode", "B", font_path)  # Bold как обычный
        font_name = "Unicode"
    else:
        # Fallback: заменяем кириллицу на транслит
        font_name = "Helvetica"

    # Заголовок
    pdf.set_font(font_name, "B", 16)
    pdf.cell(0, 10, title, ln=True, align="C")
    pdf.ln(5)

    # Дата генерации
    pdf.set_font(font_name, "", 10)
    pdf.set_text_color(128, 128, 128)
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    pdf.cell(0, 6, f"Generated: {date_str}", ln=True, align="C")
    pdf.ln(10)

    # Основной текст
    pdf.set_font(font_name, "", 11)
    pdf.set_text_color(0, 0, 0)

    # multi_cell автоматически переносит текст
    pdf.multi_cell(0, 6, text)

    # Сохраняем в буфер
    buffer = BytesIO()
    pdf.output(buffer)
    buffer.seek(0)

    return buffer
