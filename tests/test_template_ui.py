from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _resume():
    return {
        "name": "测试用户",
        "phone": "13800138000",
        "email": "test@example.com",
        "city": "上海",
        "target_role": "产品经理",
        "job_description": "",
        "summary": "具备产品经验。",
        "education": "某大学 本科",
        "work_experience": "某公司 产品经理",
        "project_experience": "招聘平台项目",
        "skills": "Axure、SQL",
        "photo_bytes": None,
        "template_id": "business_blue",
    }


def test_template_selector_updates_master_resume_and_preview():
    resume = _resume()
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.session_state["resume_data"] = resume
    at.session_state["master_resume_data"] = resume
    at.session_state["resume_workspace_mode"] = "master"
    at.session_state["active_job_version_id"] = None
    at.session_state["job_versions"] = {}
    at.run()

    selector = next(
        item for item in at.selectbox if item.label == "选择专业简历模板"
    )
    selector.select("青绿现代")
    at.run()

    assert at.session_state["resume_data"]["template_id"] == "modern_teal"
    assert at.session_state["master_resume_data"]["template_id"] == "modern_teal"
    assert any("预览会随模板和内容即时更新" in item.value for item in at.caption)
