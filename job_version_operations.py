"""岗位版本重命名、复制和删除的纯数据操作。"""

from __future__ import annotations

from copy import deepcopy


def job_version_name_exists(
    versions: dict,
    version_name: str,
    excluding_id: str | None = None,
) -> bool:
    """判断名称是否与其他岗位版本重复。"""
    normalized_name = version_name.strip().casefold()
    return any(
        version_id != excluding_id
        and str(version.get("name", "")).strip().casefold() == normalized_name
        for version_id, version in versions.items()
    )


def unique_job_version_name(versions: dict, base_name: str) -> str:
    """为复制版本生成不重复且易读的名称。"""
    clean_base_name = base_name.strip() or "未命名版本"
    candidate = f"{clean_base_name} 副本"
    suffix = 2
    while job_version_name_exists(versions, candidate):
        candidate = f"{clean_base_name} 副本 {suffix}"
        suffix += 1
    return candidate


def rename_job_version(versions: dict, version_id: str, new_name: str) -> bool:
    """修改岗位版本名称；成功返回 True。"""
    version = versions.get(version_id)
    if version is None:
        return False
    version["name"] = new_name.strip()
    return True


def duplicate_job_version(
    versions: dict,
    source_version_id: str,
    new_version_id: str,
    copy_name: str,
) -> dict | None:
    """深度复制岗位版本并加入集合。"""
    source_version = versions.get(source_version_id)
    if source_version is None:
        return None
    copied_version = deepcopy(source_version)
    copied_version["name"] = copy_name.strip()
    versions[new_version_id] = copied_version
    return copied_version


def remove_job_version(versions: dict, version_id: str) -> dict | None:
    """从集合删除岗位版本并返回被删除的数据。"""
    return versions.pop(version_id, None)
