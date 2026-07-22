"""AI 简历助手的网页入口。"""

from base64 import b64encode
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv

from resume_import import extract_resume_text_with_details, parse_resume_text
from resume_review import build_reviewed_resume_text, text_diff_fragments
from resume_template import (
    DEFAULT_TEMPLATE_ID,
    clean_resume_text,
    create_resume_document,
    create_resume_pdf,
    create_resume_preview_html,
    resume_template_description,
    resume_template_options,
)


def _load_ai_service_from_file():
    """从确切文件路径加载模块，避免 Streamlit 使用旧模块缓存。"""
    service_path = Path(__file__).with_name("ai_service.py")
    spec = spec_from_file_location("resume_assistant_ai_service", service_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 AI 服务文件：{service_path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ai_service = _load_ai_service_from_file()
DEFAULT_MODEL = _ai_service.DEFAULT_MODEL
ResumeOptimizationError = _ai_service.ResumeOptimizationError
analyze_job_match = _ai_service.analyze_job_match
classify_imported_resume = _ai_service.classify_imported_resume
optimize_resume_changes = _ai_service.optimize_resume_changes
test_deepseek_connection = _ai_service.test_deepseek_connection


load_dotenv()

APP_DIR = Path(__file__).resolve().parent
FOX_HERO_IMAGE = APP_DIR / "assets" / "fox-resume-consultant.webp"

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
AI_RELEVANT_FIELDS = (
    "target_role",
    "job_description",
    "summary",
    "education",
    "work_experience",
    "project_experience",
    "skills",
)
PHOTO_MAX_BYTES = 5 * 1024 * 1024
RESUME_IMPORT_MAX_BYTES = 10 * 1024 * 1024
IMPORT_FIELDS = (
    "name",
    "phone",
    "email",
    "city",
    "target_role",
    "summary",
    "education",
    "work_experience",
    "project_experience",
    "skills",
)
AI_IMPORT_FIELDS = (
    "target_role",
    "summary",
    "education",
    "work_experience",
    "project_experience",
    "skills",
)


def _copy_resume_data(resume: dict | None) -> dict:
    """复制一份可独立编辑的简历数据。"""
    source = resume or {}
    copied = {
        field_name: str(source.get(field_name, "") or "")
        for field_name in FORM_FIELDS
    }
    copied["photo_bytes"] = source.get("photo_bytes")
    template_options = resume_template_options()
    selected_template = str(source.get("template_id", DEFAULT_TEMPLATE_ID))
    copied["template_id"] = (
        selected_template if selected_template in template_options else DEFAULT_TEMPLATE_ID
    )
    return copied


def _initialize_resume_workspace() -> None:
    """兼容旧会话，并初始化主简历与岗位版本工作区。"""
    st.session_state.setdefault("job_versions", {})

    current_resume = st.session_state.get("resume_data")
    master_resume = st.session_state.get("master_resume_data")
    if not master_resume and current_resume:
        st.session_state["master_resume_data"] = _copy_resume_data(current_resume)
        master_resume = st.session_state["master_resume_data"]
    if not current_resume and master_resume:
        st.session_state["resume_data"] = _copy_resume_data(master_resume)

    st.session_state.setdefault("resume_workspace_mode", "master")
    st.session_state.setdefault("active_job_version_id", None)
    active_version_id = st.session_state.get("active_job_version_id")
    if (
        st.session_state.get("resume_workspace_mode") == "job"
        and active_version_id not in st.session_state["job_versions"]
    ):
        st.session_state["resume_workspace_mode"] = "master"
        st.session_state["active_job_version_id"] = None


def _clear_derived_resume_state() -> None:
    """清空只属于某个简历版本的 AI 结果。"""
    st.session_state.pop("optimized_resume", None)
    st.session_state.pop("optimized_resume_editor", None)
    st.session_state.pop("optimization_review", None)
    st.session_state.pop("job_match_result", None)


def _active_job_version() -> dict | None:
    """返回当前岗位版本；主简历模式下返回空。"""
    if st.session_state.get("resume_workspace_mode") != "job":
        return None
    version_id = st.session_state.get("active_job_version_id")
    return st.session_state.get("job_versions", {}).get(version_id)


def _persist_active_job_version() -> None:
    """把当前表单与 AI 结果保存回正在编辑的岗位版本。"""
    version = _active_job_version()
    if version is None:
        return

    current_resume = st.session_state.get("resume_data")
    if current_resume:
        version["resume_data"] = _copy_resume_data(current_resume)
    version["optimized_resume"] = st.session_state.get("optimized_resume", "")
    version["optimized_resume_editor"] = st.session_state.get(
        "optimized_resume_editor",
        "",
    )
    version["optimization_review"] = deepcopy(
        st.session_state.get("optimization_review")
    )
    version["job_match_result"] = st.session_state.get("job_match_result")


def _save_resume_to_current_workspace(resume: dict) -> bool:
    """按提交瞬间的工作区状态保存，并返回是否保存为岗位版本。"""
    saved_resume = _copy_resume_data(resume)
    st.session_state["resume_data"] = saved_resume

    if st.session_state.get("resume_workspace_mode") == "job":
        version_id = st.session_state.get("active_job_version_id")
        version = st.session_state.get("job_versions", {}).get(version_id)
        if version is not None:
            version["resume_data"] = _copy_resume_data(saved_resume)
            version["optimized_resume"] = st.session_state.get(
                "optimized_resume",
                "",
            )
            version["optimized_resume_editor"] = st.session_state.get(
                "optimized_resume_editor",
                "",
            )
            version["optimization_review"] = deepcopy(
                st.session_state.get("optimization_review")
            )
            version["job_match_result"] = st.session_state.get("job_match_result")
            return True

    st.session_state["resume_workspace_mode"] = "master"
    st.session_state["active_job_version_id"] = None
    st.session_state["master_resume_data"] = _copy_resume_data(saved_resume)
    return False


def _restore_version_results(version: dict) -> None:
    """切换岗位版本时恢复该版本自己的优化和匹配结果。"""
    _clear_derived_resume_state()
    optimized_resume = str(version.get("optimized_resume", "") or "")
    optimized_editor = str(version.get("optimized_resume_editor", "") or "")
    if optimized_resume:
        st.session_state["optimized_resume"] = optimized_resume
    if optimized_editor:
        st.session_state["optimized_resume_editor"] = optimized_editor
    optimization_review = version.get("optimization_review")
    if optimization_review:
        st.session_state["optimization_review"] = deepcopy(optimization_review)
    if version.get("job_match_result"):
        st.session_state["job_match_result"] = version["job_match_result"]


def _initialize_form_state() -> None:
    """将已保存资料稳定回填到可编辑表单。"""
    saved_resume = st.session_state.get("resume_data", {})
    for field_name in FORM_FIELDS:
        st.session_state.setdefault(
            f"form_{field_name}",
            saved_resume.get(field_name, ""),
        )


def _clear_job_match_result() -> None:
    """简历正文被手动修改后，清除已经过期的 JD 匹配结果。"""
    st.session_state.pop("job_match_result", None)
    _persist_active_job_version()


def _load_resume_into_form(resume: dict) -> None:
    """把确认后的主简历同步到下面的可编辑表单。"""
    for field_name in FORM_FIELDS:
        st.session_state[f"form_{field_name}"] = resume.get(field_name, "")


def _activate_master_resume() -> None:
    """切换到主简历，不修改任何岗位版本。"""
    _persist_active_job_version()
    master_resume = st.session_state.get("master_resume_data")
    if not master_resume:
        st.warning("请先保存一份主简历。")
        return
    st.session_state["resume_workspace_mode"] = "master"
    st.session_state["active_job_version_id"] = None
    st.session_state["resume_data"] = _copy_resume_data(master_resume)
    _load_resume_into_form(st.session_state["resume_data"])
    _clear_derived_resume_state()
    st.session_state["resume_workspace_notice"] = "已切换到主简历。"
    st.rerun()


def _activate_job_version(version_id: str) -> None:
    """切换到指定岗位版本，并恢复该版本自己的结果。"""
    version = st.session_state.get("job_versions", {}).get(version_id)
    if not version:
        st.warning("找不到这个岗位版本，可能已经被清理。")
        return
    _persist_active_job_version()
    st.session_state["resume_workspace_mode"] = "job"
    st.session_state["active_job_version_id"] = version_id
    st.session_state["pending_job_version_selector"] = version_id
    st.session_state["resume_data"] = _copy_resume_data(version.get("resume_data"))
    _load_resume_into_form(st.session_state["resume_data"])
    _restore_version_results(version)
    st.session_state["resume_workspace_notice"] = (
        f"已切换到岗位版本“{version.get('name', '未命名版本')}”。"
    )
    st.rerun()


def _create_job_version(version_name: str, target_role: str, job_description: str) -> None:
    """从主简历复制出一个独立的岗位版本。"""
    master_resume = st.session_state.get("master_resume_data")
    if not master_resume:
        st.warning("请先保存主简历，再创建岗位版本。")
        return

    _persist_active_job_version()
    version_id = uuid4().hex[:12]
    version_resume = _copy_resume_data(master_resume)
    version_resume["target_role"] = target_role.strip()
    version_resume["job_description"] = job_description.strip()
    st.session_state["job_versions"][version_id] = {
        "name": version_name.strip(),
        "resume_data": version_resume,
        "optimized_resume": "",
        "optimized_resume_editor": "",
        "optimization_review": None,
        "job_match_result": None,
    }
    _activate_job_version(version_id)


def _resume_library_json() -> bytes:
    """生成包含主简历和所有岗位版本的文字备份，不包含证件照二进制。"""
    _persist_active_job_version()
    versions = []
    for version_id, version in st.session_state.get("job_versions", {}).items():
        versions.append(
            {
                "id": version_id,
                "name": version.get("name", ""),
                "resume_data": {
                    field_name: version.get("resume_data", {}).get(field_name, "")
                    for field_name in FORM_FIELDS
                }
                | {
                    "template_id": version.get("resume_data", {}).get(
                        "template_id", DEFAULT_TEMPLATE_ID
                    )
                },
                "optimized_resume": version.get("optimized_resume", ""),
                "optimized_resume_editor": version.get(
                    "optimized_resume_editor",
                    "",
                ),
                "optimization_review": version.get("optimization_review"),
                "job_match_result": version.get("job_match_result"),
            }
        )
    backup = {
        "schema_version": 2,
        "master_resume": {
            field_name: st.session_state.get("master_resume_data", {}).get(field_name, "")
            for field_name in FORM_FIELDS
        }
        | {
            "template_id": st.session_state.get("master_resume_data", {}).get(
                "template_id", DEFAULT_TEMPLATE_ID
            )
        },
        "job_versions": versions,
    }
    return json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8")


def _handle_optimized_editor_change() -> None:
    """用户手动改了 AI 结果后，清除旧匹配并保存当前岗位版本。"""
    st.session_state.pop("job_match_result", None)
    _persist_active_job_version()


def _refresh_reviewed_resume() -> str:
    """按照当前逐条审阅决定重新生成最终采用稿。"""
    review = st.session_state.get("optimization_review") or {}
    final_text = clean_resume_text(
        build_reviewed_resume_text(
            st.session_state.get("resume_data", {}),
            review.get("changes", []),
            review.get("decisions", {}),
        )
    )
    st.session_state["optimized_resume"] = final_text
    st.session_state["optimized_resume_editor"] = final_text
    return final_text


def _install_optimization_review(changes: list[dict]) -> None:
    """安装一组新的 AI 修改建议，默认全部处于待审阅状态。"""
    st.session_state["optimization_review"] = {
        "changes": deepcopy(changes),
        "decisions": {change["id"]: "pending" for change in changes},
    }
    _refresh_reviewed_resume()
    st.session_state.pop("job_match_result", None)
    _persist_active_job_version()


def _set_review_decision(change_id: str, decision: str) -> None:
    """接受或拒绝单条建议，并同步更新导出用的最终稿。"""
    if decision not in {"accepted", "rejected"}:
        return
    review = st.session_state.get("optimization_review") or {}
    valid_ids = {
        str(change.get("id", ""))
        for change in review.get("changes", [])
        if change.get("id")
    }
    if change_id not in valid_ids:
        return
    review.setdefault("decisions", {})[change_id] = decision
    st.session_state["optimization_review"] = review
    _refresh_reviewed_resume()
    _persist_active_job_version()


def _format_diff_fragments(fragments: list[str]) -> str:
    cleaned = [fragment.strip() for fragment in fragments if fragment.strip()]
    return "、".join(f"“{fragment}”" for fragment in cleaned) if cleaned else "无"


def _render_optimization_review(review: dict) -> None:
    """显示逐条原文对比，并提供独立接受和拒绝操作。"""
    changes = review.get("changes", [])
    decisions = review.get("decisions", {})
    if not changes:
        return

    accepted_count = sum(
        decisions.get(change.get("id")) == "accepted" for change in changes
    )
    rejected_count = sum(
        decisions.get(change.get("id")) == "rejected" for change in changes
    )
    pending_count = len(changes) - accepted_count - rejected_count

    st.divider()
    st.subheader("AI 修改对比")
    st.warning("AI 不得编造公司、项目、技能、证书和业绩数字。")
    st.caption(
        f"共 {len(changes)} 条建议，已接受 {accepted_count} 条，"
        f"已拒绝 {rejected_count} 条，待审阅 {pending_count} 条。"
        "只有明确接受的修改才会进入导出稿。"
    )

    status_labels = {
        "pending": "待审阅",
        "accepted": "已接受",
        "rejected": "已拒绝",
    }
    for index, change in enumerate(changes, start=1):
        change_id = str(change.get("id", f"change-{index}"))
        decision = decisions.get(change_id, "pending")
        with st.container(border=True):
            st.markdown(f"#### {change.get('section', '简历内容')}：第 {index} 条")
            st.caption(f"当前状态：{status_labels.get(decision, '待审阅')}")

            original_column, revised_column = st.columns(2)
            with original_column:
                st.markdown("**原文**")
                st.write(change.get("original_text", ""))
            with revised_column:
                st.markdown("**AI 修改后**")
                st.write(change.get("revised_text", ""))

            diff = text_diff_fragments(
                str(change.get("original_text", "")),
                str(change.get("revised_text", "")),
            )
            added_column, removed_column = st.columns(2)
            with added_column:
                st.markdown("**新增了什么**")
                st.write(_format_diff_fragments(diff["added"]))
            with removed_column:
                st.markdown("**删除了什么**")
                st.write(_format_diff_fragments(diff["removed"]))

            st.markdown("**修改原因**")
            st.write(change.get("reason", "未提供修改原因。"))

            accept_column, reject_column = st.columns(2)
            with accept_column:
                accept_change = st.button(
                    "接受这一条",
                    key=f"accept_review_{change_id}",
                    type="primary",
                    disabled=decision == "accepted",
                    use_container_width=True,
                )
            with reject_column:
                reject_change = st.button(
                    "拒绝这一条",
                    key=f"reject_review_{change_id}",
                    disabled=decision == "rejected",
                    use_container_width=True,
                )

            if accept_change:
                _set_review_decision(change_id, "accepted")
                st.rerun()
            if reject_change:
                _set_review_decision(change_id, "rejected")
                st.rerun()

    if pending_count == 0:
        st.success("所有建议都已审阅，导出将使用下面的当前采用稿。")
    else:
        st.info("未审阅的建议暂不采用，你可以稍后继续决定。")

    with st.expander("查看当前采用稿", expanded=pending_count == 0):
        st.text_area(
            "当前采用稿（可继续修改）",
            height=500,
            key="optimized_resume_editor",
            on_change=_handle_optimized_editor_change,
        )
        st.caption("如果再次更改接受或拒绝状态，系统会按最新决定重新合并这里的内容。")


def _inject_brand_styles() -> None:
    """注入黑色编辑室主题，不改变 Streamlit 组件的业务行为。"""
    st.markdown(
        """
        <style>
        :root {
            color-scheme: dark;
            --fox-bg: #090909;
            --fox-surface: #121211;
            --fox-surface-raised: #191816;
            --fox-line: #2d2925;
            --fox-line-strong: #453a31;
            --fox-text: #f3eee8;
            --fox-text-soft: #c7beb5;
            --fox-text-muted: #8f8881;
            --fox-accent: #df812f;
            --fox-accent-bright: #f09a4c;
            --fox-accent-ink: #1a0e05;
            --fox-success: #72aa86;
            --fox-danger: #d77b70;
            --fox-radius-card: 16px;
            --fox-radius-control: 10px;
        }

        html,
        body,
        [data-testid="stAppViewContainer"],
        .stApp {
            background: var(--fox-bg);
            color: var(--fox-text);
            font-family: "Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI", sans-serif;
        }

        [data-testid="stAppViewContainer"] {
            background-image:
                radial-gradient(circle at 82% 2%, rgba(223, 129, 47, 0.08), transparent 25rem),
                linear-gradient(rgba(255,255,255,0.014) 1px, transparent 1px);
            background-size: auto, 100% 44px;
        }

        [data-testid="stHeader"] {
            background: rgba(9, 9, 9, 0.82);
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            backdrop-filter: blur(16px);
        }

        [data-testid="stAppDeployButton"],
        [data-testid="stMainMenu"] {
            display: none;
        }

        [data-testid="stAppViewBlockContainer"] {
            width: min(100%, 1240px);
            max-width: 1240px;
            padding: 1.35rem 2.2rem 6rem;
        }

        [data-testid="stSidebar"] {
            background: #0e0e0d;
            border-right: 1px solid var(--fox-line);
        }

        [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            padding-top: 1.5rem;
        }

        [data-testid="stSidebar"] h2 {
            font-size: 1.1rem;
            letter-spacing: -0.02em;
        }

        h1, h2, h3, h4 {
            color: var(--fox-text);
            letter-spacing: -0.035em;
        }

        h2, h3 {
            margin-top: 0.45rem;
        }

        p, label, [data-testid="stCaptionContainer"] {
            color: var(--fox-text-soft);
        }

        a {
            color: var(--fox-accent-bright);
        }

        hr {
            border-color: var(--fox-line) !important;
            margin: 2.8rem 0 !important;
        }

        footer {
            visibility: hidden;
        }

        .fox-hero {
            position: relative;
            display: grid;
            grid-template-columns: minmax(0, 1.05fr) minmax(360px, 0.95fr);
            min-height: min(680px, calc(100dvh - 7rem));
            overflow: hidden;
            margin: 0 0 1.15rem;
            border: 1px solid var(--fox-line);
            border-radius: var(--fox-radius-card);
            background:
                linear-gradient(122deg, rgba(25, 24, 22, 0.98) 0%, rgba(13, 13, 12, 0.96) 55%, rgba(35, 24, 16, 0.92) 100%);
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,0.045),
                0 30px 80px rgba(0,0,0,0.28);
        }

        .fox-hero::before {
            content: "";
            position: absolute;
            inset: 0;
            pointer-events: none;
            background:
                linear-gradient(90deg, transparent 49.8%, rgba(255,255,255,0.055) 50%, transparent 50.2%),
                radial-gradient(circle at 72% 42%, rgba(223,129,47,0.11), transparent 28%);
        }

        .fox-hero-copy {
            position: relative;
            z-index: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
            padding: clamp(2.4rem, 6vw, 5.4rem);
        }

        .fox-kicker {
            display: inline-flex;
            width: fit-content;
            margin-bottom: 1.35rem;
            padding: 0.5rem 0.72rem;
            border: 1px solid rgba(240,154,76,0.28);
            border-radius: 999px;
            background: rgba(223,129,47,0.09);
            color: #efb077;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.04em;
        }

        .fox-hero h1 {
            max-width: none;
            margin: 0;
            color: var(--fox-text);
            font-size: clamp(3.15rem, 4.6vw, 5.1rem);
            font-weight: 760;
            line-height: 1;
            letter-spacing: -0.068em;
        }

        .fox-hero h1 span {
            display: block;
            color: var(--fox-accent-bright);
        }

        .fox-hero h1 .fox-headline-base {
            color: var(--fox-text);
        }

        .fox-hero-copy > p {
            max-width: 32rem;
            margin: 1.65rem 0 0;
            color: var(--fox-text-soft);
            font-size: clamp(1rem, 1.5vw, 1.15rem);
            line-height: 1.8;
        }

        .fox-hero-actions {
            display: flex;
            align-items: center;
            gap: 1.15rem;
            margin-top: 2rem;
        }

        .fox-hero-actions a {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 46px;
            padding: 0 1.15rem;
            border-radius: var(--fox-radius-control);
            background: var(--fox-accent);
            color: var(--fox-accent-ink) !important;
            font-weight: 800;
            text-decoration: none;
            transition: transform 180ms ease, background-color 180ms ease;
        }

        .fox-hero-actions a:hover {
            background: var(--fox-accent-bright);
            transform: translateY(-2px);
        }

        .fox-presence {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            color: var(--fox-text-muted);
            font-size: 0.86rem;
        }

        .fox-presence i {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--fox-success);
            box-shadow: 0 0 0 4px rgba(114,170,134,0.1);
        }

        .fox-hero-visual {
            position: relative;
            min-height: 520px;
            overflow: hidden;
        }

        .fox-hero-visual img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            object-position: 61% center;
            filter: saturate(0.94) contrast(1.03);
            animation: fox-reveal 820ms cubic-bezier(0.16, 1, 0.3, 1) both;
        }

        .fox-hero-visual::after {
            content: "";
            position: absolute;
            inset: 0;
            pointer-events: none;
            background: linear-gradient(90deg, #151412 0%, transparent 22%, transparent 80%, rgba(9,9,9,0.2) 100%);
        }

        .privacy-note {
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 0.8rem 1.1rem;
            align-items: center;
            margin: 0 0 2.25rem;
            padding: 1rem 1.2rem;
            border: 1px solid var(--fox-line);
            border-radius: var(--fox-radius-card);
            background: rgba(18,18,17,0.86);
        }

        .privacy-note strong {
            color: #efb077;
            font-size: 0.86rem;
        }

        .privacy-note span {
            color: var(--fox-text-muted);
            font-size: 0.9rem;
            line-height: 1.55;
        }

        [data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--fox-line) !important;
            border-radius: var(--fox-radius-card) !important;
            background: rgba(18,18,17,0.92);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.025);
        }

        [data-testid="stForm"] {
            padding: clamp(1.2rem, 3vw, 2rem);
            border: 1px solid var(--fox-line);
            border-radius: var(--fox-radius-card);
            background: rgba(18,18,17,0.88);
        }

        [data-testid="stExpander"] details {
            overflow: hidden;
            border: 1px solid var(--fox-line) !important;
            border-radius: var(--fox-radius-card) !important;
            background: rgba(18,18,17,0.88);
        }

        [data-testid="stExpander"] summary:hover {
            background: rgba(223,129,47,0.055);
        }

        [data-testid="stTextInput"] input,
        [data-testid="stTextArea"] textarea,
        [data-baseweb="select"] > div {
            border-color: var(--fox-line-strong) !important;
            border-radius: var(--fox-radius-control) !important;
            background: #0c0c0b !important;
            color: var(--fox-text) !important;
            box-shadow: none !important;
        }

        [data-testid="stTextInput"] input:focus,
        [data-testid="stTextArea"] textarea:focus {
            border-color: var(--fox-accent) !important;
            box-shadow: 0 0 0 3px rgba(223,129,47,0.14) !important;
        }

        [data-testid="stFileUploader"] section {
            border-color: var(--fox-line-strong);
            border-radius: var(--fox-radius-control);
            background: #0d0d0c;
        }

        [data-testid="stBaseButton-primary"] {
            border: 1px solid var(--fox-accent-bright) !important;
            border-radius: var(--fox-radius-control) !important;
            background: var(--fox-accent) !important;
            color: var(--fox-accent-ink) !important;
            font-weight: 800 !important;
            box-shadow: none !important;
        }

        [data-testid="stBaseButton-primary"]:hover {
            background: var(--fox-accent-bright) !important;
            transform: translateY(-1px);
        }

        [data-testid="stBaseButton-secondary"] {
            border: 1px solid var(--fox-line-strong) !important;
            border-radius: var(--fox-radius-control) !important;
            background: var(--fox-surface-raised) !important;
            color: var(--fox-text) !important;
            font-weight: 700 !important;
        }

        [data-testid="stBaseButton-secondary"]:hover {
            border-color: #6a4a32 !important;
            background: #211b17 !important;
            color: var(--fox-text) !important;
            transform: translateY(-1px);
        }

        [data-testid="stBaseButton-primary"],
        [data-testid="stBaseButton-secondary"] {
            min-height: 42px;
            transition: transform 160ms ease, background-color 160ms ease, border-color 160ms ease;
        }

        [data-testid="stBaseButton-primary"]:active,
        [data-testid="stBaseButton-secondary"]:active {
            transform: scale(0.985);
        }

        [data-testid="stAlert"] {
            border: 1px solid var(--fox-line);
            border-radius: var(--fox-radius-control);
            background: #141311 !important;
        }

        [data-testid="stAlert"] > div {
            background: transparent !important;
        }

        [data-testid="stMetric"] {
            padding: 1rem 1.1rem;
            border: 1px solid var(--fox-line);
            border-radius: var(--fox-radius-card);
            background: var(--fox-surface);
        }

        [data-testid="stProgress"] > div > div {
            background: var(--fox-accent) !important;
        }

        [data-testid="stImage"] img {
            border: 1px solid var(--fox-line);
            border-radius: var(--fox-radius-control);
        }

        @keyframes fox-reveal {
            from { opacity: 0; transform: scale(1.035) translateX(18px); }
            to { opacity: 1; transform: scale(1) translateX(0); }
        }

        @media (max-width: 900px) {
            [data-testid="stAppViewBlockContainer"] {
                padding: 1rem 1rem 4rem;
            }

            .fox-hero {
                grid-template-columns: 1fr;
                min-height: auto;
            }

            .fox-hero::before {
                display: none;
            }

            .fox-hero-copy {
                padding: 2.4rem 1.5rem 1.8rem;
            }

            .fox-hero h1 {
                max-width: none;
                font-size: clamp(2.75rem, 12vw, 4.2rem);
            }

            .fox-hero-visual {
                min-height: 390px;
            }

            .fox-hero-visual::after {
                background: linear-gradient(180deg, #151412 0%, transparent 24%, transparent 100%);
            }
        }

        @media (max-width: 520px) {
            .fox-hero-actions {
                align-items: flex-start;
                flex-direction: column;
            }

            .privacy-note {
                grid-template-columns: 1fr;
            }
        }

        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                scroll-behavior: auto !important;
                animation-duration: 0.01ms !important;
                animation-iteration-count: 1 !important;
                transition-duration: 0.01ms !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    """渲染应用首页的狐狸顾问主视觉。"""
    image_markup = ""
    if FOX_HERO_IMAGE.exists():
        encoded_image = b64encode(FOX_HERO_IMAGE.read_bytes()).decode("ascii")
        image_markup = (
            '<div class="fox-hero-visual">'
            f'<img src="data:image/webp;base64,{encoded_image}" '
            'alt="穿黑色西装的狐狸简历优化师">'
            "</div>"
        )

    st.markdown(
        f"""
        <section class="fox-hero" aria-label="AI 简历助手介绍">
            <div class="fox-hero-copy">
                <span class="fox-kicker">你的 AI 简历优化师</span>
                <h1><span class="fox-headline-base">把经历写成</span><span>你的优势</span></h1>
                <p>导入、匹配、优化和导出，都在一个安静专业的工作台完成。</p>
                <div class="fox-hero-actions">
                    <a href="#resume-workspace">开始整理</a>
                    <span class="fox-presence"><i></i>狐狸顾问已就位</span>
                </div>
            </div>
            {image_markup}
        </section>
        <div class="privacy-note">
            <strong>隐私保护</strong>
            <span>本地识别不会发送资料。只有你主动同意 AI 精准识别时，系统才会发送已隐藏联系方式的文本。</span>
        </div>
        <div id="resume-workspace"></div>
        """,
        unsafe_allow_html=True,
    )


@st.dialog("新建岗位版本", width="large")
def _show_new_job_version_dialog() -> None:
    """填写岗位信息，并从主简历创建一个独立副本。"""
    master_resume = st.session_state.get("master_resume_data", {})
    st.write("岗位版本会复制主简历；之后修改和 AI 优化只影响这个岗位版本。")
    with st.form("new_job_version_form"):
        version_name = st.text_input(
            "版本名称 *",
            placeholder="例如：字节跳动产品经理",
        )
        target_role = st.text_input(
            "目标岗位 *",
            value=master_resume.get("target_role", ""),
            placeholder="例如：产品经理",
        )
        job_description = st.text_area(
            "岗位 JD",
            value=master_resume.get("job_description", ""),
            height=180,
            placeholder="粘贴招聘网站上的岗位职责和任职要求。",
        )
        create_version = st.form_submit_button(
            "创建并打开岗位版本",
            type="primary",
            use_container_width=True,
        )

    if create_version:
        if not version_name.strip() or not target_role.strip():
            st.warning("请填写版本名称和目标岗位。")
            return
        _create_job_version(version_name, target_role, job_description)


def _master_resume_json(resume: dict) -> bytes:
    """生成不含照片二进制数据的本地主简历备份。"""
    backup = {
        field_name: resume.get(field_name, "")
        for field_name in FORM_FIELDS
    }
    backup["template_id"] = resume.get("template_id", DEFAULT_TEMPLATE_ID)
    return json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8")


def _merge_ai_import_result(local_draft: dict, ai_result: dict) -> dict:
    """保留本地识别的联系方式，用 AI 结果替换经历章节。"""
    merged = local_draft.copy()
    for field_name in AI_IMPORT_FIELDS:
        ai_value = str(ai_result.get(field_name, "")).strip()
        if ai_value:
            merged[field_name] = ai_value
    merged["unclassified_text"] = str(ai_result.get("unclassified", "")).strip()
    merged["ai_assisted"] = True
    merged["parse_warnings"] = []
    merged["parse_quality"] = "AI 辅助"
    return merged


def _recognize_imported_file(
    imported_file,
    use_ai: bool,
    api_key: str,
) -> dict:
    """读取上传文件；按用户选择执行本地或 AI 章节归类。"""
    extraction = extract_resume_text_with_details(
        imported_file.getvalue(),
        imported_file.name,
    )
    imported_text = extraction.text
    imported_draft = parse_resume_text(imported_text)
    imported_draft["extraction_method"] = extraction.method
    imported_draft["ocr_used"] = extraction.ocr_used
    imported_draft["ocr_language"] = extraction.ocr_language
    if not use_ai:
        return imported_draft

    sensitive_values = [
        imported_draft.get(field_name, "")
        for field_name in ("name", "phone", "email", "city")
    ]
    ai_result = classify_imported_resume(
        imported_draft.get("raw_text", imported_text),
        api_key,
        sensitive_values=sensitive_values,
    )
    return _merge_ai_import_result(imported_draft, ai_result)


@st.dialog("根据 AI 建议优化", width="large")
def _show_suggestion_optimizer(
    resume_data: dict,
    api_key: str,
    suggestions: list[str],
) -> None:
    """让用户确认匹配建议，并把建议发送给 AI 重新优化简历。"""
    st.write("下面的岗位匹配建议将与简历内容一起发送给 DeepSeek。")
    suggestion_text = "\n".join(
        f"{index}. {suggestion}"
        for index, suggestion in enumerate(suggestions, start=1)
    )
    st.text_area(
        "AI 匹配建议",
        value=suggestion_text,
        height=180,
        disabled=True,
    )
    extra_instruction = st.text_area(
        "补充要求（可选）",
        placeholder="例如：重点突出项目经验，语言更简洁。",
        height=100,
        key="suggestion_optimizer_extra_instruction",
    )
    st.warning("AI 不得编造公司、项目、技能、证书和业绩数字。")

    if st.button("确认并开始优化", type="primary", use_container_width=True):
        if not api_key.strip():
            st.warning("请先在左侧填写 DeepSeek API Key。")
            return

        guidance_parts = ["岗位匹配建议：", suggestion_text]
        if extra_instruction.strip():
            guidance_parts.extend(["", "用户补充要求：", extra_instruction.strip()])

        try:
            with st.spinner("AI 正在根据建议生成逐条修改对比，请稍候……"):
                changes = optimize_resume_changes(
                    resume_data,
                    api_key,
                    optimization_guidance="\n".join(guidance_parts),
                )
            _install_optimization_review(changes)
            st.session_state["suggestion_optimization_notice"] = (
                f"已生成 {len(changes)} 条修改建议，请逐条接受或拒绝。"
            )
            st.rerun()
        except ResumeOptimizationError as error:
            st.error(str(error))


st.set_page_config(
    page_title="AI 简历助手",
    page_icon="🦊",
    layout="wide",
    initial_sidebar_state="auto",
)
st.session_state.setdefault("deepseek_api_key", os.getenv("DEEPSEEK_API_KEY", ""))
_initialize_resume_workspace()
_initialize_form_state()

_inject_brand_styles()
_render_hero()

workspace_notice = st.session_state.pop("resume_workspace_notice", None)
if workspace_notice:
    st.success(workspace_notice)

workspace_mode = st.session_state.get("resume_workspace_mode", "master")
active_version = _active_job_version()
with st.container(border=True):
    st.subheader("简历工作区")
    if workspace_mode == "job" and active_version:
        st.info(
            f"当前正在编辑岗位版本：{active_version.get('name', '未命名版本')}。"
            "保存和 AI 优化不会覆盖主简历。"
        )
    else:
        st.info("当前正在编辑主简历。岗位版本会从主简历复制创建。")

    open_master_column, new_version_column = st.columns(2)
    with open_master_column:
        if st.button(
            "打开主简历",
            use_container_width=True,
            disabled=(
                workspace_mode == "master"
                or not st.session_state.get("master_resume_data")
            ),
        ):
            _activate_master_resume()
    with new_version_column:
        if st.button(
            "新建岗位版本",
            type="primary",
            use_container_width=True,
            disabled=not st.session_state.get("master_resume_data"),
            help="请先保存主简历，再从主简历创建岗位版本。",
        ):
            _show_new_job_version_dialog()

    job_versions = st.session_state.get("job_versions", {})
    if job_versions:
        version_ids = list(job_versions.keys())
        active_version_id = st.session_state.get("active_job_version_id")
        pending_version_id = st.session_state.pop(
            "pending_job_version_selector",
            None,
        )
        if pending_version_id in version_ids:
            st.session_state["job_version_selector"] = pending_version_id
        if st.session_state.get("job_version_selector") not in version_ids:
            st.session_state["job_version_selector"] = (
                active_version_id if active_version_id in version_ids else version_ids[0]
            )
        selected_version_id = st.selectbox(
            "已有岗位版本",
            options=version_ids,
            key="job_version_selector",
            format_func=lambda version_id: (
                f"{job_versions[version_id].get('name', '未命名版本')} / "
                f"{job_versions[version_id].get('resume_data', {}).get('target_role', '未填写岗位')}"
            ),
        )
        if st.button(
            "打开所选岗位版本",
            use_container_width=True,
            disabled=(
                workspace_mode == "job"
                and selected_version_id == st.session_state.get("active_job_version_id")
            ),
        ):
            _activate_job_version(selected_version_id)
    else:
        st.caption("还没有岗位版本。保存主简历后，点击“新建岗位版本”。")

import_notice = st.session_state.pop("resume_import_notice", None)
if import_notice:
    st.success(import_notice)

with st.expander("导入已有 PDF / Word / 图片简历", expanded=False):
    st.write("先读取文字并分段，再由你逐项确认；扫描件会自动 OCR，确认前不会覆盖已填写内容。")
    imported_file = st.file_uploader(
        "选择已有简历",
        type=("pdf", "docx", "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"),
        key="resume_import_file",
        help=(
            "支持 PDF、Word（.docx）以及 JPG、PNG、WEBP、BMP、TIFF 图片，最大 10MB。"
            "扫描版 PDF 和图片会在服务器内自动进行 OCR。"
        ),
    )
    st.caption(
        "普通 PDF 会直接读取文字；扫描版 PDF 和图片会自动 OCR。"
        "OCR 只负责取字，如果板块错位可再使用 AI 精准归类。"
    )
    ai_import_consent = st.checkbox(
        "我同意将隐藏联系方式后的简历经历发送给 DeepSeek 做章节归类",
        key="resume_import_ai_consent",
        help="姓名、手机、邮箱、城市会先在本地替换为隐藏标记；经历、教育、项目和技能文字会发送给 DeepSeek。",
    )
    local_column, ai_column = st.columns(2)
    with local_column:
        start_local_import = st.button("本地快速识别", use_container_width=True)
    with ai_column:
        start_ai_import = st.button(
            "AI 精准识别",
            type="primary",
            use_container_width=True,
        )

    if start_local_import or start_ai_import:
        if imported_file is None:
            st.warning("请先选择一个 PDF、Word 或图片文件。")
        elif imported_file.size > RESUME_IMPORT_MAX_BYTES:
            st.error("文件超过 10MB，请压缩后重新上传。")
        elif start_ai_import and not ai_import_consent:
            st.warning("请先勾选同意，AI 才能帮助归类简历章节。")
        else:
            current_api_key = st.session_state.get("deepseek_api_key", "")
            if start_ai_import and not current_api_key.strip():
                st.warning("请先在左侧填写 DeepSeek API Key。")
            else:
                try:
                    spinner_text = (
                        "正在读取文字、必要时执行 OCR，并使用 AI 归类章节……"
                        if start_ai_import
                        else "正在读取文字、必要时执行 OCR，并整理简历……"
                    )
                    with st.spinner(spinner_text):
                        imported_draft = _recognize_imported_file(
                            imported_file,
                            use_ai=start_ai_import,
                            api_key=current_api_key,
                        )
                    for field_name in IMPORT_FIELDS:
                        st.session_state.pop(f"import_{field_name}", None)
                    st.session_state["resume_import_draft"] = imported_draft
                    st.session_state["resume_import_filename"] = imported_file.name
                    st.session_state["resume_import_method"] = (
                        "AI 精准识别" if start_ai_import else "本地快速识别"
                    )
                except ValueError as error:
                    st.error(str(error))
                except ResumeOptimizationError as error:
                    st.error(str(error))

imported_draft = st.session_state.get("resume_import_draft")
if imported_draft:
    for field_name in IMPORT_FIELDS:
        st.session_state.setdefault(
            f"import_{field_name}",
            imported_draft.get(field_name, ""),
        )

    recognized_count = sum(
        bool(str(imported_draft.get(field_name, "")).strip())
        for field_name in IMPORT_FIELDS
    )
    st.info(
        f"已从“{st.session_state.get('resume_import_filename', '上传文件')}”"
        f"使用{st.session_state.get('resume_import_method', '本地快速识别')}识别出 {recognized_count} 项。"
        "请检查并修改，确认后才会保存为主简历。"
    )
    parse_warnings = imported_draft.get("parse_warnings", [])
    if imported_draft.get("ocr_used"):
        ocr_language = imported_draft.get("ocr_language", "")
        if ocr_language == "windows-profile":
            language_note = "（Windows 本地）"
        else:
            language_note = "（中英文）" if "chi_sim" in ocr_language else ""
        st.success(
            f"已自动使用{imported_draft.get('extraction_method', 'OCR')}{language_note}取字。"
            "扫描件可能出现相似字、空格或日期误差，请重点核对姓名、联系方式和时间。"
        )
    if imported_draft.get("ai_assisted"):
        st.success("已完成 AI 辅助归类。联系方式仍来自本地识别，请重点核对姓名、手机和邮箱。")
    elif parse_warnings:
        st.warning(
            f"本地识别质量：{imported_draft.get('parse_quality', '待核对')}。"
            + "；".join(str(item).rstrip("。") for item in parse_warnings)
            + "。"
        )
    else:
        st.success("本地识别质量：高。仍建议快速核对联系方式、日期和章节边界。")

    with st.form("resume_import_confirmation"):
        st.subheader("确认识别内容")
        basic_left, basic_right = st.columns(2)
        with basic_left:
            st.text_input("姓名 *", key="import_name")
            st.text_input("手机号码", key="import_phone")
        with basic_right:
            st.text_input("电子邮箱", key="import_email")
            st.text_input("所在城市", key="import_city")

        st.text_input("目标岗位 *", key="import_target_role")
        st.text_area("自我介绍", height=120, key="import_summary")
        st.text_area("教育经历", height=140, key="import_education")
        st.text_area("工作或实习经历", height=220, key="import_work_experience")
        st.text_area("项目经历", height=180, key="import_project_experience")
        st.text_area("技能与证书", height=140, key="import_skills")

        confirm_import = st.form_submit_button(
            "确认并保存为主简历",
            type="primary",
            use_container_width=True,
        )

    with st.expander("查看读取到的完整原文（用于核对漏识别内容）"):
        st.caption(
            f"取字方式：{imported_draft.get('extraction_method', '自动判断')}。"
            "这里展示程序实际读取到的文本；如果错行或缺字，请上传更清晰、方向正确的原文件。"
        )
        st.text_area(
            "完整原文",
            value=imported_draft.get("raw_text", ""),
            height=240,
            disabled=True,
        )
        if imported_draft.get("unclassified_text"):
            st.caption("下面内容没有自动归入章节，请按需要复制到上面的对应输入框。")
            st.text_area(
                "未分类内容",
                value=imported_draft["unclassified_text"],
                height=140,
                disabled=True,
            )

    if confirm_import:
        confirmed_resume = {
            field_name: st.session_state.get(f"import_{field_name}", "").strip()
            for field_name in IMPORT_FIELDS
        }
        if not confirmed_resume["name"] or not confirmed_resume["target_role"]:
            st.warning("请补充姓名和目标岗位后再保存。")
        else:
            _persist_active_job_version()
            previous_resume = st.session_state.get("resume_data", {})
            confirmed_resume["job_description"] = ""
            confirmed_resume["photo_bytes"] = previous_resume.get("photo_bytes")
            st.session_state["resume_data"] = confirmed_resume
            st.session_state["master_resume_data"] = _copy_resume_data(confirmed_resume)
            st.session_state["resume_workspace_mode"] = "master"
            st.session_state["active_job_version_id"] = None
            _load_resume_into_form(confirmed_resume)
            _clear_derived_resume_state()
            st.session_state.pop("resume_import_draft", None)
            st.session_state["resume_import_notice"] = (
                "导入内容已经保存为主简历；已有岗位版本没有被覆盖。"
            )
            st.rerun()

master_resume = st.session_state.get("master_resume_data")
if master_resume:
    backup_name = "".join(
        character
        for character in f"{master_resume.get('name', '我的')}-主简历备份"
        if character not in '\\/:*?\"<>|'
    ) or "我的主简历备份"
    backup_column, library_column, status_column = st.columns([2, 2, 3])
    with backup_column:
        st.download_button(
            "下载主简历备份",
            data=_master_resume_json(master_resume),
            file_name=f"{backup_name}.json",
            mime="application/json",
            use_container_width=True,
        )
    with library_column:
        st.download_button(
            "下载全部版本备份",
            data=_resume_library_json(),
            file_name=f"{backup_name}-全部版本.json",
            mime="application/json",
            use_container_width=True,
        )
    with status_column:
        version_count = len(st.session_state.get("job_versions", {}))
        st.caption(
            f"当前有 1 份主简历和 {version_count} 份岗位版本。"
            "JSON 备份不包含证件照。"
        )

with st.sidebar:
    st.header("AI 连接")
    st.caption("连接 DeepSeek，让狐狸顾问开始分析和改写。")
    api_key = st.text_input(
        "DeepSeek API Key",
        type="password",
        key="deepseek_api_key",
        placeholder="sk-...",
        help="直接填写时只保存在当前浏览器会话中；也可使用本地 .env 文件。",
    )
    st.caption(f"当前 DeepSeek 模型：`{DEFAULT_MODEL}`")
    st.info("请用户自行填写 DeepSeek API Key。")
    st.markdown(
        "DeepSeek API 开放平台："
        "[https://platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)"
    )
    st.warning("不要把 API Key 发到聊天、截图或简历内容中。")
    if st.button("测试 DeepSeek 连接", use_container_width=True):
        if not api_key.strip():
            st.warning("请先填写 DeepSeek API Key。")
        else:
            try:
                with st.spinner("正在测试连接……"):
                    connection_message = test_deepseek_connection(api_key)
                st.success(connection_message)
            except ResumeOptimizationError as error:
                st.error(str(error))

with st.form("resume_form"):
    if workspace_mode == "job" and active_version:
        st.subheader(f"编辑岗位版本：{active_version.get('name', '未命名版本')}")
        st.caption("这个版本可以单独修改，保存后不会改变主简历。")
    else:
        st.subheader("编辑主简历")
        st.caption("主简历保存你的完整真实经历，岗位版本会从这里复制内容。")

    st.subheader("基本信息")
    left, right = st.columns(2)
    with left:
        name = st.text_input(
            "姓名 *",
            placeholder="例如：张三",
            key="form_name",
        )
        phone = st.text_input(
            "手机号码",
            placeholder="例如：13800000000",
            key="form_phone",
        )
    with right:
        email = st.text_input(
            "电子邮箱",
            placeholder="例如：name@example.com",
            key="form_email",
        )
        city = st.text_input(
            "所在城市",
            placeholder="例如：上海",
            key="form_city",
        )

    uploaded_photo = st.file_uploader(
        "证件照（可选）",
        type=("jpg", "jpeg", "png"),
        key="form_photo",
        help="建议使用竖版 3:4 证件照，支持 JPG、PNG，最大 5MB。照片不会发送给 AI。",
    )
    saved_photo = st.session_state.get("resume_data", {}).get("photo_bytes")
    if uploaded_photo is not None:
        st.image(uploaded_photo, width=120, caption="新上传的证件照")
    elif saved_photo:
        st.image(saved_photo, width=120, caption="已保存的证件照")

    st.subheader("求职目标")
    target_role = st.text_input(
        "目标岗位 *",
        placeholder="例如：产品经理",
        key="form_target_role",
    )
    job_description = st.text_area(
        "岗位要求",
        placeholder="可以粘贴招聘网站上的岗位职责和任职要求。",
        height=140,
        key="form_job_description",
    )

    st.subheader("个人经历")
    summary = st.text_area(
        "自我介绍",
        placeholder="简单介绍你的优势、经验和求职方向。",
        height=120,
        key="form_summary",
    )
    education = st.text_area(
        "教育经历",
        placeholder="例如：2020.09-2024.06，某某大学，计算机专业，本科。",
        height=120,
        key="form_education",
    )
    work_experience = st.text_area(
        "工作或实习经历",
        placeholder="写清时间、公司、岗位、做过什么，以及取得的结果。",
        height=180,
        key="form_work_experience",
    )
    project_experience = st.text_area(
        "项目经历",
        placeholder="写清项目名称、你的职责、采用的方法和最终成果。",
        height=180,
        key="form_project_experience",
    )
    skills = st.text_area(
        "技能与证书",
        placeholder="例如：Excel、Python、英语六级、教师资格证。",
        height=120,
        key="form_skills",
    )

    save_label = (
        "保存岗位版本并预览"
        if workspace_mode == "job"
        else "保存主简历并预览"
    )
    submitted = st.form_submit_button(save_label, type="primary", use_container_width=True)

if submitted:
    if not name.strip() or not target_role.strip():
        st.warning("请先填写带 * 的姓名和目标岗位。")
    else:
        previous_resume_data = st.session_state.get("resume_data")
        photo_bytes = (
            uploaded_photo.getvalue()
            if uploaded_photo is not None
            else (previous_resume_data or {}).get("photo_bytes")
        )
        if photo_bytes and len(photo_bytes) > PHOTO_MAX_BYTES:
            st.error("证件照超过 5MB，请压缩后重新上传。")
            st.stop()
        updated_resume_data = {
            "name": name.strip(),
            "phone": phone.strip(),
            "email": email.strip(),
            "city": city.strip(),
            "target_role": target_role.strip(),
            "job_description": job_description.strip(),
            "summary": summary.strip(),
            "education": education.strip(),
            "work_experience": work_experience.strip(),
            "project_experience": project_experience.strip(),
            "skills": skills.strip(),
            "photo_bytes": photo_bytes,
        }
        ai_content_changed = previous_resume_data is not None and any(
            previous_resume_data.get(field_name, "")
            != updated_resume_data[field_name]
            for field_name in AI_RELEVANT_FIELDS
        )
        had_optimized_resume = bool(st.session_state.get("optimized_resume"))

        if ai_content_changed:
            _clear_derived_resume_state()

        saved_as_job_version = _save_resume_to_current_workspace(updated_resume_data)

        if ai_content_changed and had_optimized_resume:
            save_message = "信息已更新。岗位或经历发生变化，请重新进行 AI 优化。"
        elif previous_resume_data is not None:
            if saved_as_job_version:
                save_message = "岗位版本已更新；主简历没有被修改。"
            else:
                save_message = "主简历已更新；只修改联系方式时，AI 优化结果会保留。"
        else:
            if saved_as_job_version:
                save_message = "岗位版本已保存；建议同时下载全部版本 JSON 备份。"
            else:
                save_message = "已经保存为主简历；建议同时下载上方的 JSON 备份长期保留。"

        st.session_state["resume_workspace_notice"] = save_message
        st.rerun()

resume_data = st.session_state.get("resume_data")
if resume_data:
    st.divider()
    st.subheader("简历内容预览")
    if workspace_mode == "job" and active_version:
        st.caption(f"当前预览：岗位版本“{active_version.get('name', '未命名版本')}”")
    else:
        st.caption("当前预览：主简历")
    preview_main, preview_photo = st.columns([4, 1])
    with preview_main:
        st.header(resume_data["name"])

        contact_items = [
            item
            for item in (resume_data["phone"], resume_data["email"], resume_data["city"])
            if item
        ]
        if contact_items:
            st.caption(" ｜ ".join(contact_items))

        st.markdown(f"**目标岗位：** {resume_data['target_role']}")
    with preview_photo:
        if resume_data.get("photo_bytes"):
            st.image(resume_data["photo_bytes"], use_container_width=True)

    preview_sections = (
        ("自我介绍", "summary"),
        ("教育经历", "education"),
        ("工作或实习经历", "work_experience"),
        ("项目经历", "project_experience"),
        ("技能与证书", "skills"),
    )
    for title, key in preview_sections:
        if resume_data[key]:
            st.markdown(f"### {title}")
            st.write(resume_data[key])

    st.divider()
    st.subheader("JD 岗位匹配")
    st.caption(
        "AI 会比较岗位 JD 与当前简历内容。80-100 为适配，60-79 为一般适配，0-59 为不适配。"
    )
    if workspace_mode == "master":
        st.info("建议先从主简历创建岗位版本，再针对具体 JD 分析和优化。")

    if st.button("分析 JD 匹配度", use_container_width=True):
        if not resume_data.get("job_description", "").strip():
            st.warning("请先在上方填写或粘贴完整的岗位要求。")
        elif not api_key.strip():
            st.warning("请先在左侧填写 DeepSeek API Key。")
        else:
            try:
                with st.spinner("AI 正在分析岗位匹配度，请稍候……"):
                    job_match_result = analyze_job_match(
                        resume_data,
                        api_key,
                        current_resume_text=st.session_state.get(
                            "optimized_resume_editor",
                            "",
                        ),
                    )
                st.session_state["job_match_result"] = job_match_result
                _persist_active_job_version()
            except ResumeOptimizationError as error:
                st.error(str(error))

    job_match_result = st.session_state.get("job_match_result")
    if job_match_result:
        match_score = int(job_match_result["score"])
        match_level = job_match_result["level"]

        st.metric("简历与岗位匹配度", f"{match_score}%")
        st.progress(match_score)

        if match_level == "适配":
            st.success("适配：当前简历与岗位要求的主要内容较为匹配。")
        elif match_level == "一般适配":
            st.warning("一般适配：具备部分相关条件，但简历仍有明显补强空间。")
        else:
            st.error("不适配：当前简历与岗位要求存在较大差距。")

        matched_column, missing_column = st.columns(2)
        with matched_column:
            st.markdown("#### 已匹配内容")
            matched_points = job_match_result.get("matched_points", [])
            if matched_points:
                for point in matched_points:
                    st.markdown(f"- {point}")
            else:
                st.caption("暂未识别到明确匹配内容。")

        with missing_column:
            st.markdown("#### 缺失或证据不足")
            missing_points = job_match_result.get("missing_points", [])
            if missing_points:
                for point in missing_points:
                    st.markdown(f"- {point}")
            else:
                st.caption("暂未发现明显缺失项。")

        if match_level in ("一般适配", "不适配"):
            st.markdown("#### AI 修改建议")
            st.caption("只补充真实经历；如果没有相关经验，不要为了提高分数而编造。")
            for index, suggestion in enumerate(
                job_match_result.get("suggestions", []),
                start=1,
            ):
                st.markdown(f"{index}. {suggestion}")

    st.divider()
    st.subheader("AI 优化")
    st.caption("只发送目标岗位和经历文本；姓名、手机、邮箱、城市不会发送给 AI。")
    st.warning("AI 不得编造公司、项目、技能、证书和业绩数字。")

    optimization_notice = st.session_state.pop("suggestion_optimization_notice", None)
    if optimization_notice:
        st.success(optimization_notice)

    suggestion_optimization_available = bool(
        job_match_result
        and job_match_result.get("level") in ("一般适配", "不适配")
        and job_match_result.get("suggestions")
    )
    if suggestion_optimization_available:
        standard_column, suggestion_column = st.columns(2)
        with standard_column:
            run_standard_optimization = st.button(
                "常规 AI 优化",
                use_container_width=True,
            )
        with suggestion_column:
            open_suggestion_optimizer = st.button(
                "根据 AI 建议优化",
                type="primary",
                use_container_width=True,
            )
    else:
        run_standard_optimization = st.button(
            "使用 AI 优化简历",
            type="primary",
            use_container_width=True,
        )
        open_suggestion_optimizer = False

    if open_suggestion_optimizer:
        _show_suggestion_optimizer(
            resume_data,
            api_key,
            job_match_result.get("suggestions", []),
        )

    if run_standard_optimization:
        if not api_key.strip():
            st.warning("请先在左侧填写 DeepSeek API Key。")
        else:
            try:
                with st.spinner("AI 正在生成逐条修改对比，请稍候……"):
                    changes = optimize_resume_changes(resume_data, api_key)
                _install_optimization_review(changes)
                st.success(f"已生成 {len(changes)} 条修改建议，请逐条接受或拒绝。")
            except ResumeOptimizationError as error:
                st.error(str(error))

optimization_review = st.session_state.get("optimization_review")
optimized_resume = st.session_state.get("optimized_resume")
if optimization_review:
    _render_optimization_review(optimization_review)
elif optimized_resume:
    st.divider()
    st.subheader("旧版 AI 优化结果")
    st.caption("这是旧会话生成的整篇结果。重新运行 AI 优化后，将显示逐条修改对比。")
    st.text_area(
        "可以在这里继续修改",
        height=500,
        key="optimized_resume_editor",
        on_change=_handle_optimized_editor_change,
    )
    st.caption("导出时会使用这里的最终内容。")

if resume_data:
    st.divider()
    st.subheader("专业模板与实时预览")
    if workspace_mode == "job" and active_version:
        st.caption(f"当前导出：岗位版本“{active_version.get('name', '未命名版本')}”")
    else:
        st.caption("当前导出：主简历")

    template_options = resume_template_options()
    current_template_id = resume_data.get("template_id", DEFAULT_TEMPLATE_ID)
    if current_template_id not in template_options:
        current_template_id = DEFAULT_TEMPLATE_ID
    workspace_identity = (
        st.session_state.get("active_job_version_id")
        if workspace_mode == "job"
        else "master"
    )
    template_widget_key = f"resume_template_selector_{workspace_identity}"
    if template_widget_key not in st.session_state:
        st.session_state[template_widget_key] = current_template_id
    selected_template_id = st.selectbox(
        "选择专业简历模板",
        options=list(template_options),
        format_func=lambda template_id: template_options[template_id],
        key=template_widget_key,
    )
    st.caption(resume_template_description(selected_template_id))

    if selected_template_id != resume_data.get("template_id"):
        updated_resume = _copy_resume_data(resume_data)
        updated_resume["template_id"] = selected_template_id
        st.session_state["resume_data"] = updated_resume
        resume_data = updated_resume
        if workspace_mode == "job" and active_version:
            active_version["resume_data"] = _copy_resume_data(updated_resume)
        else:
            st.session_state["master_resume_data"] = _copy_resume_data(updated_resume)

    final_resume_text = st.session_state.get("optimized_resume_editor", "").strip()
    if final_resume_text:
        if optimization_review:
            st.success("将导出逐条审阅后的当前采用稿。")
        else:
            st.success("将导出 AI 优化后的最终内容。")
    else:
        st.info("还没有 AI 优化结果，将导出你填写的原始内容。")

    st.markdown(
        create_resume_preview_html(
            resume_data,
            optimized_text=final_resume_text or None,
            photo_bytes=resume_data.get("photo_bytes"),
            template_id=selected_template_id,
        ),
        unsafe_allow_html=True,
    )
    st.caption("预览会随模板和内容即时更新。最终分页以导出的 Word 或 PDF 为准。")

    st.subheader("导出文件")

    try:
        word_bytes = create_resume_document(
            resume_data,
            optimized_text=final_resume_text or None,
            photo_bytes=resume_data.get("photo_bytes"),
            template_id=selected_template_id,
        )
        pdf_bytes = create_resume_pdf(
            resume_data,
            optimized_text=final_resume_text or None,
            photo_bytes=resume_data.get("photo_bytes"),
            template_id=selected_template_id,
        )
        safe_name = "".join(
            character
            for character in f"{resume_data['name']}-{resume_data['target_role']}"
            if character not in '\\/:*?\"<>|'
        ) or "我的简历"
        word_column, pdf_column = st.columns(2)
        with word_column:
            st.download_button(
                "导出 Word",
                data=word_bytes,
                file_name=f"{safe_name}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        with pdf_column:
            st.download_button(
                "一键导出 PDF",
                data=pdf_bytes,
                file_name=f"{safe_name}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
            )
    except Exception as error:
        st.error(f"生成简历失败：{error}")
        st.caption("如果刚上传照片，请换一张 JPG 或 PNG 后重试。")
