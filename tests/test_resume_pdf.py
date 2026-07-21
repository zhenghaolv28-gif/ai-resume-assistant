from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
import unittest

from pypdf import PdfReader


MODULE_PATH = Path(__file__).resolve().parents[1] / "resume_template.py"
SPEC = spec_from_file_location("resume_template_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
RESUME_TEMPLATE = module_from_spec(SPEC)
SPEC.loader.exec_module(RESUME_TEMPLATE)


SAMPLE_RESUME = {
    "name": "张三",
    "target_role": "产品经理",
    "phone": "13800138000",
    "email": "zhangsan@example.com",
    "city": "上海",
    "summary": "拥有三年互联网产品经验，擅长需求分析与跨部门协作。",
    "education": "2018.09-2022.06 某大学 信息管理 本科",
    "work_experience": (
        "2022.07-至今 某科技公司 产品经理\n"
        "- 负责产品规划、需求分析和版本推进。\n"
        "- 推动核心流程优化，交付周期缩短 30%。"
    ),
    "project_experience": "智能简历助手｜项目负责人\n- 完成需求梳理与产品方案设计。",
    "skills": "- Axure、Figma、SQL、Excel",
}


class ResumePdfTests(unittest.TestCase):
    def test_pdf_contains_business_header_and_resume_sections(self) -> None:
        pdf_bytes = RESUME_TEMPLATE.create_resume_pdf(SAMPLE_RESUME)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        reader = PdfReader(BytesIO(pdf_bytes))
        self.assertEqual(len(reader.pages), 1)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        for expected in (
            "简历",
            "张三",
            "目标岗位：产品经理",
            "个人简介",
            "教育背景",
            "工作经历",
            "项目经历",
            "技能证书",
            "第 1 页",
        ):
            self.assertIn(expected, text)

    def test_pdf_metadata_uses_candidate_name(self) -> None:
        pdf_bytes = RESUME_TEMPLATE.create_resume_pdf(SAMPLE_RESUME)
        reader = PdfReader(BytesIO(pdf_bytes))

        self.assertEqual(reader.metadata.title, "张三的简历")
        self.assertEqual(reader.metadata.author, "张三")


if __name__ == "__main__":
    unittest.main()
