from resume_review import (
    apply_review_decisions,
    build_reviewed_resume_text,
    normalize_review_changes,
    text_diff_fragments,
)


def _resume():
    return {
        "summary": "具备数据分析经验，能够完成业务报表。",
        "education": "2020.09-2024.06，示例大学，统计学，本科。",
        "work_experience": "负责销售数据整理和周报制作。",
        "project_experience": "参与用户分析项目，完成数据清洗。",
        "skills": "Excel、Python。",
    }


def test_normalize_review_changes_requires_exact_source_anchor():
    changes = normalize_review_changes(
        {
            "changes": [
                {
                    "section": "summary",
                    "original_text": "具备数据分析经验，能够完成业务报表。",
                    "revised_text": "具备数据分析经验，可独立完成业务报表。",
                    "reason": "减少口语表达。",
                },
                {
                    "section": "skills",
                    "original_text": "熟练掌握 SQL。",
                    "revised_text": "熟练掌握 SQL 和 Tableau。",
                    "reason": "补充技能。",
                },
            ]
        },
        _resume(),
    )

    assert len(changes) == 1
    assert changes[0]["field"] == "summary"
    assert changes[0]["original_text"] == "具备数据分析经验，能够完成业务报表。"


def test_normalize_review_changes_rejects_invented_numbers():
    changes = normalize_review_changes(
        [
            {
                "section": "work_experience",
                "original_text": "负责销售数据整理和周报制作。",
                "revised_text": "负责销售数据整理和周报制作，效率提升 30%。",
                "reason": "增加量化结果。",
            }
        ],
        _resume(),
    )

    assert changes == []


def test_diff_fragments_show_real_added_and_removed_text():
    diff = text_diff_fragments(
        "能够完成业务报表",
        "可独立完成业务报表",
    )

    assert "独立" in "".join(diff["added"])
    assert "能够" in "".join(diff["removed"])


def test_only_accepted_changes_enter_final_resume():
    resume = _resume()
    changes = normalize_review_changes(
        [
            {
                "section": "summary",
                "original_text": "具备数据分析经验，能够完成业务报表。",
                "revised_text": "具备数据分析经验，可独立完成业务报表。",
                "reason": "表达更直接。",
            },
            {
                "section": "work_experience",
                "original_text": "负责销售数据整理和周报制作。",
                "revised_text": "负责销售数据整理，并按周制作业务报表。",
                "reason": "调整语序。",
            },
        ],
        resume,
    )
    decisions = {
        changes[0]["id"]: "accepted",
        changes[1]["id"]: "rejected",
    }

    sections = apply_review_decisions(resume, changes, decisions)
    final_text = build_reviewed_resume_text(resume, changes, decisions)

    assert sections["summary"] == "具备数据分析经验，可独立完成业务报表。"
    assert sections["work_experience"] == "负责销售数据整理和周报制作。"
    assert "个人简介\n具备数据分析经验，可独立完成业务报表。" in final_text
    assert "工作经历\n负责销售数据整理和周报制作。" in final_text
