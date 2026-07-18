"""读取并初步整理 PDF/Word 简历。

这里故意使用本地规则完成第一次识别，不把用户上传的完整简历自动发送给
DeepSeek。识别结果一定要经过用户确认后，才会保存到主简历。
"""

from __future__ import annotations

from io import BytesIO
import re
import unicodedata
from typing import Any
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from docx import Document


_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "summary": (
        "自我介绍",
        "个人简介",
        "个人总结",
        "自我评价",
        "职业概述",
        "关于我",
        "个人优势",
        "个人概述",
        "职业简介",
        "个人说明",
        "个人介绍",
        "个人概况",
        "个人亮点",
        "个人陈述",
        "自我概况",
        "优势总结",
        "职业摘要",
        "简介",
        "profile",
        "professional summary",
        "career profile",
        "summary",
        "about me",
    ),
    "education": (
        "教育经历",
        "教育背景",
        "学历经历",
        "学历信息",
        "教育信息",
        "学历",
        "学习经历",
        "学术经历",
        "education",
        "education background",
        "academic background",
    ),
    "work_experience": (
        "工作经历",
        "工作经验",
        "工作履历",
        "实习经历",
        "实习经验",
        "职业经历",
        "任职经历",
        "就业经历",
        "工作实习经历",
        "工作与实习经历",
        "工作/实习经历",
        "工作实践",
        "社会工作经历",
        "work experience",
        "professional experience",
        "employment history",
        "internship experience",
    ),
    "project_experience": (
        "项目经历",
        "项目经验",
        "项目实践",
        "项目成果",
        "个人项目",
        "项目案例",
        "项目作品",
        "项目亮点",
        "项目开发",
        "项目实践经历",
        "科研项目",
        "校园项目",
        "个人项目经历",
        "项目详情",
        "作品集",
        "project experience",
        "projects",
        "selected projects",
    ),
    "skills": (
        "技能与证书",
        "专业技能",
        "技能证书",
        "技能",
        "技能特长",
        "专业能力",
        "核心技能",
        "核心竞争力",
        "技术技能",
        "技术能力",
        "计算机技能",
        "个人技能",
        "专业特长",
        "技能专长",
        "技能清单",
        "能力特长",
        "知识技能",
        "技术栈",
        "工具技能",
        "证书及荣誉",
        "证书和技能",
        "培训经历",
        "语言及技能",
        "it技能",
        "证书",
        "证书资质",
        "资格证书",
        "语言能力",
        "语言技能",
        "荣誉奖项",
        "获奖经历",
        "奖项荣誉",
        "skills",
        "technical skills",
        "core competencies",
        "certifications",
        "certificates",
        "honors and awards",
    ),
    "target_role": (
        "求职意向",
        "求职目标",
        "求职方向",
        "期望职位",
        "期望岗位",
        "目标岗位",
        "应聘职位",
        "应聘岗位",
        "求职岗位",
        "职位目标",
        "应聘方向",
        "申请职位",
        "职业目标",
        "career objective",
        "objective",
        "target position",
    ),
}

_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "name": ("姓名", "名字"),
    "phone": ("手机", "手机号码", "联系电话", "电话", "联系方式"),
    "email": ("邮箱", "电子邮箱", "电子邮件", "email"),
    "city": (
        "所在城市",
        "现居地",
        "现居城市",
        "居住地",
        "所在地区",
        "所在地",
        "现所在地",
        "居住城市",
        "工作城市",
        "户籍所在地",
        "所在地点",
        "所在位置",
        "籍贯",
        "城市",
        "地区",
        "地址",
        "location",
    ),
    "target_role": _SECTION_ALIASES["target_role"],
}

_FIELD_NAMES = (
    "name",
    "phone",
    "email",
    "city",
    "target_role",
    "summary",
    "education",
    "work_experience",
    "project_experience",
    "skills",
)

_IGNORED_NAME_LINES = {
    "个人简历",
    "简历",
    "resume",
    "curriculum vitae",
    "cv",
}


def _normalize_text(text: str) -> str:
    """统一 PDF/Word 常见的换行、空格和全角字符。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    blank_seen = False
    for raw_line in text.split("\n"):
        line = re.sub(r"[ \t\u00a0]+", " ", raw_line).strip()
        if not line:
            if not blank_seen:
                lines.append("")
            blank_seen = True
            continue
        lines.append(line)
        blank_seen = False
    return "\n".join(lines).strip()


def _read_pdf(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("读取 PDF 需要 pypdf 依赖，请先安装项目依赖。") from exc

    try:
        reader = PdfReader(BytesIO(file_bytes))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # pragma: no cover - varies by PDF producer
        raise ValueError("这个 PDF 暂时无法读取，请尝试另存为新的 PDF 后再上传。") from exc
    return _normalize_text("\n".join(pages))


_WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_WORD_PARAGRAPH = f"{_WORD_NAMESPACE}p"
_WORD_TEXT = f"{_WORD_NAMESPACE}t"
_WORD_TAB = f"{_WORD_NAMESPACE}tab"
_WORD_BREAK = f"{_WORD_NAMESPACE}br"
_WORD_CARRIAGE_RETURN = f"{_WORD_NAMESPACE}cr"


def _word_paragraph_text(paragraph: ElementTree.Element) -> str:
    """读取一个 Word 段落，但跳过其中嵌套文本框的子段落。"""
    parts: list[str] = []

    def visit(node: ElementTree.Element) -> None:
        for child in node:
            if child.tag == _WORD_PARAGRAPH:
                # 文本框里的段落会由外层 root.iter 单独读取，不能在这里重复收集。
                continue
            if child.tag == _WORD_TEXT:
                parts.append(child.text or "")
            elif child.tag == _WORD_TAB:
                parts.append("\t")
            elif child.tag in {_WORD_BREAK, _WORD_CARRIAGE_RETURN}:
                parts.append("\n")
            else:
                visit(child)

    visit(paragraph)
    return "".join(parts).strip()


def _read_docx_xml(file_bytes: bytes) -> str:
    """读取 DOCX XML 中的段落，覆盖文本框、页眉和页脚等区域。"""
    parts: list[str] = []
    with ZipFile(BytesIO(file_bytes)) as archive:
        xml_names = [
            name
            for name in archive.namelist()
            if name == "word/document.xml"
            or re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
        ]

        def content_order(name: str) -> tuple[int, str]:
            if "/header" in name:
                return 0, name
            if name == "word/document.xml":
                return 1, name
            return 2, name

        xml_names.sort(key=content_order)
        for name in xml_names:
            try:
                root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                continue
            for paragraph in root.iter(_WORD_PARAGRAPH):
                text = _word_paragraph_text(paragraph)
                if text:
                    parts.append(text)
    return _normalize_text("\n".join(parts))


def _flatten_docx2python_content(value: Any) -> list[str]:
    """按 docx2python 保留的文档/表格顺序取出段落。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        paragraphs: list[str] = []
        for item in value:
            paragraphs.extend(_flatten_docx2python_content(item))
        return paragraphs
    return []


def _deduplicate_paragraphs(paragraphs: list[str]) -> list[str]:
    """去除解析器或合并单元格产生的重复段落，同时保留正常重复短词。"""
    result: list[str] = []
    long_paragraphs_seen: set[str] = set()
    for paragraph in paragraphs:
        normalized = _normalize_text(paragraph)
        if not normalized:
            continue
        comparison_key = re.sub(r"\s+", "", normalized)
        if result and normalized == result[-1]:
            continue
        if len(comparison_key) >= 20:
            if comparison_key in long_paragraphs_seen:
                continue
            long_paragraphs_seen.add(comparison_key)
        result.append(normalized)
    return result


def _read_docx_structured(file_bytes: bytes) -> str:
    """使用成熟解析器按段落/表格结构读取 DOCX，并关闭合并单元格复制。"""
    try:
        from docx2python import docx2python
    except ImportError:
        return ""

    try:
        with docx2python(
            BytesIO(file_bytes),
            duplicate_merged_cells=False,
        ) as content:
            header = _flatten_docx2python_content(content.header)
            body = _flatten_docx2python_content(content.body)
            footer = _flatten_docx2python_content(content.footer)
    except Exception:
        return ""

    paragraphs = _deduplicate_paragraphs(header + body + footer)
    return _normalize_text("\n".join(paragraphs))


def _read_docx(file_bytes: bytes) -> str:
    structured_text = _read_docx_structured(file_bytes)
    if len(structured_text.replace("\n", "").strip()) >= 10:
        return structured_text

    try:
        xml_text = _read_docx_xml(file_bytes)
        if len(xml_text.replace("\n", "").strip()) >= 10:
            return xml_text
    except (BadZipFile, OSError, ElementTree.ParseError):
        pass

    try:
        document = Document(BytesIO(file_bytes))
    except Exception as exc:  # pragma: no cover - varies by Word producer
        raise ValueError("这个 Word 文件无法读取，请另存为 .docx 后再上传。") from exc

    parts: list[str] = []
    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            parts.append(paragraph.text)
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return _normalize_text("\n".join(parts))


def extract_resume_text(file_bytes: bytes, filename: str) -> str:
    """从 .pdf 或 .docx 读取文本。"""
    if not file_bytes:
        raise ValueError("上传的文件为空，请重新选择。")
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "pdf":
        text = _read_pdf(file_bytes)
    elif suffix == "docx":
        text = _read_docx(file_bytes)
    elif suffix == "doc":
        raise ValueError("暂不支持旧版 .doc，请在 Word 中另存为 .docx 后再上传。")
    else:
        raise ValueError("只支持 PDF 或 Word（.docx）文件。")
    if len(text.replace("\n", "").strip()) < 10:
        if suffix == "docx":
            raise ValueError(
                "这个 Word 中没有读取到足够的可编辑文字。文件内容可能是图片，"
                "请在 Word 中确认文字可以选中复制，再另存为新的 .docx 后上传。"
            )
        raise ValueError("没有读取到足够的文字。若这是扫描版 PDF，请先用 OCR 转成可复制文字的 PDF。")
    return text


def _compact_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _find_field(label: str) -> str | None:
    compact = _compact_key(label)
    for field_name, aliases in _FIELD_LABELS.items():
        for alias in aliases:
            alias_key = _compact_key(alias)
            if compact == alias_key:
                return field_name
    for field_name, aliases in _SECTION_ALIASES.items():
        for alias in aliases:
            alias_key = _compact_key(alias)
            if compact == alias_key:
                return field_name
    return None


def _guess_heading_field(title: str) -> str | None:
    """根据常见关键词兼容用户自定义的章节标题。"""
    compact = _compact_key(title)
    if len(compact) > 24:
        return None
    keyword_groups = (
        ("target_role", ("求职", "应聘", "期望", "目标岗位", "objective", "target")),
        ("education", ("教育", "学历", "学业", "学习", "academic")),
        ("work_experience", ("工作", "实习", "任职", "职业", "就业", "employment")),
        ("project_experience", ("项目", "实践", "作品", "project")),
        ("skills", ("技能", "能力", "证书", "资质", "语言", "荣誉", "奖项", "skill", "certificate")),
        ("summary", ("自我", "个人简介", "个人概述", "职业简介", "profile", "summary", "about")),
    )
    for field_name, keywords in keyword_groups:
        if any(_compact_key(keyword) in compact for keyword in keywords):
            return field_name
    return None


def _heading_prefix_and_value(line: str) -> tuple[str | None, str]:
    """识别“工作经历 2020—2024”或“工作经历 / Work Experience”。"""
    title = re.sub(r"^\s*(?:[-•●▪·]\s*|\d+[.、)]\s*)+", "", line).strip()
    aliases: list[tuple[str, str]] = []
    for field_name, field_aliases in _SECTION_ALIASES.items():
        aliases.extend((field_name, alias) for alias in field_aliases)
    for field_name, alias in sorted(aliases, key=lambda item: len(item[1]), reverse=True):
        match = re.match(
            rf"^{re.escape(alias)}(?=$|[\s:：|/\\()（）—–-])",
            title,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        remainder = title[match.end() :].strip()
        cleaned_remainder = remainder.strip(" ：:|/\\()（）—–-·•")
        if not cleaned_remainder or _find_field(cleaned_remainder):
            return field_name, ""
        return field_name, cleaned_remainder
    return None, ""


def _heading_and_value(
    line: str,
    current_field: str | None = None,
) -> tuple[str | None, str]:
    """识别单独章节标题，也识别“邮箱：xxx”这种行内字段。"""
    match = re.match(r"^\s*(?:\d+[.、)]\s*)?([^:：|]{1,20})\s*[:：|]\s*(.*)$", line)
    if match:
        field = _find_field(match.group(1))
        if field:
            return field, match.group(2).strip()

    prefix_field, prefix_value = _heading_prefix_and_value(line)
    if prefix_field:
        return prefix_field, prefix_value

    title = re.sub(r"^\s*(?:\d+[.、)]\s*)", "", line).strip(" ：:|·•")
    field = _find_field(title)
    if field and len(title) <= 60:
        return field, ""
    guessed_field = _guess_heading_field(title)
    if (
        guessed_field
        and guessed_field != current_field
        and len(title) <= 24
        and not re.search(r"[。；，,.;]$", title)
    ):
        return guessed_field, ""
    return None, ""


def _looks_like_unrecognized_heading(line: str) -> bool:
    """发现未知章节标题时切断上一章节，避免后续内容整体错位。"""
    title = re.sub(r"^\s*(?:[-•●▪·]\s*|\d+[.、)]\s*)+", "", line).strip()
    if not title or len(title) > 20 or re.search(r"[，,。；;：:]", title):
        return False
    compact = _compact_key(title)
    endings = (
        "经历",
        "经验",
        "背景",
        "信息",
        "情况",
        "能力",
        "技能",
        "介绍",
        "评价",
        "概述",
        "目标",
        "意向",
        "履历",
        "资质",
        "证书",
        "实践",
        "荣誉",
        "奖项",
        "experience",
        "background",
        "education",
        "skills",
        "projects",
        "summary",
        "profile",
        "contact",
    )
    return compact.endswith(endings)


def _guess_labeled_value(lines: list[str], field_name: str) -> str:
    aliases = _FIELD_LABELS[field_name]
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    pattern = re.compile(rf"^\s*(?:{alias_pattern})\s*[:：|]\s*(.+?)\s*$", re.IGNORECASE)
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group(1).strip()
    return ""


def _guess_name(lines: list[str]) -> str:
    labeled = _guess_labeled_value(lines, "name")
    if labeled:
        return labeled
    for line in lines[:12]:
        candidate = line.strip(" ：:|·•")
        if not candidate or candidate.lower() in _IGNORED_NAME_LINES:
            continue
        if re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", candidate):
            continue
        if re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", candidate):
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,6}", candidate):
            return candidate
        if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,40}", candidate) and " " in candidate:
            return candidate
    return ""


def _guess_phone(text: str, lines: list[str]) -> str:
    match = re.search(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)", text)
    if match:
        return re.sub(r"\D", "", match.group(0))[-11:]
    return _guess_labeled_value(lines, "phone")


def _guess_email(text: str, lines: list[str]) -> str:
    match = re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", text)
    return match.group(0) if match else _guess_labeled_value(lines, "email")


def _join_section(lines: list[str]) -> str:
    return "\n".join(line for line in lines if line.strip()).strip()


def parse_resume_text(text: str) -> dict[str, Any]:
    """使用常见中文/英文章节标题将简历文本分成表单字段。"""
    normalized = _normalize_text(text)
    lines = [line for line in normalized.split("\n") if line.strip()]
    buckets: dict[str, list[str]] = {field: [] for field in _FIELD_NAMES}
    unclassified: list[str] = []
    current_field: str | None = None
    single_value_fields = {"name", "phone", "email", "city", "target_role"}

    for line in lines:
        field, inline_value = _heading_and_value(
            line,
            current_field=current_field,
        )
        if field:
            if inline_value:
                buckets[field].append(inline_value)
                current_field = None if field in single_value_fields else field
            else:
                current_field = field
            continue
        if _looks_like_unrecognized_heading(line):
            current_field = None
            unclassified.append(line)
            continue
        if current_field:
            buckets[current_field].append(line)
            if current_field in single_value_fields:
                current_field = None
        else:
            unclassified.append(line)

    result: dict[str, Any] = {field: _join_section(buckets[field]) for field in _FIELD_NAMES}
    result["name"] = result["name"] or _guess_name(lines)
    result["phone"] = result["phone"] or _guess_phone(normalized, lines)
    result["email"] = result["email"] or _guess_email(normalized, lines)
    result["city"] = result["city"] or _guess_labeled_value(lines, "city")
    result["target_role"] = result["target_role"] or _guess_labeled_value(lines, "target_role")
    result["raw_text"] = normalized
    result["unclassified_text"] = _join_section(unclassified)
    return result
