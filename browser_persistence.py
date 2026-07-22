"""浏览器自动保存与 JSON 恢复所需的纯数据处理。"""

from __future__ import annotations

from base64 import b64decode, b64encode
from copy import deepcopy
import json
from typing import Any, Iterable, Mapping


BROWSER_SCHEMA_VERSION = 1
BROWSER_SENSITIVE_FIELDS = frozenset({"phone", "email", "city"})
FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "deepseek_api_key",
        "access_token",
        "refresh_token",
        "secret",
        "token",
    }
)


def _secret_key(key: object) -> bool:
    normalized = str(key).strip().casefold().replace("-", "_").replace(" ", "_")
    return normalized in FORBIDDEN_SECRET_KEYS


def strip_forbidden_secrets(value: Any) -> Any:
    """递归删除 API Key、token 和 secret 字段。"""
    if isinstance(value, Mapping):
        return {
            str(key): strip_forbidden_secrets(item)
            for key, item in value.items()
            if not _secret_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [strip_forbidden_secrets(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def encode_photo(photo_bytes: bytes | bytearray | None) -> str:
    if not photo_bytes:
        return ""
    return b64encode(bytes(photo_bytes)).decode("ascii")


def decode_photo(photo_base64: object, max_bytes: int) -> bytes | None:
    if not isinstance(photo_base64, str) or not photo_base64.strip():
        return None
    try:
        decoded = b64decode(photo_base64, validate=True)
    except (ValueError, TypeError):
        return None
    return decoded if len(decoded) <= max_bytes else None


def _template_id(value: object, default_template_id: str, allowed_templates: set[str]) -> str:
    selected = str(value or default_template_id)
    return selected if selected in allowed_templates else default_template_id


def browser_resume_payload(
    resume: Mapping[str, Any] | None,
    *,
    form_fields: Iterable[str],
    default_template_id: str,
    allowed_templates: set[str],
) -> dict[str, Any] | None:
    """生成 IndexedDB 使用的简历副本，并清空联系方式。"""
    if not resume:
        return None
    payload = {
        field_name: (
            ""
            if field_name in BROWSER_SENSITIVE_FIELDS
            else str(resume.get(field_name, "") or "")
        )
        for field_name in form_fields
    }
    payload["template_id"] = _template_id(
        resume.get("template_id"),
        default_template_id,
        allowed_templates,
    )
    payload["photo_base64"] = encode_photo(resume.get("photo_bytes"))
    return payload


def resume_from_browser_payload(
    payload: Mapping[str, Any] | None,
    *,
    form_fields: Iterable[str],
    default_template_id: str,
    allowed_templates: set[str],
    photo_max_bytes: int,
) -> dict[str, Any] | None:
    """校验并恢复 IndexedDB 简历，联系方式始终保持为空。"""
    if not isinstance(payload, Mapping):
        return None
    resume = {
        field_name: (
            ""
            if field_name in BROWSER_SENSITIVE_FIELDS
            else str(payload.get(field_name, "") or "")
        )
        for field_name in form_fields
    }
    resume["template_id"] = _template_id(
        payload.get("template_id"),
        default_template_id,
        allowed_templates,
    )
    resume["photo_bytes"] = decode_photo(
        payload.get("photo_base64"),
        photo_max_bytes,
    )
    return resume


def _version_payload(
    version: Mapping[str, Any],
    *,
    form_fields: Iterable[str],
    default_template_id: str,
    allowed_templates: set[str],
) -> dict[str, Any]:
    return {
        "name": str(version.get("name", "") or ""),
        "resume_data": browser_resume_payload(
            version.get("resume_data"),
            form_fields=form_fields,
            default_template_id=default_template_id,
            allowed_templates=allowed_templates,
        ),
        "optimized_resume": str(version.get("optimized_resume", "") or ""),
        "optimized_resume_editor": str(
            version.get("optimized_resume_editor", "") or ""
        ),
        "optimization_review": strip_forbidden_secrets(
            deepcopy(version.get("optimization_review"))
        ),
        "job_match_result": strip_forbidden_secrets(
            deepcopy(version.get("job_match_result"))
        ),
    }


def build_browser_workspace_snapshot(
    *,
    master_resume: Mapping[str, Any] | None,
    current_resume: Mapping[str, Any] | None,
    job_versions: Mapping[str, Mapping[str, Any]] | None,
    master_results: Mapping[str, Any] | None,
    workspace_mode: str,
    active_job_version_id: str | None,
    form_fields: Iterable[str],
    default_template_id: str,
    allowed_templates: set[str],
) -> dict[str, Any]:
    """构造传给浏览器的白名单数据，绝不读取整个 Session State。"""
    versions = {
        str(version_id): _version_payload(
            version,
            form_fields=form_fields,
            default_template_id=default_template_id,
            allowed_templates=allowed_templates,
        )
        for version_id, version in (job_versions or {}).items()
        if isinstance(version, Mapping)
    }
    active_id = str(active_job_version_id or "")
    mode = "job" if workspace_mode == "job" and active_id in versions else "master"
    return {
        "schema_version": BROWSER_SCHEMA_VERSION,
        "master_resume": browser_resume_payload(
            master_resume,
            form_fields=form_fields,
            default_template_id=default_template_id,
            allowed_templates=allowed_templates,
        ),
        "job_versions": versions,
        "master_results": strip_forbidden_secrets(deepcopy(master_results or {})),
        "workspace_mode": mode,
        "active_job_version_id": active_id if mode == "job" else None,
        "draft": browser_resume_payload(
            current_resume,
            form_fields=form_fields,
            default_template_id=default_template_id,
            allowed_templates=allowed_templates,
        ),
        "draft_context": {
            "workspace_mode": mode,
            "active_job_version_id": active_id if mode == "job" else None,
        },
        "draft_captured": False,
    }


def _restore_version(
    version: Mapping[str, Any],
    *,
    form_fields: Iterable[str],
    default_template_id: str,
    allowed_templates: set[str],
    photo_max_bytes: int,
) -> dict[str, Any] | None:
    resume = resume_from_browser_payload(
        version.get("resume_data"),
        form_fields=form_fields,
        default_template_id=default_template_id,
        allowed_templates=allowed_templates,
        photo_max_bytes=photo_max_bytes,
    )
    if resume is None:
        return None
    return {
        "name": str(version.get("name", "") or "未命名版本")[:120],
        "resume_data": resume,
        "optimized_resume": str(version.get("optimized_resume", "") or ""),
        "optimized_resume_editor": str(
            version.get("optimized_resume_editor", "") or ""
        ),
        "optimization_review": strip_forbidden_secrets(
            deepcopy(version.get("optimization_review"))
        ),
        "job_match_result": strip_forbidden_secrets(
            deepcopy(version.get("job_match_result"))
        ),
    }


def restore_browser_workspace_snapshot(
    payload: Mapping[str, Any] | None,
    *,
    form_fields: Iterable[str],
    default_template_id: str,
    allowed_templates: set[str],
    photo_max_bytes: int,
) -> dict[str, Any] | None:
    """把浏览器记录恢复成可安装到 Session State 的工作区。"""
    if not isinstance(payload, Mapping):
        return None
    if payload.get("schema_version") != BROWSER_SCHEMA_VERSION:
        return None

    master = resume_from_browser_payload(
        payload.get("master_resume"),
        form_fields=form_fields,
        default_template_id=default_template_id,
        allowed_templates=allowed_templates,
        photo_max_bytes=photo_max_bytes,
    )
    versions: dict[str, dict[str, Any]] = {}
    raw_versions = payload.get("job_versions")
    if isinstance(raw_versions, Mapping):
        for version_id, version in list(raw_versions.items())[:100]:
            if not isinstance(version, Mapping):
                continue
            restored = _restore_version(
                version,
                form_fields=form_fields,
                default_template_id=default_template_id,
                allowed_templates=allowed_templates,
                photo_max_bytes=photo_max_bytes,
            )
            if restored is not None:
                versions[str(version_id)[:120]] = restored

    active_id = str(payload.get("active_job_version_id") or "")
    mode = "job" if payload.get("workspace_mode") == "job" and active_id in versions else "master"
    draft = resume_from_browser_payload(
        payload.get("draft"),
        form_fields=form_fields,
        default_template_id=default_template_id,
        allowed_templates=allowed_templates,
        photo_max_bytes=photo_max_bytes,
    )
    draft_captured = payload.get("draft_captured") is True and draft is not None
    unsaved_draft = None

    if draft_captured and mode == "job" and active_id in versions:
        versions[active_id]["resume_data"] = draft
    elif draft_captured and master is not None:
        master = draft
    elif draft_captured:
        unsaved_draft = draft

    current_resume = (
        versions[active_id]["resume_data"]
        if mode == "job" and active_id in versions
        else master
    )
    if master is None and not versions and unsaved_draft is None:
        return None
    return {
        "master_resume_data": master,
        "job_versions": versions,
        "master_results": strip_forbidden_secrets(
            deepcopy(payload.get("master_results") or {})
        ),
        "resume_workspace_mode": mode,
        "active_job_version_id": active_id if mode == "job" else None,
        "resume_data": current_resume,
        "unsaved_draft": unsaved_draft,
        "saved_at": str(payload.get("saved_at", "") or ""),
    }


def _json_resume(
    payload: Mapping[str, Any],
    *,
    form_fields: Iterable[str],
    default_template_id: str,
    allowed_templates: set[str],
) -> dict[str, Any]:
    resume = {
        field_name: str(payload.get(field_name, "") or "")
        for field_name in form_fields
    }
    resume["template_id"] = _template_id(
        payload.get("template_id"),
        default_template_id,
        allowed_templates,
    )
    resume["photo_bytes"] = None
    return resume


def parse_json_resume_backup(
    raw_backup: bytes | str,
    *,
    form_fields: Iterable[str],
    default_template_id: str,
    allowed_templates: set[str],
    max_bytes: int = 5 * 1024 * 1024,
) -> dict[str, Any]:
    """读取单份或全部版本 JSON 备份；JSON 备份不包含证件照。"""
    encoded = raw_backup if isinstance(raw_backup, bytes) else raw_backup.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("JSON 备份超过 5MB，请检查文件是否正确。")
    try:
        payload = json.loads(encoded.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("无法读取 JSON 备份，请选择本应用导出的 JSON 文件。") from error
    if not isinstance(payload, Mapping):
        raise ValueError("JSON 备份格式不正确。")

    if isinstance(payload.get("master_resume"), Mapping):
        master = _json_resume(
            payload["master_resume"],
            form_fields=form_fields,
            default_template_id=default_template_id,
            allowed_templates=allowed_templates,
        )
        versions: dict[str, dict[str, Any]] = {}
        raw_versions = payload.get("job_versions", [])
        if isinstance(raw_versions, list):
            for item in raw_versions[:100]:
                if not isinstance(item, Mapping) or not isinstance(item.get("resume_data"), Mapping):
                    continue
                version_id = str(item.get("id") or "")[:120]
                if not version_id:
                    continue
                versions[version_id] = {
                    "name": str(item.get("name", "") or "未命名版本")[:120],
                    "resume_data": _json_resume(
                        item["resume_data"],
                        form_fields=form_fields,
                        default_template_id=default_template_id,
                        allowed_templates=allowed_templates,
                    ),
                    "optimized_resume": str(item.get("optimized_resume", "") or ""),
                    "optimized_resume_editor": str(
                        item.get("optimized_resume_editor", "") or ""
                    ),
                    "optimization_review": strip_forbidden_secrets(
                        deepcopy(item.get("optimization_review"))
                    ),
                    "job_match_result": strip_forbidden_secrets(
                        deepcopy(item.get("job_match_result"))
                    ),
                }
    else:
        master = _json_resume(
            payload,
            form_fields=form_fields,
            default_template_id=default_template_id,
            allowed_templates=allowed_templates,
        )
        versions = {}

    return {
        "master_resume_data": master,
        "job_versions": versions,
        "resume_workspace_mode": "master",
        "active_job_version_id": None,
        "resume_data": master,
    }
