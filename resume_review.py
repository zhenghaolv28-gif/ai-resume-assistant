"""AI 简历修改建议的校验、差异计算和最终稿合并。"""

from __future__ import annotations

from difflib import SequenceMatcher
import re


REVIEW_SECTIONS = (
    ("summary", "个人简介"),
    ("education", "教育背景"),
    ("work_experience", "工作经历"),
    ("project_experience", "项目经历"),
    ("skills", "技能证书"),
)
SECTION_LABELS = dict(REVIEW_SECTIONS)
SECTION_ALIASES = {
    "summary": "summary",
    "个人简介": "summary",
    "职业概述": "summary",
    "自我介绍": "summary",
    "education": "education",
    "教育背景": "education",
    "教育经历": "education",
    "work_experience": "work_experience",
    "工作经历": "work_experience",
    "工作或实习经历": "work_experience",
    "实习经历": "work_experience",
    "project_experience": "project_experience",
    "项目经历": "project_experience",
    "skills": "skills",
    "技能证书": "skills",
    "技能与证书": "skills",
}
MAX_REVIEW_CHANGES = 12


def _text_value(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if value is None:
        return ""
    return str(value).strip()


def _clean_revised_text(value: object) -> str:
    """清除模型偶尔附带的 Markdown，同时保留简历原有换行。"""
    text = _text_value(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("```", "").replace("**", "").replace("__", "")
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = re.sub(r"^\s*[-+*•]\s+", "· ", line)
        cleaned_lines.append(line.rstrip())
    return "\n".join(cleaned_lines).strip()


def _number_tokens(text: str) -> set[str]:
    """提取数字、百分比和常见小数，防止 AI 凭空添加业绩数字。"""
    return {
        token.replace(",", "")
        for token in re.findall(r"(?<![A-Za-z0-9])\d[\d,.]*%?", text)
    }


def normalize_review_changes(raw_changes: object, resume_data: dict) -> list[dict]:
    """只保留能精确锚定原文且没有新增数字的修改建议。"""
    if isinstance(raw_changes, dict):
        raw_changes = raw_changes.get("changes", [])
    if not isinstance(raw_changes, list):
        return []

    changes: list[dict] = []
    used_anchors: set[tuple[str, str]] = set()
    field_counts: dict[str, int] = {}

    for raw_change in raw_changes:
        if not isinstance(raw_change, dict):
            continue
        raw_section = _text_value(
            raw_change.get("section") or raw_change.get("field")
        )
        field_name = SECTION_ALIASES.get(raw_section)
        if not field_name:
            continue

        source_text = _text_value(resume_data.get(field_name, ""))
        original_text = _text_value(
            raw_change.get("original_text") or raw_change.get("original")
        )
        revised_text = _clean_revised_text(
            raw_change.get("revised_text") or raw_change.get("revised")
        )
        if (
            not source_text
            or not original_text
            or original_text not in source_text
            or not revised_text
            or original_text == revised_text
        ):
            continue

        anchor = (field_name, original_text)
        if anchor in used_anchors:
            continue
        if not _number_tokens(revised_text).issubset(_number_tokens(source_text)):
            continue

        field_counts[field_name] = field_counts.get(field_name, 0) + 1
        changes.append(
            {
                "id": f"{field_name}-{field_counts[field_name]}",
                "field": field_name,
                "section": SECTION_LABELS[field_name],
                "original_text": original_text,
                "revised_text": revised_text,
                "reason": _text_value(raw_change.get("reason"))
                or "调整表达，使内容更清楚、更便于招聘者快速阅读。",
            }
        )
        used_anchors.add(anchor)
        if len(changes) >= MAX_REVIEW_CHANGES:
            break

    return changes


def text_diff_fragments(original_text: str, revised_text: str) -> dict[str, list[str]]:
    """计算修改后实际新增和删除的连续文字片段。"""
    added: list[str] = []
    removed: list[str] = []
    matcher = SequenceMatcher(None, original_text, revised_text, autojunk=False)
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation in ("insert", "replace"):
            fragment = revised_text[new_start:new_end].strip()
            if fragment:
                added.append(fragment)
        if operation in ("delete", "replace"):
            fragment = original_text[old_start:old_end].strip()
            if fragment:
                removed.append(fragment)
    return {"added": added, "removed": removed}


def apply_review_decisions(
    resume_data: dict,
    changes: list[dict],
    decisions: dict[str, str] | None,
) -> dict[str, str]:
    """只应用明确接受的建议；待审阅和拒绝的建议继续使用原文。"""
    final_sections = {
        field_name: _text_value(resume_data.get(field_name, ""))
        for field_name, _label in REVIEW_SECTIONS
    }
    current_decisions = decisions or {}
    for change in changes:
        if current_decisions.get(change.get("id")) != "accepted":
            continue
        field_name = change.get("field")
        original_text = _text_value(change.get("original_text"))
        revised_text = _text_value(change.get("revised_text"))
        if field_name not in final_sections or not original_text or not revised_text:
            continue
        if original_text in final_sections[field_name]:
            final_sections[field_name] = final_sections[field_name].replace(
                original_text,
                revised_text,
                1,
            )
    return final_sections


def build_reviewed_resume_text(
    resume_data: dict,
    changes: list[dict],
    decisions: dict[str, str] | None,
) -> str:
    """把逐条审阅结果整理为 Word/PDF 导出所需的章节正文。"""
    final_sections = apply_review_decisions(resume_data, changes, decisions)
    blocks: list[str] = []
    for field_name, section_label in REVIEW_SECTIONS:
        content = final_sections.get(field_name, "").strip()
        if content:
            blocks.extend((section_label, content, ""))
    return "\n".join(blocks).strip()
