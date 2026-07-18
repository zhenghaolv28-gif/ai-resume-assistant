"""招聘网站风格的中文简历模板，以及 Word/PDF 导出功能。"""

from __future__ import annotations

from io import BytesIO
import html
from pathlib import Path
import re
import unicodedata

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image as PdfImage,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


CHINESE_FONT = "STSong-Light"
EMBEDDED_CHINESE_FONT = "ResumeChinese"
FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)
SECTION_LABELS = {
    "职业概述": "个人简介",
    "个人简介": "个人简介",
    "自我介绍": "个人简介",
    "教育经历": "教育背景",
    "教育背景": "教育背景",
    "工作或实习经历": "工作经历",
    "工作经历": "工作经历",
    "项目经历": "项目经历",
    "技能与证书": "技能证书",
    "技能证书": "技能证书",
}
RAW_SECTIONS = (
    ("个人简介", "summary"),
    ("教育背景", "education"),
    ("工作经历", "work_experience"),
    ("项目经历", "project_experience"),
    ("技能证书", "skills"),
)
ALLOWED_SYMBOLS = set("+-@./\\%&")
MARKDOWN_SYMBOLS = set("*_`~#")


def _clean_line(text: str, preserve_bullet: bool = True) -> str:
    """清理 Markdown、表情和不可见字符，保留中文及常用简历标点。"""
    line = str(text).replace("\uFEFF", "").replace("\uFFFD", "")
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"^\s*#{1,6}\s*", "", line)
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = re.sub(r"__(.*?)__", r"\1", line)
    is_bullet = bool(re.match(r"^\s*[-+*•·]\s+", line))
    line = re.sub(r"^\s*[-+*•·]\s+", "", line)

    cleaned: list[str] = []
    for character in line:
        if character in MARKDOWN_SYMBOLS:
            continue
        if character in "\r\n\t":
            cleaned.append(" ")
            continue
        category = unicodedata.category(character)
        if category.startswith("C") or category == "So":
            continue
        if character == "•":
            character = "·"
        if character.isalnum() or category.startswith("L") or category.startswith("N"):
            cleaned.append(character)
        elif category.startswith("P") or character in ALLOWED_SYMBOLS or character.isspace():
            cleaned.append(character)

    result = re.sub(r"[ \t]+", " ", "".join(cleaned)).strip()
    if preserve_bullet and is_bullet and result:
        result = f"· {result}"
    return result


def clean_resume_text(text: str | None) -> str:
    """清理 AI 结果，确保编辑框和导出文件不出现 Markdown 星号或乱码。"""
    if not text:
        return ""
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_clean_line(line) for line in normalized.split("\n")).strip()


def _section_label(text: str) -> str:
    cleaned = _clean_line(text, preserve_bullet=False).rstrip("：:")
    return SECTION_LABELS.get(cleaned, cleaned)


def _content_blocks(resume_data: dict, optimized_text: str | None) -> list[tuple[str, str]]:
    """将 AI 文本或原始资料整理为招聘简历常见的标题、正文和项目符号。"""
    blocks: list[tuple[str, str]] = []
    if optimized_text and optimized_text.strip():
        for original_line in str(optimized_text).splitlines():
            raw_line = original_line.strip()
            if not raw_line:
                continue
            is_heading = bool(re.match(r"^\s*#{1,6}\s*", raw_line))
            is_bullet = bool(re.match(r"^\s*[-+*•·]\s+", raw_line))
            line = _clean_line(raw_line, preserve_bullet=False)
            if not line:
                continue
            if is_heading or line in SECTION_LABELS:
                blocks.append(("heading", _section_label(line)))
            elif is_bullet:
                blocks.append(("bullet", line))
            else:
                blocks.append(("paragraph", line))
        return blocks

    for title, key in RAW_SECTIONS:
        content = str(resume_data.get(key, "")).strip()
        if not content:
            continue
        blocks.append(("heading", title))
        for original_line in content.splitlines():
            raw_line = original_line.strip()
            if not raw_line:
                continue
            is_bullet = bool(re.match(r"^\s*[-+*•·]\s+", raw_line))
            line = _clean_line(raw_line, preserve_bullet=False)
            if line:
                blocks.append(("bullet" if is_bullet else "paragraph", line))
    return blocks


def _plain(value: object) -> str:
    return clean_resume_text(str(value or "")).replace("\n", " ").strip()


def _contact_line(resume_data: dict) -> str:
    items = [
        _plain(resume_data.get("phone")),
        _plain(resume_data.get("email")),
        _plain(resume_data.get("city")),
    ]
    return " ｜ ".join(item for item in items if item)


def _set_word_font(run, size: float = 10.5, bold: bool = False, color: str = "202938") -> None:
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    run.bold = bold


def _word_text(paragraph, text: str, size: float = 10.5, bold: bool = False, color: str = "202938") -> None:
    _set_word_font(paragraph.add_run(text), size=size, bold=bold, color=color)


def _shade_word_cell(cell, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = properties.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        properties.append(shading)
    shading.set(qn("w:fill"), fill)


def _remove_word_table_borders(table) -> None:
    properties = table._tbl.tblPr
    borders = properties.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        properties.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "nil")


def _set_word_heading_style(paragraph) -> None:
    paragraph.paragraph_format.space_before = Pt(10)
    paragraph.paragraph_format.space_after = Pt(5)
    paragraph.paragraph_format.keep_with_next = True
    properties = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:space"), "8")
    left.set(qn("w:color"), "1F4E79")
    borders.append(left)
    properties.append(borders)


def create_resume_document(
    resume_data: dict,
    optimized_text: str | None = None,
    photo_bytes: bytes | None = None,
) -> bytes:
    """生成招聘网站风格的完整 Word 简历。"""
    document = Document()
    section = document.sections[0]
    section.top_margin = Cm(1.35)
    section.bottom_margin = Cm(1.35)
    section.left_margin = Cm(1.65)
    section.right_margin = Cm(1.65)
    normal = document.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    normal.font.size = Pt(10.5)

    accent = document.add_table(rows=1, cols=1)
    accent.autofit = False
    accent.columns[0].width = Cm(17.0)
    _remove_word_table_borders(accent)
    _shade_word_cell(accent.cell(0, 0), "1F4E79")
    accent.cell(0, 0).paragraphs[0].paragraph_format.space_after = Pt(0)
    _word_text(accent.cell(0, 0).paragraphs[0], " ", 2)

    header = document.add_table(rows=1, cols=2)
    header.autofit = False
    header.columns[0].width = Cm(13.7)
    header.columns[1].width = Cm(3.3)
    _remove_word_table_borders(header)
    left_cell, right_cell = header.rows[0].cells
    _shade_word_cell(left_cell, "F4F7FB")
    _shade_word_cell(right_cell, "F4F7FB")
    left_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    right_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

    name_paragraph = left_cell.paragraphs[0]
    _word_text(name_paragraph, _plain(resume_data.get("name")) or "个人简历", 22, True, "172B4D")
    name_paragraph.paragraph_format.space_after = Pt(4)
    role_paragraph = left_cell.add_paragraph()
    _word_text(role_paragraph, f"目标岗位：{_plain(resume_data.get('target_role'))}", 11, True, "1F4E79")
    role_paragraph.paragraph_format.space_after = Pt(3)
    contact = _contact_line(resume_data)
    if contact:
        contact_paragraph = left_cell.add_paragraph()
        _word_text(contact_paragraph, contact, 9.5, False, "46566B")

    if photo_bytes:
        photo_paragraph = right_cell.paragraphs[0]
        photo_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        photo_paragraph.add_run().add_picture(BytesIO(photo_bytes), width=Cm(2.8))

    document.add_paragraph().paragraph_format.space_after = Pt(0)
    for block_type, text in _content_blocks(resume_data, optimized_text):
        if block_type == "heading":
            paragraph = document.add_paragraph()
            _set_word_heading_style(paragraph)
            _word_text(paragraph, text, 13, True, "1F4E79")
        elif block_type == "bullet":
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Cm(0.35)
            paragraph.paragraph_format.first_line_indent = Cm(-0.25)
            paragraph.paragraph_format.space_after = Pt(2)
            _word_text(paragraph, f"· {text}")
        else:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.line_spacing = 1.15
            paragraph.paragraph_format.space_after = Pt(3)
            _word_text(paragraph, text)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _word_text(footer, "个人简历", 8, False, "64748B")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _register_pdf_font() -> str:
    try:
        pdfmetrics.getFont(EMBEDDED_CHINESE_FONT)
        return EMBEDDED_CHINESE_FONT
    except KeyError:
        pass

    for font_path in FONT_CANDIDATES:
        if not font_path.is_file():
            continue
        try:
            pdfmetrics.registerFont(
                TTFont(EMBEDDED_CHINESE_FONT, str(font_path), subfontIndex=0)
            )
            return EMBEDDED_CHINESE_FONT
        except Exception:
            continue

    try:
        pdfmetrics.getFont(CHINESE_FONT)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(CHINESE_FONT))
    return CHINESE_FONT


def _pdf_photo(photo_bytes: bytes) -> PdfImage:
    photo_buffer = BytesIO(photo_bytes)
    image_reader = ImageReader(photo_buffer)
    pixel_width, pixel_height = image_reader.getSize()
    max_width, max_height = 28 * mm, 38 * mm
    scale = min(max_width / pixel_width, max_height / pixel_height)
    photo_buffer.seek(0)
    return PdfImage(photo_buffer, width=pixel_width * scale, height=pixel_height * scale)


class SectionIcon(Flowable):
    """用矢量线条绘制章节图标，避免依赖可能缺字的图标字体。"""

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.width = 11 * mm
        self.height = 11 * mm

    def wrap(self, available_width: float, available_height: float):
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        center = self.width / 2
        radius = 4.6 * mm
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#E2F3EF"))
        canvas.setStrokeColor(colors.HexColor("#5BAE9E"))
        canvas.setLineWidth(0.7)
        canvas.circle(center, self.height / 2, radius, fill=1, stroke=1)
        canvas.setStrokeColor(colors.HexColor("#327C70"))
        canvas.setFillColor(colors.HexColor("#327C70"))
        canvas.setLineWidth(1.05)

        if self.kind == "education":
            canvas.line(center - 3.4 * mm, center + 0.8 * mm, center, center + 2.6 * mm)
            canvas.line(center, center + 2.6 * mm, center + 3.4 * mm, center + 0.8 * mm)
            canvas.line(center - 2.5 * mm, center + 0.1 * mm, center + 2.5 * mm, center + 0.1 * mm)
            canvas.line(center - 2.1 * mm, center - 0.3 * mm, center - 2.1 * mm, center - 2.1 * mm)
            canvas.line(center + 2.1 * mm, center - 0.3 * mm, center + 2.1 * mm, center - 2.1 * mm)
        elif self.kind == "work":
            canvas.roundRect(center - 3.1 * mm, center - 2.2 * mm, 6.2 * mm, 4.2 * mm, 0.7 * mm, fill=0, stroke=1)
            canvas.arc(center - 1.6 * mm, center + 0.8 * mm, center + 1.6 * mm, center + 3.2 * mm, 0, 180)
            canvas.line(center - 3.1 * mm, center - 0.2 * mm, center + 3.1 * mm, center - 0.2 * mm)
            canvas.line(center, center + 0.4 * mm, center, center - 0.8 * mm)
        elif self.kind == "project":
            points = [
                (center - 2.8 * mm, center + 2 * mm),
                (center + 2.8 * mm, center + 1.1 * mm),
                (center - 1.6 * mm, center - 2.2 * mm),
            ]
            canvas.line(*points[0], *points[1])
            canvas.line(*points[1], *points[2])
            canvas.line(*points[2], *points[0])
            for x, y in points:
                canvas.circle(x, y, 1.1 * mm, fill=1, stroke=0)
        elif self.kind == "skills":
            canvas.roundRect(center - 3.1 * mm, center - 3.1 * mm, 6.2 * mm, 6.2 * mm, 1 * mm, fill=0, stroke=1)
            canvas.line(center - 2 * mm, center, center - 0.5 * mm, center - 1.6 * mm)
            canvas.line(center - 0.5 * mm, center - 1.6 * mm, center + 2.3 * mm, center + 1.8 * mm)
        else:
            for offset in (1.7 * mm, 0, -1.7 * mm):
                canvas.line(center - 2.5 * mm, center + offset, center + 2.5 * mm, center + offset)
        canvas.restoreState()


def _pdf_section_heading(title: str, kind: str, style, width: float) -> Table:
    heading = Table(
        [[SectionIcon(kind), Paragraph(html.escape(title), style)]],
        colWidths=[12 * mm, width - 12 * mm],
    )
    heading.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0F8F6")),
        ("BOX", (0, 0), (-1, -1), 0.55, colors.HexColor("#CDE7E1")),
        ("LEFTPADDING", (0, 0), (0, 0), 4),
        ("RIGHTPADDING", (0, 0), (0, 0), 1),
        ("LEFTPADDING", (1, 0), (1, 0), 2),
        ("RIGHTPADDING", (1, 0), (1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return heading


def _draw_pdf_page(canvas, doc, pdf_font: str) -> None:
    """绘制适合打印的浅色背景和页码。"""
    canvas.saveState()
    page_width, page_height = A4
    canvas.setFillColor(colors.HexColor("#FAFCFB"))
    canvas.rect(0, 0, page_width, page_height, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#EAF6F2"))
    canvas.rect(0, 0, 7 * mm, page_height, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#EEF7FA"))
    canvas.circle(page_width - 13 * mm, page_height - 15 * mm, 30 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#F1F8F6"))
    canvas.circle(-9 * mm, 18 * mm, 25 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#D7ECE7"))
    canvas.setLineWidth(0.7)
    canvas.line(14 * mm, page_height - 8 * mm, page_width - 17 * mm, page_height - 8 * mm)
    canvas.setFont(pdf_font, 8)
    canvas.setFillColor(colors.HexColor("#6A8580"))
    canvas.drawCentredString(page_width / 2, 8 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def create_resume_pdf(
    resume_data: dict,
    optimized_text: str | None = None,
    photo_bytes: bytes | None = None,
) -> bytes:
    """生成招聘网站风格的完整中文 PDF 简历。"""
    pdf_font = _register_pdf_font()
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=f"{_plain(resume_data.get('name'))}的简历",
        author=_plain(resume_data.get("name")),
    )
    styles = getSampleStyleSheet()
    name_style = ParagraphStyle(
        "ResumeName", parent=styles["Title"], fontName=pdf_font,
        fontSize=22, leading=27, alignment=TA_LEFT,
        textColor=colors.HexColor("#172B4D"), spaceAfter=4,
    )
    role_style = ParagraphStyle(
        "ResumeRole", parent=styles["Normal"], fontName=pdf_font,
        fontSize=10.5, leading=15, textColor=colors.HexColor("#46566B"),
    )
    heading_style = ParagraphStyle(
        "ResumeHeading", parent=styles["Heading2"], fontName=pdf_font,
        fontSize=12.5, leading=17, textColor=colors.HexColor("#276D63"),
        spaceBefore=0, spaceAfter=0,
    )
    body_style = ParagraphStyle(
        "ResumeBody", parent=styles["BodyText"], fontName=pdf_font,
        fontSize=10.2, leading=15.2, alignment=TA_LEFT,
        textColor=colors.HexColor("#202938"), spaceAfter=3, wordWrap="CJK",
    )

    header_items = [
        Paragraph(html.escape(_plain(resume_data.get("name")) or "个人简历"), name_style),
        Paragraph(html.escape(f"目标岗位：{_plain(resume_data.get('target_role'))}"), role_style),
    ]
    contact = _contact_line(resume_data)
    if contact:
        header_items.append(Paragraph(html.escape(contact), role_style))

    story = [Spacer(1, 1 * mm)]
    if photo_bytes:
        header = Table([[header_items, _pdf_photo(photo_bytes)]], colWidths=[document.width - 34 * mm, 34 * mm])
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F7FB")),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7E2EE")),
        ]))
        story.extend([header, Spacer(1, 3 * mm)])
    else:
        story.extend(header_items + [Spacer(1, 3 * mm)])

    section_kinds = {
        "个人简介": "summary",
        "教育背景": "education",
        "工作经历": "work",
        "项目经历": "project",
        "技能证书": "skills",
    }
    for block_type, text in _content_blocks(resume_data, optimized_text):
        safe_text = html.escape(text)
        if block_type == "heading":
            story.append(KeepTogether([
                _pdf_section_heading(
                    text,
                    section_kinds.get(text, "summary"),
                    heading_style,
                    document.width,
                )
            ]))
        elif block_type == "bullet":
            story.append(Paragraph(html.escape(f"· {text}"), body_style))
        else:
            story.append(Paragraph(safe_text, body_style))

    document.build(
        story,
        onFirstPage=lambda canvas, doc: _draw_pdf_page(canvas, doc, pdf_font),
        onLaterPages=lambda canvas, doc: _draw_pdf_page(canvas, doc, pdf_font),
    )
    return output.getvalue()
