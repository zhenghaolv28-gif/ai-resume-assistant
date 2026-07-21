from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _find(elements, label):
    return next(element for element in elements if element.label == label)


def _resume(**overrides):
    data = {
        "name": "测试用户",
        "phone": "",
        "email": "",
        "city": "",
        "target_role": "通用岗位",
        "job_description": "",
        "summary": "主简历内容",
        "education": "",
        "work_experience": "",
        "project_experience": "",
        "skills": "",
        "photo_bytes": None,
    }
    data.update(overrides)
    return data


def test_first_master_save_immediately_enables_new_job_version():
    at = AppTest.from_file(str(APP_PATH), default_timeout=20).run()

    _find(at.text_input, "姓名 *").set_value("测试用户")
    _find(at.text_input, "目标岗位 *").set_value("通用岗位")
    _find(at.button, "保存主简历并预览").click()
    at.run()

    new_version_button = _find(at.button, "新建岗位版本")
    assert not new_version_button.disabled
    assert at.session_state["master_resume_data"]["name"] == "测试用户"


def test_job_version_save_writes_all_fields_to_active_version():
    master_resume = _resume()
    job_resume = _resume(
        target_role="Python 后端工程师",
        job_description="熟悉 Python 和数据库",
    )
    at = AppTest.from_file(str(APP_PATH), default_timeout=20)
    at.session_state["master_resume_data"] = master_resume
    at.session_state["resume_data"] = job_resume
    at.session_state["resume_workspace_mode"] = "job"
    at.session_state["active_job_version_id"] = "backend"
    at.session_state["job_versions"] = {
        "backend": {
            "name": "后端岗位版",
            "resume_data": job_resume,
            "optimized_resume": "",
            "optimized_resume_editor": "",
            "job_match_result": None,
        }
    }
    at.run()

    _find(at.text_area, "自我介绍").set_value("岗位版本专用自我介绍")
    _find(at.text_area, "工作或实习经历").set_value(
        "2025 某公司 Python 后端开发"
    )
    _find(at.button, "保存岗位版本并预览").click()
    at.run()

    saved_resume = at.session_state["job_versions"]["backend"]["resume_data"]
    assert saved_resume["summary"] == "岗位版本专用自我介绍"
    assert saved_resume["work_experience"] == "2025 某公司 Python 后端开发"
    assert at.session_state["master_resume_data"]["summary"] == "主简历内容"
