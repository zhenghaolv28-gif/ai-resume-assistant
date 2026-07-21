import json

import pytest

from ai_service import (
    REVIEW_SYSTEM_INSTRUCTIONS,
    ResumeOptimizationError,
    _parse_resume_review_result,
)


def _resume():
    return {
        "summary": "具备数据分析经验，能够完成业务报表。",
        "education": "",
        "work_experience": "负责销售数据整理和周报制作。",
        "project_experience": "",
        "skills": "Excel、Python。",
    }


def test_review_prompt_contains_explicit_no_fabrication_rule():
    assert "AI 不得编造公司、项目、技能、证书和业绩数字。" in REVIEW_SYSTEM_INSTRUCTIONS


def test_parse_review_result_keeps_only_safe_anchored_changes():
    content = json.dumps(
        {
            "changes": [
                {
                    "section": "summary",
                    "original_text": "具备数据分析经验，能够完成业务报表。",
                    "revised_text": "具备数据分析经验，可独立完成业务报表。",
                    "reason": "表达更直接。",
                },
                {
                    "section": "work_experience",
                    "original_text": "负责销售数据整理和周报制作。",
                    "revised_text": "负责销售数据整理和周报制作，效率提升 30%。",
                    "reason": "补充业绩。",
                },
            ]
        },
        ensure_ascii=False,
    )

    changes = _parse_resume_review_result(content, _resume())

    assert len(changes) == 1
    assert changes[0]["field"] == "summary"


def test_parse_review_result_rejects_when_every_change_is_unsafe():
    content = json.dumps(
        {
            "changes": [
                {
                    "section": "skills",
                    "original_text": "熟练使用 Tableau。",
                    "revised_text": "精通 Tableau。",
                    "reason": "加强技能表达。",
                }
            ]
        },
        ensure_ascii=False,
    )

    with pytest.raises(ResumeOptimizationError, match="没有给出可安全应用的修改"):
        _parse_resume_review_result(content, _resume())
