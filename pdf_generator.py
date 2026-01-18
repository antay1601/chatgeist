"""
Генератор PDF для длинных ответов бота.
Использует fpdf2 с поддержкой Unicode/кириллицы и markdown.
"""

import os
import re
from io import BytesIO
from datetime import datetime
from fpdf import FPDF

# Путь к шрифтам DejaVu (поддерживают кириллицу)
FONT_PATHS = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ],
    "italic": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Oblique.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ],
}

# Цвета
COLORS = {
    "header1": (41, 98, 255),      # Синий для H1/H2
    "header2": (41, 98, 255),      # Синий для H3
    "text": (33, 33, 33),          # Тёмно-серый текст
    "gray": (128, 128, 128),       # Серый для дат
    "link": (0, 102, 204),         # Синий для ссылок
    "table_header": (240, 240, 240),  # Светло-серый фон заголовка таблицы
    "table_border": (200, 200, 200),  # Граница таблицы
}


def _find_font(font_type: str) -> str | None:
    """Ищет шрифт указанного типа в системе."""
    for path in FONT_PATHS.get(font_type, []):
        if os.path.exists(path):
            return path
    return None


class MarkdownPDF(FPDF):
    """PDF генератор с поддержкой markdown."""

    def __init__(self):
        super().__init__()
        self.font_name = "Helvetica"
        self._setup_fonts()

    def _setup_fonts(self):
        """Настраивает шрифты с поддержкой кириллицы."""
        regular = _find_font("regular")
        bold = _find_font("bold")
        italic = _find_font("italic")

        if regular:
            self.add_font("Unicode", "", regular)
            self.font_name = "Unicode"

            if bold:
                self.add_font("Unicode", "B", bold)
            else:
                self.add_font("Unicode", "B", regular)

            if italic:
                self.add_font("Unicode", "I", italic)
            else:
                self.add_font("Unicode", "I", regular)

    def _set_color(self, color_name: str):
        """Устанавливает цвет текста."""
        r, g, b = COLORS.get(color_name, COLORS["text"])
        self.set_text_color(r, g, b)

    def _set_fill_color(self, color_name: str):
        """Устанавливает цвет заливки."""
        r, g, b = COLORS.get(color_name, (255, 255, 255))
        self.set_fill_color(r, g, b)

    def _set_draw_color(self, color_name: str):
        """Устанавливает цвет линий."""
        r, g, b = COLORS.get(color_name, (0, 0, 0))
        self.set_draw_color(r, g, b)

    def render_header1(self, text: str):
        """Рендерит заголовок H1/H2."""
        self.ln(4)
        self.set_font(self.font_name, "B", 16)
        self._set_color("header1")
        self.multi_cell(0, 8, text.strip())
        self._set_color("text")
        self.ln(2)

    def render_header2(self, text: str):
        """Рендерит заголовок H3."""
        self.ln(3)
        self.set_font(self.font_name, "B", 12)
        self._set_color("header2")
        self.multi_cell(0, 6, text.strip())
        self._set_color("text")
        self.ln(1)

    def render_table(self, rows: list[list[str]]):
        """Рендерит таблицу."""
        if not rows:
            return

        self.set_font(self.font_name, "", 10)
        self._set_draw_color("table_border")

        # Рассчитываем ширину колонок
        page_width = self.w - 2 * self.l_margin
        col_count = len(rows[0])
        col_width = page_width / col_count

        for i, row in enumerate(rows):
            # Заголовок таблицы
            if i == 0:
                self._set_fill_color("table_header")
                self.set_font(self.font_name, "B", 10)
            else:
                self.set_font(self.font_name, "", 10)

            for j, cell in enumerate(row):
                fill = (i == 0)
                self.cell(col_width, 7, cell.strip(), border=1, fill=fill)
            self.ln()

        self.ln(2)

    def render_bullet(self, text: str, level: int = 0):
        """Рендерит пункт списка."""
        self.set_font(self.font_name, "", 11)
        self._set_color("text")

        indent = 5 + (level * 5)
        self.set_x(self.l_margin + indent)

        # Рендерим текст с поддержкой форматирования
        self._render_inline_text("• " + text.strip())
        self.ln(1)

    def render_numbered_item(self, number: str, text: str):
        """Рендерит нумерованный пункт."""
        self.set_font(self.font_name, "", 11)
        self._set_color("text")

        self.set_x(self.l_margin + 5)
        self._render_inline_text(f"{number}. {text.strip()}")
        self.ln(1)

    def render_paragraph(self, text: str):
        """Рендерит обычный параграф."""
        self.set_font(self.font_name, "", 11)
        self._set_color("text")
        self._render_inline_text(text.strip())
        self.ln(2)

    def _render_inline_text(self, text: str):
        """Рендерит текст с inline форматированием (bold, italic, links)."""
        # Паттерны для форматирования
        # Ссылки: [text](url)
        # Bold: **text** или __text__
        # Italic: *text* или _text_

        parts = self._parse_inline(text)

        for part_type, content in parts:
            if part_type == "bold":
                self.set_font(self.font_name, "B", 11)
                self.write(5, content)
                self.set_font(self.font_name, "", 11)
            elif part_type == "italic":
                self.set_font(self.font_name, "I", 11)
                self.write(5, content)
                self.set_font(self.font_name, "", 11)
            elif part_type == "link":
                link_text, url = content
                self._set_color("link")
                self.set_font(self.font_name, "", 11)
                self.write(5, link_text, url)
                self._set_color("text")
            else:
                self.write(5, content)

        self.ln()

    def _parse_inline(self, text: str) -> list[tuple[str, any]]:
        """Парсит inline форматирование."""
        parts = []
        pos = 0

        # Регулярки для поиска форматирования
        patterns = [
            (r'\[([^\]]+)\]\(([^)]+)\)', 'link'),       # [text](url)
            (r'\*\*([^*]+)\*\*', 'bold'),              # **bold**
            (r'__([^_]+)__', 'bold'),                   # __bold__
            (r'(?<!\*)\*([^*]+)\*(?!\*)', 'italic'),   # *italic*
            (r'(?<!_)_([^_]+)_(?!_)', 'italic'),       # _italic_
        ]

        combined_pattern = '|'.join(f'({p[0]})' for p in patterns)

        for match in re.finditer(combined_pattern, text):
            # Добавляем текст до матча
            if match.start() > pos:
                parts.append(("text", text[pos:match.start()]))

            # Определяем тип форматирования
            full_match = match.group(0)

            if full_match.startswith('[') and '](' in full_match:
                # Ссылка
                link_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', full_match)
                if link_match:
                    parts.append(("link", (link_match.group(1), link_match.group(2))))
            elif full_match.startswith('**') or full_match.startswith('__'):
                # Bold
                content = full_match[2:-2]
                parts.append(("bold", content))
            elif full_match.startswith('*') or full_match.startswith('_'):
                # Italic
                content = full_match[1:-1]
                parts.append(("italic", content))

            pos = match.end()

        # Добавляем оставшийся текст
        if pos < len(text):
            parts.append(("text", text[pos:]))

        if not parts:
            parts.append(("text", text))

        return parts

    def render_separator(self):
        """Рендерит горизонтальную линию."""
        self.ln(3)
        self._set_draw_color("table_border")
        y = self.get_y()
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(5)


def parse_markdown(text: str) -> list[dict]:
    """Парсит markdown текст в структурированные блоки."""
    blocks = []
    lines = text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Пропускаем пустые строки
        if not stripped:
            i += 1
            continue

        # Заголовки
        if stripped.startswith('## '):
            blocks.append({"type": "h1", "content": stripped[3:]})
            i += 1
            continue

        if stripped.startswith('### '):
            blocks.append({"type": "h2", "content": stripped[4:]})
            i += 1
            continue

        if stripped.startswith('# '):
            blocks.append({"type": "h1", "content": stripped[2:]})
            i += 1
            continue

        # Разделитель
        if stripped in ['---', '***', '___']:
            blocks.append({"type": "separator"})
            i += 1
            continue

        # Таблица
        if '|' in stripped and stripped.startswith('|'):
            table_rows = []
            while i < len(lines) and '|' in lines[i]:
                row_line = lines[i].strip()
                # Пропускаем разделитель таблицы (|---|---|)
                if re.match(r'\|[-:\s|]+\|', row_line):
                    i += 1
                    continue
                # Парсим ячейки
                cells = [c.strip() for c in row_line.split('|')[1:-1]]
                if cells:
                    table_rows.append(cells)
                i += 1
            if table_rows:
                blocks.append({"type": "table", "content": table_rows})
            continue

        # Списки с буллетами
        if stripped.startswith('• ') or stripped.startswith('- ') or stripped.startswith('* '):
            prefix_len = 2
            content = stripped[prefix_len:]
            blocks.append({"type": "bullet", "content": content})
            i += 1
            continue

        # Нумерованные списки
        num_match = re.match(r'^(\d+)\.\s+(.+)$', stripped)
        if num_match:
            blocks.append({
                "type": "numbered",
                "number": num_match.group(1),
                "content": num_match.group(2)
            })
            i += 1
            continue

        # Обычный текст (собираем параграф)
        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            next_line = lines[i].strip()
            # Прерываем на пустой строке или специальных элементах
            if not next_line or next_line.startswith('#') or next_line.startswith('|') or next_line in ['---', '***', '___']:
                break
            if next_line.startswith('• ') or next_line.startswith('- ') or next_line.startswith('* '):
                break
            if re.match(r'^\d+\.\s+', next_line):
                break
            paragraph_lines.append(next_line)
            i += 1

        blocks.append({"type": "paragraph", "content": ' '.join(paragraph_lines)})

    return blocks


def generate_pdf(text: str, title: str = "Отчёт") -> BytesIO:
    """
    Генерирует PDF документ из markdown текста.

    Args:
        text: Markdown текст для конвертации в PDF
        title: Заголовок документа

    Returns:
        BytesIO буфер с PDF данными
    """
    pdf = MarkdownPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Заголовок документа
    pdf.set_font(pdf.font_name, "B", 18)
    pdf._set_color("header1")
    pdf.cell(0, 12, title, ln=True, align="C")
    pdf.ln(3)

    # Дата генерации
    pdf.set_font(pdf.font_name, "", 9)
    pdf._set_color("gray")
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    pdf.cell(0, 5, f"Generated: {date_str}", ln=True, align="C")
    pdf.ln(8)

    # Парсим и рендерим markdown
    blocks = parse_markdown(text)

    for block in blocks:
        block_type = block["type"]

        if block_type == "h1":
            pdf.render_header1(block["content"])
        elif block_type == "h2":
            pdf.render_header2(block["content"])
        elif block_type == "table":
            pdf.render_table(block["content"])
        elif block_type == "bullet":
            pdf.render_bullet(block["content"])
        elif block_type == "numbered":
            pdf.render_numbered_item(block["number"], block["content"])
        elif block_type == "separator":
            pdf.render_separator()
        elif block_type == "paragraph":
            pdf.render_paragraph(block["content"])

    # Сохраняем в буфер
    buffer = BytesIO()
    pdf.output(buffer)
    buffer.seek(0)

    return buffer
