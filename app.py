"""AI 简历助手的网页入口。"""

from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from resume_export import create_resume_document, create_resume_pdf


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
optimize_resume_text = _ai_service.optimize_resume_text
test_deepseek_connection = _ai_service.test_deepseek_connection


load_dotenv()

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


def _initialize_form_state() -> None:
    """将已保存资料稳定回填到可编辑表单。"""
    saved_resume = st.session_state.get("resume_data", {})
    for field_name in FORM_FIELDS:
        st.session_state.setdefault(
            f"form_{field_name}",
            saved_resume.get(field_name, ""),
        )

st.set_page_config(page_title="AI 简历助手", page_icon="📄", layout="centered")
_initialize_form_state()

st.title("📄 AI 简历助手")
st.write("填写真实经历，后续让 AI 帮你整理成更清晰的简历。")
st.info("🔒 当前内容只保存在这个浏览器会话中，不会写入硬盘。")

with st.sidebar:
    st.header("AI 设置")
    api_key = st.text_input(
        "DeepSeek API Key",
        type="password",
        value=os.getenv("DEEPSEEK_API_KEY", ""),
        placeholder="sk-...",
        help="直接填写时只保存在当前浏览器会话中；也可使用本地 .env 文件。",
    )
    st.caption(f"当前 DeepSeek 模型：`{DEFAULT_MODEL}`")
    st.info("分享版请每位使用者填写自己的 DeepSeek API Key。")
    st.warning("不要把 API Key 发到聊天、截图或简历内容中。")
    if st.button("🔌 测试 DeepSeek 连接", use_container_width=True):
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
    st.subheader("1. 基本信息")
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

    st.subheader("2. 求职目标")
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

    st.subheader("3. 个人经历")
    summary = st.text_area(
        "自我介绍",
        placeholder="简单介绍你的优势、经验和求职方向。",
        height=120,
        key="form_summary",
    )
    education = st.text_area(
        "教育经历",
        placeholder="例如：2020.09—2024.06，某某大学，计算机专业，本科。",
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

    submitted = st.form_submit_button("保存并预览", type="primary", use_container_width=True)

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

        st.session_state["resume_data"] = updated_resume_data

        if ai_content_changed:
            st.session_state.pop("optimized_resume", None)
            st.session_state.pop("optimized_resume_editor", None)

        if ai_content_changed and had_optimized_resume:
            st.success("信息已更新。岗位或经历发生变化，请重新进行 AI 优化。")
        elif previous_resume_data is not None:
            st.success("信息已更新；只修改联系方式时，AI 优化结果会保留。")
        else:
            st.success("信息已暂存在当前浏览器会话中。")

resume_data = st.session_state.get("resume_data")
if resume_data:
    st.divider()
    st.subheader("简历内容预览")
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
    st.subheader("AI 优化")
    st.caption("只发送目标岗位和经历文本；姓名、手机、邮箱、城市不会发送给 AI。")

    if st.button("✨ 使用 AI 优化简历", type="primary", use_container_width=True):
        if not api_key.strip():
            st.warning("请先在左侧填写 DeepSeek API Key。")
        else:
            try:
                with st.spinner("AI 正在优化，请稍候……"):
                    optimized_resume = optimize_resume_text(resume_data, api_key)
                st.session_state["optimized_resume"] = optimized_resume
                st.session_state["optimized_resume_editor"] = optimized_resume
                st.success("AI 优化完成，你可以继续修改结果。")
            except ResumeOptimizationError as error:
                st.error(str(error))

optimized_resume = st.session_state.get("optimized_resume")
if optimized_resume:
    st.divider()
    st.subheader("AI 优化结果")
    st.text_area(
        "可以在这里继续修改",
        height=500,
        key="optimized_resume_editor",
    )
    st.caption("导出时会使用这里的最终内容。")

if resume_data:
    st.divider()
    st.subheader("导出简历")
    final_resume_text = st.session_state.get("optimized_resume_editor", "").strip()
    if final_resume_text:
        st.success("将导出 AI 优化后的最终内容。")
    else:
        st.info("还没有 AI 优化结果，将导出你填写的原始内容。")

    try:
        word_bytes = create_resume_document(
            resume_data,
            optimized_text=final_resume_text or None,
            photo_bytes=resume_data.get("photo_bytes"),
        )
        pdf_bytes = create_resume_pdf(
            resume_data,
            optimized_text=final_resume_text or None,
            photo_bytes=resume_data.get("photo_bytes"),
        )
        safe_name = "".join(
            character
            for character in f"{resume_data['name']}-{resume_data['target_role']}"
            if character not in '\\/:*?\"<>|'
        ) or "我的简历"
        word_column, pdf_column = st.columns(2)
        with word_column:
            st.download_button(
                "⬇️ 导出 Word",
                data=word_bytes,
                file_name=f"{safe_name}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        with pdf_column:
            st.download_button(
                "⬇️ 一键导出 PDF",
                data=pdf_bytes,
                file_name=f"{safe_name}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
            )
    except Exception as error:
        st.error(f"生成简历失败：{error}")
        st.caption("如果刚上传照片，请换一张 JPG 或 PNG 后重试。")
