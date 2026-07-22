import json
from pathlib import Path

from browser_persistence import (
    build_browser_workspace_snapshot,
    parse_json_resume_backup,
    restore_browser_workspace_snapshot,
)


FORM_FIELDS = (
    "name",
    "phone",
    "email",
    "city",
    "target_role",
    "job_description",
    "summary",
    "education",
    "work_experience",
    "project_experience",
    "skills",
)
TEMPLATES = {"business_blue", "minimal_mono"}


def _resume(**overrides):
    resume = {
        "name": "林晓然",
        "phone": "13800138000",
        "email": "lin@example.com",
        "city": "上海",
        "target_role": "产品经理",
        "job_description": "负责产品规划",
        "summary": "五年产品经验",
        "education": "某大学本科",
        "work_experience": "某科技公司产品经理",
        "project_experience": "招聘平台项目",
        "skills": "Figma、SQL",
        "photo_bytes": b"photo-bytes",
        "template_id": "business_blue",
    }
    resume.update(overrides)
    return resume


def _build_snapshot():
    master = _resume()
    return build_browser_workspace_snapshot(
        master_resume=master,
        current_resume=master,
        job_versions={
            "product": {
                "name": "产品岗位版",
                "resume_data": _resume(summary="岗位版内容"),
                "optimized_resume": "优化稿",
                "optimized_resume_editor": "采用稿",
                "optimization_review": {
                    "changes": [],
                    "deepseek_api_key": "must-not-save",
                },
                "job_match_result": {
                    "score": 88,
                    "api_key": "must-not-save",
                },
            }
        },
        master_results={
            "optimized_resume": "主简历优化稿",
            "token": "must-not-save",
        },
        workspace_mode="master",
        active_job_version_id=None,
        form_fields=FORM_FIELDS,
        default_template_id="business_blue",
        allowed_templates=TEMPLATES,
    )


def test_browser_snapshot_keeps_name_and_photo_but_removes_contact_details_and_secrets():
    snapshot = _build_snapshot()
    serialized = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["master_resume"]["name"] == "林晓然"
    assert snapshot["master_resume"]["phone"] == ""
    assert snapshot["master_resume"]["email"] == ""
    assert snapshot["master_resume"]["city"] == ""
    assert snapshot["master_resume"]["photo_base64"]
    assert "must-not-save" not in serialized
    assert "deepseek_api_key" not in serialized
    assert '"api_key"' not in serialized
    assert '"token"' not in serialized


def test_browser_snapshot_round_trip_restores_photo_versions_and_ai_results():
    snapshot = _build_snapshot()

    restored = restore_browser_workspace_snapshot(
        snapshot,
        form_fields=FORM_FIELDS,
        default_template_id="business_blue",
        allowed_templates=TEMPLATES,
        photo_max_bytes=5 * 1024 * 1024,
    )

    assert restored is not None
    assert restored["master_resume_data"]["name"] == "林晓然"
    assert restored["master_resume_data"]["phone"] == ""
    assert restored["master_resume_data"]["photo_bytes"] == b"photo-bytes"
    assert restored["job_versions"]["product"]["resume_data"]["summary"] == "岗位版内容"
    assert restored["job_versions"]["product"]["optimized_resume"] == "优化稿"
    assert restored["master_results"]["optimized_resume"] == "主简历优化稿"


def test_captured_draft_overlays_saved_master_without_restoring_contacts():
    snapshot = _build_snapshot()
    snapshot["draft_captured"] = True
    snapshot["draft"]["summary"] = "刷新前尚未点击保存的内容"
    snapshot["draft"]["phone"] = "不应恢复"

    restored = restore_browser_workspace_snapshot(
        snapshot,
        form_fields=FORM_FIELDS,
        default_template_id="business_blue",
        allowed_templates=TEMPLATES,
        photo_max_bytes=5 * 1024 * 1024,
    )

    assert restored["master_resume_data"]["summary"] == "刷新前尚未点击保存的内容"
    assert restored["master_resume_data"]["phone"] == ""


def test_json_backup_restore_supports_full_library_and_preserves_manual_contact_backup():
    backup = {
        "schema_version": 2,
        "master_resume": _resume() | {"photo_bytes": None},
        "job_versions": [
            {
                "id": "product",
                "name": "产品岗位版",
                "resume_data": _resume(summary="岗位版本") | {"photo_bytes": None},
                "optimized_resume": "优化稿",
                "optimized_resume_editor": "采用稿",
                "optimization_review": None,
                "job_match_result": {"score": 90},
            }
        ],
    }

    restored = parse_json_resume_backup(
        json.dumps(backup, ensure_ascii=False, default=str),
        form_fields=FORM_FIELDS,
        default_template_id="business_blue",
        allowed_templates=TEMPLATES,
    )

    assert restored["master_resume_data"]["phone"] == "13800138000"
    assert restored["master_resume_data"]["photo_bytes"] is None
    assert restored["job_versions"]["product"]["resume_data"]["summary"] == "岗位版本"


def test_component_uses_indexeddb_and_one_second_debounce():
    component_source = (
        Path(__file__).resolve().parents[1] / "browser_autosave_component.py"
    ).read_text(encoding="utf-8")

    assert "indexedDB.open" in component_source
    assert "const SAVE_DELAY = 1000" in component_source
    assert 'phone: ""' in component_source
    assert 'email: ""' in component_source
    assert 'city: ""' in component_source
    assert "DeepSeek API Key" not in component_source
