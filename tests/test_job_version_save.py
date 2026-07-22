from pathlib import Path

from streamlit.testing.v1 import AppTest

from job_version_operations import (
    duplicate_job_version,
    remove_job_version,
    rename_job_version,
    unique_job_version_name,
)

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


def _job_version_app():
    master_resume = _resume()
    job_resume = _resume(
        target_role="产品经理",
        summary="岗位版本内容",
    )
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.session_state["master_resume_data"] = master_resume
    at.session_state["resume_data"] = job_resume
    at.session_state["resume_workspace_mode"] = "job"
    at.session_state["active_job_version_id"] = "product"
    at.session_state["job_versions"] = {
        "product": {
            "name": "产品经理版本",
            "resume_data": job_resume,
            "optimized_resume": "优化后的内容",
            "optimized_resume_editor": "优化后的内容",
            "optimization_review": {
                "changes": [],
                "decisions": {},
            },
            "job_match_result": {"score": 88},
        }
    }
    return at.run()


def test_job_version_management_controls_are_available():
    at = _job_version_app()

    for label in ("重命名", "复制", "删除"):
        _find(at.button, label)

    _find(at.button, "重命名").click()
    at.run()
    _find(at.text_input, "新版本名称 *")
    _find(at.button, "保存新名称")


def test_job_version_data_can_be_renamed():
    versions = {"product": {"name": "产品经理版本", "resume_data": _resume()}}

    renamed = rename_job_version(versions, "product", "资深产品经理版本")

    assert renamed
    assert versions["product"]["name"] == "资深产品经理版本"


def test_job_version_can_be_duplicated_with_independent_data():
    versions = {
        "product": {
            "name": "产品经理版本",
            "resume_data": _resume(summary="岗位版本内容"),
            "optimized_resume_editor": "优化后的内容",
            "job_match_result": {"score": 88},
        }
    }
    copy_name = unique_job_version_name(versions, "产品经理版本")

    copied_version = duplicate_job_version(
        versions,
        "product",
        "product-copy",
        copy_name,
    )

    assert copied_version is not None
    assert len(versions) == 2
    assert copied_version["name"] == copy_name
    assert copied_version["resume_data"]["summary"] == "岗位版本内容"
    assert copied_version["optimized_resume_editor"] == "优化后的内容"
    assert copied_version["job_match_result"]["score"] == 88
    copied_version["resume_data"]["summary"] = "副本独立内容"
    assert versions["product"]["resume_data"]["summary"] == "岗位版本内容"


def test_duplicate_name_automatically_uses_next_available_suffix():
    versions = {
        "original": {"name": "产品经理版本"},
        "copy-one": {"name": "产品经理版本 副本"},
        "copy-two": {"name": "产品经理版本 副本 2"},
    }

    assert unique_job_version_name(versions, "产品经理版本") == "产品经理版本 副本 3"


def test_job_version_data_can_be_deleted_without_affecting_others():
    versions = {
        "product": {"name": "产品经理版本"},
        "backend": {"name": "后端版本"},
    }

    deleted_version = remove_job_version(versions, "product")

    assert deleted_version == {"name": "产品经理版本"}
    assert "product" not in versions
    assert versions["backend"]["name"] == "后端版本"
