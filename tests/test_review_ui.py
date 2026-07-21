from pathlib import Path

from streamlit.testing.v1 import AppTest

from resume_review import build_reviewed_resume_text


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _find(elements, label):
    return next(element for element in elements if element.label == label)


def test_review_ui_accepts_and_rejects_changes_individually():
    resume = {
        "name": "测试用户",
        "phone": "",
        "email": "",
        "city": "",
        "target_role": "数据分析师",
        "job_description": "负责业务数据分析。",
        "summary": "具备数据分析经验，能够完成业务报表。",
        "education": "",
        "work_experience": "负责销售数据整理和周报制作。",
        "project_experience": "",
        "skills": "Excel、Python。",
        "photo_bytes": None,
    }
    changes = [
        {
            "id": "summary-1",
            "field": "summary",
            "section": "个人简介",
            "original_text": "具备数据分析经验，能够完成业务报表。",
            "revised_text": "具备数据分析经验，可独立完成业务报表。",
            "reason": "表达更直接。",
        },
        {
            "id": "work_experience-1",
            "field": "work_experience",
            "section": "工作经历",
            "original_text": "负责销售数据整理和周报制作。",
            "revised_text": "负责销售数据整理，并按周制作业务报表。",
            "reason": "调整语序。",
        },
    ]
    decisions = {"summary-1": "pending", "work_experience-1": "pending"}
    at = AppTest.from_file(str(APP_PATH), default_timeout=20)
    at.session_state["resume_data"] = resume
    at.session_state["master_resume_data"] = resume
    at.session_state["job_versions"] = {}
    at.session_state["resume_workspace_mode"] = "master"
    at.session_state["active_job_version_id"] = None
    at.session_state["optimization_review"] = {
        "changes": changes,
        "decisions": decisions,
    }
    current_text = build_reviewed_resume_text(resume, changes, decisions)
    at.session_state["optimized_resume"] = current_text
    at.session_state["optimized_resume_editor"] = current_text
    at.run()

    assert any(
        warning.value == "AI 不得编造公司、项目、技能、证书和业绩数字。"
        for warning in at.warning
    )
    _find(at.button, "接受这一条").click()
    at.run()

    assert (
        at.session_state["optimization_review"]["decisions"]["summary-1"]
        == "accepted"
    )
    assert "可独立完成业务报表" in at.session_state["optimized_resume_editor"]

    [button for button in at.button if button.label == "拒绝这一条"][-1].click()
    at.run()

    assert (
        at.session_state["optimization_review"]["decisions"]["work_experience-1"]
        == "rejected"
    )
    assert "负责销售数据整理和周报制作" in at.session_state[
        "optimized_resume_editor"
    ]
