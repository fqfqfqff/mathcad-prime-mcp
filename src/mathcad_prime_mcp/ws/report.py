"""Markdown -> лабораторный отчёт .docx с оформлением по ГОСТ.

Пишется обычный markdown, на выходе Times New Roman 14, полуторный интервал,
поля 30/15/20/20 мм, титульный лист из front matter, картинки по ширине
страницы с подписью и автоматический масштаб.

Разметка сверх обычного markdown:

    ---                       front matter титульного листа
    work: 4
    title: Фильтрация сигналов
    ---
    # 1. Цель работы          заголовок раздела (по центру, жирный)
    ## 2.1 Подраздел          подзаголовок (слева, жирный)
    $$C := fft(V)$$           формула отдельной строкой по центру
    ![Рисунок 1 - Документ](fig.png)   картинка с подписью
    | a | b |                 таблица
    \\newpage                  разрыв страницы

Внутри текста: **жирный**, X_{i} подстрочный, 10^{5} надстрочный.
"""

from __future__ import annotations

import os
import re

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Cm, Pt

FONT = "Times New Roman"
SIZE = Pt(14)
SIZE_TITLE = Pt(16)
SIZE_TABLE = Pt(12)
TEXT_WIDTH_CM = 16.0        # ширина полосы набора при полях 30 и 15 мм

FRONT_DEFAULTS = {
    "ministry": "МИНИСТЕРСТВО ОБРАЗОВАНИЯ И НАУКИ РОССИЙСКОЙ ФЕДЕРАЦИИ",
    "institution": ["ФЕДЕРАЛЬНОЕ ГОСУДАРСТВЕННОЕ БЮДЖЕТНОЕ ОБРАЗОВАТЕЛЬНОЕ",
                    "УЧРЕЖДЕНИЕ ВЫСШЕГО ОБРАЗОВАНИЯ"],
    "university": "МОСКОВСКИЙ ПОЛИТЕХНИЧЕСКИЙ УНИВЕРСИТЕТ (ФГБОУ ВПО МПУ)",
    "subject": "",
    "work": "",
    "title": "",
    "group": "",
    "student": "",
    "teacher": "",
    "date": "",
    "year": "",
}


# --------------------------------------------------------------------------
# инлайновая разметка
# --------------------------------------------------------------------------
_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|[_^]\{[^}]*\})")


def add_runs(par, text: str, size=SIZE, bold=False, italic=False):
    for piece in _INLINE.split(text):
        if not piece:
            continue
        b, i, sub, sup = bold, italic, False, False
        body = piece
        if piece.startswith("**") and piece.endswith("**"):
            body, b = piece[2:-2], True
        elif piece.startswith("`") and piece.endswith("`"):
            body = piece[1:-1]
        elif piece[:2] in ("_{", "^{") and piece.endswith("}"):
            body = piece[2:-1]
            sub, sup = piece[0] == "_", piece[0] == "^"
        run = par.add_run(body)
        run.font.name = FONT
        run.font.size = size
        run.bold = b
        run.italic = i
        run.font.subscript = sub
        run.font.superscript = sup
    return par


# --------------------------------------------------------------------------
# абзацы
# --------------------------------------------------------------------------
def _par(doc, align=WD_ALIGN_PARAGRAPH.JUSTIFY, before=0, after=6, spacing=1.5):
    p = doc.add_paragraph()
    p.alignment = align
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = spacing
    return p


def heading(doc, text):
    add_runs(_par(doc, WD_ALIGN_PARAGRAPH.CENTER, before=12, after=8), text, bold=True)


def subheading(doc, text):
    add_runs(_par(doc, WD_ALIGN_PARAGRAPH.LEFT, before=8, after=5), text, bold=True)


def body(doc, text):
    add_runs(_par(doc), text)


def formula(doc, text):
    add_runs(_par(doc, WD_ALIGN_PARAGRAPH.CENTER, before=4, after=7, spacing=1.0), text)


def caption(doc, text):
    add_runs(_par(doc, WD_ALIGN_PARAGRAPH.CENTER, before=4, after=10, spacing=1.0), text)


def bullet(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.line_spacing = 1.5
    p.paragraph_format.space_after = Pt(3)
    add_runs(p, text)


def picture(doc, path, base_dir, max_cm=TEXT_WIDTH_CM):
    full = path if os.path.isabs(path) else os.path.join(base_dir, path)
    width = Cm(max_cm)
    try:
        from PIL import Image
        with Image.open(full) as im:
            w_px, h_px = im.size
        # вписываем по ширине, но не выше половины страницы
        cm_w = min(max_cm, w_px / 37.8)
        cm_h = cm_w * h_px / w_px
        if cm_h > 12.0:
            cm_w *= 12.0 / cm_h
        width = Cm(cm_w)
    except Exception:
        pass
    p = _par(doc, WD_ALIGN_PARAGRAPH.CENTER, before=6, after=2, spacing=1.0)
    p.add_run().add_picture(full, width=width)


def table(doc, rows):
    head, body_rows = rows[0], rows[1:]
    t = doc.add_table(rows=len(rows), cols=len(head))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for j, cell in enumerate(head):
        par = t.rows[0].cells[j].paragraphs[0]
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_runs(par, cell, size=SIZE_TABLE, bold=True)
    for i, row in enumerate(body_rows, start=1):
        for j, cell in enumerate(row):
            if j >= len(head):
                continue
            par = t.rows[i].cells[j].paragraphs[0]
            add_runs(par, cell, size=SIZE_TABLE)
    # иначе следующий абзац прилипает к нижней линии таблицы
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(0)
    spacer.paragraph_format.line_spacing = 0.6


# --------------------------------------------------------------------------
# титульный лист
# --------------------------------------------------------------------------
def title_page(doc, fm):
    def line(text, align=WD_ALIGN_PARAGRAPH.RIGHT, size=SIZE, bold=False):
        add_runs(_par(doc, align, after=0, spacing=1.0), text, size=size, bold=bold)

    line(fm["ministry"])
    for s in fm["institution"]:
        line(s)
    line(fm["university"], WD_ALIGN_PARAGRAPH.CENTER)

    for _ in range(9):
        line(" ", WD_ALIGN_PARAGRAPH.CENTER)
    if fm.get("work"):
        line("Лабораторная работа № %s" % fm["work"], WD_ALIGN_PARAGRAPH.CENTER, SIZE_TITLE)
    if fm.get("title"):
        line("«%s»" % fm["title"], WD_ALIGN_PARAGRAPH.CENTER, SIZE_TITLE)
    if fm.get("subject"):
        line("По дисциплине «%s»" % fm["subject"], WD_ALIGN_PARAGRAPH.CENTER)

    for _ in range(6):
        line(" ", WD_ALIGN_PARAGRAPH.CENTER)
    from docx.enum.text import WD_TAB_ALIGNMENT
    for label, key in (("Группа", "group"), ("Студент", "student"),
                       ("Дата", "date"), ("Преподаватель", "teacher")):
        if not fm.get(key):
            continue
        p = _par(doc, WD_ALIGN_PARAGRAPH.LEFT, after=2, spacing=1.0)
        p.paragraph_format.tab_stops.add_tab_stop(Cm(3.5), WD_TAB_ALIGNMENT.LEFT)
        add_runs(p, "%s\t%s" % (label, fm[key]))

    for _ in range(2):
        line(" ", WD_ALIGN_PARAGRAPH.CENTER)
    if fm.get("year"):
        line("%s г." % fm["year"], WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


# --------------------------------------------------------------------------
# разбор исходника
# --------------------------------------------------------------------------
def parse_front_matter(text: str) -> tuple[dict, str]:
    fm = dict(FRONT_DEFAULTS)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fm, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return fm, text
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "institution":
            fm[key] = [v.strip() for v in value.split("|")]
        else:
            fm[key] = value
    return fm, "\n".join(lines[end + 1:])


IMG_RE = re.compile(r"^!\[(.*?)\]\((.+?)\)\s*$")
PLACEHOLDER = re.compile(r"\{\{\s*([^}:\s]+)\s*(?::\s*(\d+)\s*)?\}\}")


def ru(x, decimals=None) -> str:
    """Число в русской записи: запятая вместо точки."""
    if isinstance(x, str):
        return x
    if decimals is not None:
        s = "%.*f" % (decimals, x)
    elif isinstance(x, int) or float(x).is_integer():
        s = "%d" % round(x)
    else:
        s = "%.6g" % x
    return s.replace(".", ",")


def substitute(text: str, values: dict) -> tuple[str, list]:
    """{{sk}} и {{sk:1}} -> значения из рассчитанного листа."""
    missing = []

    def repl(m):
        name, dec = m.group(1), m.group(2)
        if name not in values:
            missing.append(name)
            return m.group(0)
        return ru(values[name], int(dec) if dec else None)

    return PLACEHOLDER.sub(repl, text), missing


def render(source: str, out_docx: str, base_dir: str = "", values: dict | None = None) -> str:
    if values:
        source, missing = substitute(source, values)
        if missing:
            raise KeyError("в листе нет значений для: %s. Подставляются только "
                           "переменные, которые выведены в листе через 'имя='"
                           % ", ".join(sorted(set(missing))))
    fm, text = parse_front_matter(source)
    base_dir = base_dir or os.path.dirname(os.path.abspath(out_docx))

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = FONT
    style.font.size = SIZE
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin, sec.right_margin = Cm(3.0), Cm(1.5)
    sec.top_margin, sec.bottom_margin = Cm(2.0), Cm(2.0)

    if any(fm.get(k) for k in ("work", "title", "student")):
        title_page(doc, fm)

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        i += 1

        if not s:
            continue
        if s in (r"\newpage", r"\pagebreak"):
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            continue
        if s.startswith("## "):
            subheading(doc, s[3:].strip())
            continue
        if s.startswith("# "):
            heading(doc, s[2:].strip())
            continue
        if s.startswith("$$") and s.endswith("$$") and len(s) > 4:
            formula(doc, s[2:-2].strip())
            continue
        m = IMG_RE.match(s)
        if m:
            picture(doc, m.group(2).strip(), base_dir)
            if m.group(1).strip():
                caption(doc, m.group(1).strip())
            continue
        if s.startswith("|") and s.endswith("|"):
            rows = []
            j = i - 1
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                j += 1
            table(doc, rows)
            i = j
            continue
        if s.startswith("- ") or s.startswith("* "):
            bullet(doc, s[2:].strip())
            continue
        body(doc, s)

    os.makedirs(os.path.dirname(os.path.abspath(out_docx)) or ".", exist_ok=True)
    doc.save(out_docx)
    return os.path.abspath(out_docx)


def to_pdf(docx_path: str, pdf_path: str = "") -> str:
    """Конвертация через установленный Word."""
    import pythoncom
    import win32com.client

    pdf_path = pdf_path or os.path.splitext(docx_path)[0] + ".pdf"
    pythoncom.CoInitialize()
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    doc = word.Documents.Open(os.path.abspath(docx_path))
    try:
        doc.SaveAs(os.path.abspath(pdf_path), FileFormat=17)
    finally:
        doc.Close(False)
        word.Quit()
    return os.path.abspath(pdf_path)
