"""读取并初步整理 PDF/Word 简历。

这里故意使用本地规则完成第一次识别，不把用户上传的完整简历自动发送给
DeepSeek。识别结果一定要经过用户确认后，才会保存到主简历。
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import re
import unicodedata
from typing import Any, NamedTuple
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from docx import Document
from docx.table import Table


IMAGE_RESUME_SUFFIXES = frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"})
OCR_MAX_PDF_PAGES = 8
OCR_MAX_IMAGE_FRAMES = 8
OCR_MAX_PIXELS_PER_PAGE = 40_000_000
OCR_MIN_NATIVE_CHARACTERS = 30
OCR_RENDER_SCALE = 2.4


class ResumeTextExtraction(NamedTuple):
    """一次简历取字的结果，供界面说明是否启用了 OCR。"""

    text: str
    method: str
    ocr_used: bool = False
    ocr_language: str = ""


_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "summary": (
        "自我介绍",
        "个人简介",
        "个人总结",
        "自我评价",
        "职业概述",
        "关于我",
        "个人优势",
        "个人概述",
        "职业简介",
        "个人说明",
        "个人介绍",
        "个人概况",
        "个人亮点",
        "个人陈述",
        "自我概况",
        "优势总结",
        "职业摘要",
        "简介",
        "profile",
        "professional summary",
        "executive summary",
        "professional profile",
        "career profile",
        "summary",
        "about me",
    ),
    "education": (
        "教育经历",
        "教育背景",
        "学历经历",
        "学历信息",
        "教育信息",
        "学历",
        "学习经历",
        "学术经历",
        "education",
        "education background",
        "academic background",
        "education and training",
        "education & training",
    ),
    "work_experience": (
        "工作经历",
        "工作经验",
        "工作履历",
        "实习经历",
        "实习经验",
        "职业经历",
        "任职经历",
        "就业经历",
        "工作实习经历",
        "工作与实习经历",
        "工作/实习经历",
        "工作实践",
        "社会工作经历",
        "校园经历",
        "校园活动",
        "学生工作",
        "社团经历",
        "志愿经历",
        "社会实践",
        "work experience",
        "experience",
        "work history",
        "career history",
        "professional experience",
        "employment history",
        "internship experience",
    ),
    "project_experience": (
        "项目经历",
        "项目经验",
        "项目实践",
        "项目成果",
        "个人项目",
        "项目案例",
        "项目作品",
        "项目亮点",
        "项目开发",
        "项目实践经历",
        "科研项目",
        "科研经历",
        "研究经历",
        "校园项目",
        "个人项目经历",
        "项目详情",
        "作品集",
        "project experience",
        "projects",
        "selected projects",
        "research experience",
    ),
    "skills": (
        "技能与证书",
        "专业技能",
        "技能证书",
        "技能",
        "技能特长",
        "专业能力",
        "核心技能",
        "核心竞争力",
        "技术技能",
        "技术能力",
        "计算机技能",
        "个人技能",
        "专业特长",
        "技能专长",
        "技能清单",
        "能力特长",
        "知识技能",
        "技术栈",
        "工具技能",
        "证书及荣誉",
        "证书和技能",
        "培训经历",
        "语言及技能",
        "it技能",
        "证书",
        "证书资质",
        "资格证书",
        "语言能力",
        "语言技能",
        "荣誉奖项",
        "获奖经历",
        "奖项荣誉",
        "skills",
        "technical skills",
        "skills and tools",
        "skills & tools",
        "technical proficiencies",
        "core competencies",
        "certifications",
        "certificates",
        "honors and awards",
    ),
    "target_role": (
        "求职意向",
        "求职目标",
        "求职方向",
        "期望职位",
        "期望岗位",
        "目标岗位",
        "应聘职位",
        "应聘岗位",
        "求职岗位",
        "职位目标",
        "应聘方向",
        "申请职位",
        "职业目标",
        "career objective",
        "objective",
        "target position",
    ),
}

_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "name": ("姓名", "名字"),
    "phone": ("手机", "手机号码", "联系电话", "电话", "联系方式"),
    "email": ("邮箱", "电子邮箱", "电子邮件", "email"),
    "city": (
        "所在城市",
        "现居地",
        "现居住地",
        "现居地址",
        "现居",
        "现居城市",
        "当前城市",
        "目前城市",
        "目前居住地",
        "常住地",
        "常住城市",
        "居住地",
        "居住地址",
        "所在地区",
        "所在地",
        "现所在地",
        "居住城市",
        "工作城市",
        "工作地点",
        "期望城市",
        "期望工作城市",
        "期望工作地",
        "意向城市",
        "目标城市",
        "户籍所在地",
        "户籍地",
        "所在地点",
        "所在位置",
        "籍贯",
        "城市",
        "地区",
        "地址",
        "location",
    ),
    "target_role": _SECTION_ALIASES["target_role"],
}

_FIELD_NAMES = (
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

_IGNORED_NAME_LINES = {
    "个人简历",
    "简历",
    "resume",
    "curriculum vitae",
    "cv",
}

_SINGLE_VALUE_FIELDS = {"name", "phone", "email", "city", "target_role"}

_COMMON_LOCATION_NAMES = {
    "北京",
    "北京市",
    "上海",
    "上海市",
    "天津",
    "天津市",
    "重庆",
    "重庆市",
    "深圳",
    "深圳市",
    "广州",
    "广州市",
    "杭州",
    "杭州市",
    "南京",
    "南京市",
    "苏州",
    "苏州市",
    "成都",
    "成都市",
    "武汉",
    "武汉市",
    "西安",
    "西安市",
    "长沙",
    "长沙市",
    "郑州",
    "郑州市",
    "青岛",
    "青岛市",
    "厦门",
    "厦门市",
    "福州",
    "福州市",
    "济南",
    "济南市",
    "合肥",
    "合肥市",
    "昆明",
    "昆明市",
    "宁波",
    "宁波市",
    "无锡",
    "无锡市",
    "东莞",
    "东莞市",
    "佛山",
    "佛山市",
    "珠海",
    "珠海市",
    "石家庄",
    "太原",
    "沈阳",
    "大连",
    "长春",
    "哈尔滨",
    "南昌",
    "南宁",
    "海口",
    "贵阳",
    "兰州",
    "西宁",
    "呼和浩特",
    "乌鲁木齐",
    "拉萨",
    "银川",
    "泉州",
    "温州",
    "绍兴",
    "嘉兴",
    "常州",
    "南通",
    "徐州",
    "烟台",
    "潍坊",
    "洛阳",
    "宜昌",
    "襄阳",
    "惠州",
    "中山",
    "汕头",
    "三亚",
    "义乌",
    "香港",
    "澳门",
    "台北",
    "新加坡",
    "singapore",
    "hong kong",
    "macau",
    "tokyo",
    "seoul",
    "london",
    "new york",
    "san francisco",
    "beijing",
    "shanghai",
    "shenzhen",
    "guangzhou",
    "hangzhou",
    "chengdu",
    "wuhan",
    "nanjing",
    "suzhou",
    "xiamen",
    "xian",
    "xi'an",
    "sydney",
    "melbourne",
    "toronto",
    "vancouver",
}

_CHINESE_PROVINCE_NAMES = (
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "海南",
    "四川",
    "贵州",
    "云南",
    "陕西",
    "甘肃",
    "青海",
    "内蒙古",
    "广西",
    "西藏",
    "宁夏",
    "新疆",
)

# 覆盖简历中常见的无“市”后缀写法，例如“安徽 蚌埠”或单独一行“淄博”。
_COMMON_LOCATION_NAMES.update(
    """
    唐山 秦皇岛 邯郸 邢台 保定 张家口 承德 沧州 廊坊 衡水
    大同 阳泉 长治 晋城 朔州 晋中 运城 忻州 临汾 吕梁
    包头 乌海 赤峰 通辽 鄂尔多斯 呼伦贝尔 巴彦淖尔 乌兰察布 兴安 锡林郭勒 阿拉善
    鞍山 抚顺 本溪 丹东 锦州 营口 阜新 辽阳 盘锦 铁岭 朝阳 葫芦岛
    四平 辽源 通化 白山 松原 白城 延边
    齐齐哈尔 鸡西 鹤岗 双鸭山 大庆 伊春 佳木斯 七台河 牡丹江 黑河 绥化 大兴安岭
    连云港 淮安 盐城 扬州 镇江 泰州 宿迁
    湖州 金华 衢州 舟山 台州 丽水
    芜湖 蚌埠 淮南 马鞍山 淮北 铜陵 安庆 黄山 滁州 阜阳 宿州 六安 亳州 池州 宣城
    莆田 三明 漳州 南平 龙岩 宁德
    景德镇 萍乡 九江 新余 鹰潭 赣州 吉安 宜春 抚州 上饶
    淄博 枣庄 东营 济宁 泰安 威海 日照 临沂 德州 聊城 滨州 菏泽
    开封 平顶山 安阳 鹤壁 新乡 焦作 濮阳 许昌 漯河 三门峡 南阳 商丘 信阳 周口 驻马店 济源
    黄石 十堰 荆州 鄂州 荆门 孝感 黄冈 咸宁 随州 恩施
    株洲 湘潭 衡阳 邵阳 岳阳 常德 张家界 益阳 郴州 永州 怀化 娄底 湘西
    韶关 河源 梅州 汕尾 江门 阳江 湛江 茂名 肇庆 清远 潮州 揭阳 云浮
    柳州 桂林 梧州 北海 防城港 钦州 贵港 玉林 百色 贺州 河池 来宾 崇左
    儋州
    自贡 攀枝花 泸州 德阳 绵阳 广元 遂宁 内江 乐山 南充 眉山 宜宾 广安 达州 雅安 巴中 资阳 阿坝 甘孜 凉山
    六盘水 遵义 安顺 毕节 铜仁 黔西南 黔东南 黔南
    曲靖 玉溪 保山 昭通 普洱 临沧 楚雄 红河 文山 西双版纳 大理 德宏 怒江 迪庆
    日喀则 昌都 林芝 山南 那曲 阿里
    铜川 宝鸡 咸阳 渭南 延安 汉中 榆林 安康 商洛
    嘉峪关 金昌 白银 天水 武威 张掖 平凉 酒泉 庆阳 定西 陇南 临夏 甘南
    海东 海北 黄南 海南州 果洛 玉树 海西
    石嘴山 吴忠 固原 中卫
    克拉玛依 吐鲁番 哈密 昌吉 博尔塔拉 巴音郭楞 阿克苏 克孜勒苏 喀什 和田 伊犁 塔城 阿勒泰
    """.split()
)

_ROLE_SUFFIXES = (
    "工程师",
    "经理",
    "总监",
    "专员",
    "顾问",
    "分析师",
    "设计师",
    "运营",
    "开发",
    "销售",
    "助理",
    "主管",
    "负责人",
)


def _normalize_text(text: str) -> str:
    """统一 PDF/Word 常见的换行、空格和全角字符。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    blank_seen = False
    for raw_line in text.split("\n"):
        line = re.sub(r"[ \t\u00a0]+", " ", raw_line).strip()
        if not line:
            if not blank_seen:
                lines.append("")
            blank_seen = True
            continue
        lines.append(line)
        blank_seen = False
    return "\n".join(lines).strip()


def _columns_contain_distinct_sections(columns: list[list[str]]) -> bool:
    """仅把确实包含多个简历板块标题的内容视为并排栏目。"""
    section_fields: list[set[str]] = []
    for column in columns:
        fields: set[str] = set()
        for line in column:
            field, _ = _heading_and_value(line)
            if field in _SECTION_ALIASES:
                fields.add(field)
        section_fields.append(fields)

    columns_with_sections = [fields for fields in section_fields if fields]
    return (
        len(columns_with_sections) >= 2
        and len(set().union(*columns_with_sections)) >= 2
    )


def _pdf_layout_column_candidate(layout_text: str) -> str:
    """把 pypdf 布局模式保留的大空格两栏，重排为逐栏阅读顺序。"""
    rows: list[list[tuple[int, str]]] = []
    multi_column_rows = 0
    for raw_line in layout_text.replace("\r", "").split("\n"):
        if not raw_line.strip():
            continue
        parts = [
            (match.start(), match.group(0).strip())
            for match in re.finditer(r"\S(?:.*?\S)?(?=\s{3,}|$)", raw_line)
            if match.group(0).strip()
        ]
        if len(parts) >= 2:
            multi_column_rows += 1
        rows.append(parts)

    if multi_column_rows < 2:
        return ""

    first_multi_index = next(
        (index for index, row in enumerate(rows) if len(row) >= 2),
        len(rows),
    )
    prefix = [row[0][1] for row in rows[:first_multi_index] if row]
    body_rows = rows[first_multi_index:]
    right_starts = [row[1][0] for row in body_rows if len(row) >= 2]
    if not right_starts:
        return ""
    right_anchor = min(right_starts)

    columns: list[list[str]] = [[], []]
    for row in body_rows:
        if not row:
            continue
        if len(row) >= 2:
            columns[0].append(row[0][1])
            columns[1].extend(part[1] for part in row[1:])
        elif row[0][0] >= max(12, right_anchor - 4):
            columns[1].append(row[0][1])
        else:
            columns[0].append(row[0][1])

    if not _columns_contain_distinct_sections(columns):
        return ""
    return _normalize_text("\n".join(prefix + columns[0] + columns[1]))


def _read_pdf_native(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("读取 PDF 需要 pypdf 依赖，请先安装项目依赖。") from exc

    try:
        reader = PdfReader(BytesIO(file_bytes))
        default_pages: list[str] = []
        layout_pages: list[str] = []
        for page in reader.pages:
            default_pages.append(page.extract_text() or "")
            try:
                layout_pages.append(
                    page.extract_text(
                        extraction_mode="layout",
                        layout_mode_space_vertically=False,
                    )
                    or ""
                )
            except (TypeError, ValueError, NotImplementedError):
                layout_pages = []
    except Exception as exc:  # pragma: no cover - varies by PDF producer
        raise ValueError("这个 PDF 暂时无法读取，请尝试另存为新的 PDF 后再上传。") from exc

    candidates = [_normalize_text("\n\n".join(default_pages))]
    if layout_pages:
        raw_layout_text = "\n\n".join(layout_pages)
        layout_text = _normalize_text(raw_layout_text)
        if layout_text and layout_text != candidates[0]:
            candidates.append(layout_text)
        column_text = _pdf_layout_column_candidate(raw_layout_text)
        if column_text and column_text not in candidates:
            candidates.append(column_text)
    return max(candidates, key=_resume_text_structure_score)


def _meaningful_character_count(text: str) -> int:
    """统计中英文和数字，避免把 PDF 控制字符误当成有效正文。"""
    return len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text))


def _native_pdf_text_is_sufficient(text: str) -> bool:
    """普通 PDF 有足够正文时不启动较慢的 OCR。"""
    return _meaningful_character_count(text) >= OCR_MIN_NATIVE_CHARACTERS


@lru_cache(maxsize=1)
def _tesseract_language() -> str:
    """优先使用简体中文和英文；给本地缺少语言包时提供明确提示。"""
    try:
        import pytesseract
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("OCR 组件未安装，请安装 requirements.txt 后重新启动应用。") from exc

    try:
        available_languages = set(pytesseract.get_languages(config=""))
    except pytesseract.TesseractNotFoundError as exc:
        raise ValueError(
            "没有找到 Tesseract OCR。Windows 本地运行时请先安装 Tesseract，"
            "云端部署会通过 packages.txt 自动安装。"
        ) from exc
    except pytesseract.TesseractError as exc:
        raise ValueError("OCR 引擎启动失败，请检查 Tesseract 安装后重试。") from exc

    preferred = [language for language in ("chi_sim", "eng") if language in available_languages]
    if not preferred:
        raise ValueError(
            "OCR 缺少中文或英文语言包，请安装 tesseract-ocr-chi-sim 和 "
            "tesseract-ocr-eng。"
        )
    return "+".join(preferred)


def _prepare_ocr_image(image):
    """修正旋转、背景和对比度，并把小图放大到适合 OCR 的尺寸。"""
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("图片识别需要 Pillow 依赖，请先安装项目依赖。") from exc

    image = ImageOps.exif_transpose(image)
    if image.width * image.height > OCR_MAX_PIXELS_PER_PAGE:
        raise ValueError("图片尺寸过大，请压缩到 4000 万像素以内再上传。")

    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba_image = image.convert("RGBA")
        white_background = Image.new("RGBA", rgba_image.size, "white")
        image = Image.alpha_composite(white_background, rgba_image).convert("RGB")
    else:
        image = image.convert("RGB")

    longest_side = max(image.size)
    if longest_side < 1800:
        scale = min(2.5, 1800 / max(longest_side, 1))
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    elif longest_side > 3200:
        scale = 3200 / longest_side
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )

    grayscale = ImageOps.grayscale(image)
    grayscale = ImageOps.autocontrast(grayscale, cutoff=1)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(1.15)
    return grayscale.filter(ImageFilter.SHARPEN)


def _best_ocr_text(raw_text: str) -> str:
    """保留 OCR 原顺序，同时尝试修复用大空格分开的双栏简历。"""
    normalized = _normalize_text(raw_text)
    candidates = [normalized]
    column_text = _pdf_layout_column_candidate(raw_text)
    if column_text and column_text not in candidates:
        candidates.append(column_text)
    return max(candidates, key=_resume_text_structure_score)


def _ocr_image(image) -> tuple[str, str]:
    try:
        import pytesseract
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("OCR 组件未安装，请安装 requirements.txt 后重新启动应用。") from exc

    language = _tesseract_language()
    prepared = _prepare_ocr_image(image)
    config = "--oem 3 --psm 3 -c preserve_interword_spaces=1"
    try:
        raw_text = pytesseract.image_to_string(
            prepared,
            lang=language,
            config=config,
            timeout=45,
        )
        if _meaningful_character_count(raw_text) < 10:
            raw_text = pytesseract.image_to_string(
                prepared,
                lang=language,
                config="--oem 3 --psm 6 -c preserve_interword_spaces=1",
                timeout=45,
            )
    except RuntimeError as exc:
        raise ValueError("OCR 识别超时，请压缩图片或减少 PDF 页数后重试。") from exc
    except pytesseract.TesseractNotFoundError as exc:
        _tesseract_language.cache_clear()
        raise ValueError("没有找到 Tesseract OCR，请先安装 OCR 引擎后重试。") from exc
    except pytesseract.TesseractError as exc:
        raise ValueError("OCR 未能读取这张图片，请换成更清晰的原图后重试。") from exc
    return _best_ocr_text(raw_text), language


def _read_image_ocr(file_bytes: bytes) -> tuple[str, str]:
    try:
        from PIL import Image, ImageSequence, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("图片识别需要 Pillow 依赖，请先安装项目依赖。") from exc

    try:
        with Image.open(BytesIO(file_bytes)) as source:
            frames = []
            for frame_index, frame in enumerate(ImageSequence.Iterator(source)):
                if frame_index >= OCR_MAX_IMAGE_FRAMES:
                    raise ValueError(
                        f"图片文件超过 {OCR_MAX_IMAGE_FRAMES} 页，请拆分后重新上传。"
                    )
                frames.append(frame.copy())
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("这个图片文件无法读取，请另存为 JPG 或 PNG 后再上传。") from exc

    page_texts: list[str] = []
    language = ""
    for frame in frames:
        page_text, language = _ocr_image(frame)
        if page_text:
            page_texts.append(page_text)
    return _normalize_text("\n\n".join(page_texts)), language


def _read_pdf_ocr(file_bytes: bytes) -> tuple[str, str]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("扫描 PDF 识别需要 pypdfium2，请先安装项目依赖。") from exc

    try:
        document = pdfium.PdfDocument(file_bytes)
    except Exception as exc:
        raise ValueError("这个 PDF 无法转换为图片，请尝试另存为新的 PDF 后再上传。") from exc

    try:
        page_count = len(document)
        if page_count == 0:
            raise ValueError("这个 PDF 没有可识别的页面。")
        if page_count > OCR_MAX_PDF_PAGES:
            raise ValueError(
                f"扫描 PDF 最多支持 {OCR_MAX_PDF_PAGES} 页，当前有 {page_count} 页。"
                "请删除无关页面或拆分文件后重试。"
            )

        page_texts: list[str] = []
        language = ""
        for page_index in range(page_count):
            page = document[page_index]
            bitmap = None
            try:
                bitmap = page.render(scale=OCR_RENDER_SCALE)
                page_text, language = _ocr_image(bitmap.to_pil())
                if page_text:
                    page_texts.append(page_text)
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
        return _normalize_text("\n\n".join(page_texts)), language
    finally:
        document.close()


_WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_WORD_PARAGRAPH = f"{_WORD_NAMESPACE}p"
_WORD_TEXT = f"{_WORD_NAMESPACE}t"
_WORD_TAB = f"{_WORD_NAMESPACE}tab"
_WORD_BREAK = f"{_WORD_NAMESPACE}br"
_WORD_CARRIAGE_RETURN = f"{_WORD_NAMESPACE}cr"


def _word_paragraph_text(paragraph: ElementTree.Element) -> str:
    """读取一个 Word 段落，但跳过其中嵌套文本框的子段落。"""
    parts: list[str] = []

    def visit(node: ElementTree.Element) -> None:
        for child in node:
            if child.tag == _WORD_PARAGRAPH:
                # 文本框里的段落会由外层 root.iter 单独读取，不能在这里重复收集。
                continue
            if child.tag == _WORD_TEXT:
                parts.append(child.text or "")
            elif child.tag == _WORD_TAB:
                parts.append("\t")
            elif child.tag in {_WORD_BREAK, _WORD_CARRIAGE_RETURN}:
                parts.append("\n")
            else:
                visit(child)

    visit(paragraph)
    return "".join(parts).strip()


def _read_docx_xml(file_bytes: bytes) -> str:
    """读取 DOCX XML 中的段落，覆盖文本框、页眉和页脚等区域。"""
    parts: list[str] = []
    with ZipFile(BytesIO(file_bytes)) as archive:
        xml_names = [
            name
            for name in archive.namelist()
            if name == "word/document.xml"
            or re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
        ]

        def content_order(name: str) -> tuple[int, str]:
            if "/header" in name:
                return 0, name
            if name == "word/document.xml":
                return 1, name
            return 2, name

        xml_names.sort(key=content_order)
        for name in xml_names:
            try:
                root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                continue
            for paragraph in root.iter(_WORD_PARAGRAPH):
                text = _word_paragraph_text(paragraph)
                if text:
                    parts.append(text)
    return _normalize_text("\n".join(parts))


def _flatten_docx2python_content(value: Any) -> list[str]:
    """按 docx2python 保留的文档/表格顺序取出段落。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        paragraphs: list[str] = []
        for item in value:
            paragraphs.extend(_flatten_docx2python_content(item))
        return paragraphs
    return []


def _docx2python_table_column_candidate(table: Any) -> list[str]:
    """当 Word 表格的各列是不同板块时，按列而不是按行读取。"""
    if not isinstance(table, (list, tuple)) or len(table) < 2:
        return []
    rows = [row for row in table if isinstance(row, (list, tuple))]
    column_count = max((len(row) for row in rows), default=0)
    if column_count < 2:
        return []

    columns: list[list[str]] = [[] for _ in range(column_count)]
    for column_index in range(column_count):
        for row in rows:
            if column_index >= len(row):
                continue
            columns[column_index].extend(_flatten_docx2python_content(row[column_index]))

    normalized_columns = [
        [line for value in column for line in _normalize_text(value).split("\n") if line]
        for column in columns
    ]
    if not _columns_contain_distinct_sections(normalized_columns):
        return []
    return [line for column in normalized_columns for line in column]


def _flatten_docx2python_body_section_first(body: Any) -> list[str]:
    """保留普通表格行序；只对包含并排板块标题的布局表格改为列序。"""
    if not isinstance(body, (list, tuple)):
        return _flatten_docx2python_content(body)

    paragraphs: list[str] = []
    for table in body:
        column_candidate = _docx2python_table_column_candidate(table)
        if column_candidate:
            paragraphs.extend(column_candidate)
        else:
            paragraphs.extend(_flatten_docx2python_content(table))
    return paragraphs


def _collapse_repeated_blocks(lines: list[str]) -> list[str]:
    """折叠解析器产生的 A+B+A+B 连续副本，不删除单独重复的职责句。"""
    result = lines[:]
    changed = True
    while changed:
        changed = False
        for block_size in range(len(result) // 2, 1, -1):
            for start in range(0, len(result) - block_size * 2 + 1):
                first = result[start : start + block_size]
                second = result[start + block_size : start + block_size * 2]
                if first != second:
                    continue
                del result[start + block_size : start + block_size * 2]
                changed = True
                break
            if changed:
                break
    return result


def _deduplicate_paragraphs(paragraphs: list[str]) -> list[str]:
    """去除相邻项和整段副本，同时保留不同经历中正常重复的职责原文。"""
    result: list[str] = []
    for paragraph in paragraphs:
        normalized = _normalize_text(paragraph)
        if not normalized:
            continue
        for line in normalized.split("\n"):
            if result and line == result[-1]:
                continue
            result.append(line)
    return _collapse_repeated_blocks(result)


def _read_docx_structured(file_bytes: bytes) -> str:
    """使用成熟解析器按段落/表格结构读取 DOCX，并关闭合并单元格复制。"""
    try:
        from docx2python import docx2python
    except ImportError:
        return ""

    try:
        with docx2python(
            BytesIO(file_bytes),
            duplicate_merged_cells=False,
        ) as content:
            header = _flatten_docx2python_content(content.header)
            row_body = _flatten_docx2python_content(content.body)
            section_first_body = _flatten_docx2python_body_section_first(content.body)
            footer = _flatten_docx2python_content(content.footer)
    except Exception:
        return ""

    candidates: list[str] = []
    for body in (row_body, section_first_body):
        paragraphs = _deduplicate_paragraphs(header + body + footer)
        candidate = _normalize_text("\n".join(paragraphs))
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return max(candidates, key=_resume_text_structure_score) if candidates else ""


def _read_docx(file_bytes: bytes) -> str:
    structured_text = _read_docx_structured(file_bytes)
    if len(structured_text.replace("\n", "").strip()) >= 10:
        return structured_text

    try:
        xml_text = _read_docx_xml(file_bytes)
        if len(xml_text.replace("\n", "").strip()) >= 10:
            return xml_text
    except (BadZipFile, OSError, ElementTree.ParseError):
        pass

    try:
        document = Document(BytesIO(file_bytes))
    except Exception as exc:  # pragma: no cover - varies by Word producer
        raise ValueError("这个 Word 文件无法读取，请另存为 .docx 后再上传。") from exc

    parts: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                cells: list[str] = []
                for cell in row.cells:
                    cell_text = _normalize_text(cell.text)
                    if cell_text and (not cells or cell_text != cells[-1]):
                        cells.append(cell_text)
                if cells:
                    parts.append(" | ".join(cells))
        elif block.text.strip():
            parts.append(block.text)
    return _normalize_text("\n".join(parts))


def extract_resume_text_with_details(
    file_bytes: bytes,
    filename: str,
) -> ResumeTextExtraction:
    """从 PDF、Word 或图片取字；扫描内容会自动回退到本地 OCR。"""
    if not file_bytes:
        raise ValueError("上传的文件为空，请重新选择。")
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "pdf":
        native_text = _read_pdf_native(file_bytes)
        if _native_pdf_text_is_sufficient(native_text):
            result = ResumeTextExtraction(
                text=native_text,
                method="PDF 可复制文字",
            )
        else:
            try:
                ocr_text, ocr_language = _read_pdf_ocr(file_bytes)
            except ValueError:
                if _meaningful_character_count(native_text) >= 10:
                    result = ResumeTextExtraction(
                        text=native_text,
                        method="PDF 可复制文字（内容较少）",
                    )
                else:
                    raise
            else:
                result = ResumeTextExtraction(
                    text=ocr_text,
                    method="扫描 PDF OCR",
                    ocr_used=True,
                    ocr_language=ocr_language,
                )
    elif suffix == "docx":
        result = ResumeTextExtraction(
            text=_read_docx(file_bytes),
            method="Word 可编辑文字",
        )
    elif suffix == "doc":
        raise ValueError("暂不支持旧版 .doc，请在 Word 中另存为 .docx 后再上传。")
    elif suffix in IMAGE_RESUME_SUFFIXES:
        ocr_text, ocr_language = _read_image_ocr(file_bytes)
        result = ResumeTextExtraction(
            text=ocr_text,
            method="图片 OCR",
            ocr_used=True,
            ocr_language=ocr_language,
        )
    else:
        raise ValueError(
            "只支持 PDF、Word（.docx）或 JPG、PNG、WEBP、BMP、TIFF 图片。"
        )
    if _meaningful_character_count(result.text) < 10:
        if suffix == "docx":
            raise ValueError(
                "这个 Word 中没有读取到足够的可编辑文字。文件内容可能是图片，"
                "请先把页面导出为图片或 PDF，再使用 OCR 导入。"
            )
        if result.ocr_used:
            raise ValueError(
                "OCR 没有识别到足够文字。请上传更清晰、方向正确且没有严重反光的原图。"
            )
        raise ValueError("没有读取到足够的文字，请检查文件内容后重新上传。")
    return result


def extract_resume_text(file_bytes: bytes, filename: str) -> str:
    """兼容原调用方式，只返回取出的简历文字。"""
    return extract_resume_text_with_details(file_bytes, filename).text


def _compact_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _find_field(label: str) -> str | None:
    compact = _compact_key(label)
    for field_name, aliases in _FIELD_LABELS.items():
        for alias in aliases:
            alias_key = _compact_key(alias)
            if compact == alias_key:
                return field_name
    for field_name, aliases in _SECTION_ALIASES.items():
        for alias in aliases:
            alias_key = _compact_key(alias)
            if compact == alias_key:
                return field_name
    return None


def _metadata_label_and_value(line: str) -> tuple[str | None, str]:
    """识别使用冒号或空格分隔的姓名、电话、邮箱、城市和目标岗位。"""
    title = re.sub(r"^\s*(?:[-•●▪·]\s*|\d+[.、)]\s*)+", "", line).strip()
    aliases: list[tuple[str, str]] = []
    for field_name in _SINGLE_VALUE_FIELDS:
        aliases.extend((field_name, alias) for alias in _FIELD_LABELS[field_name])

    for field_name, alias in sorted(aliases, key=lambda item: len(item[1]), reverse=True):
        match = re.match(
            rf"^{re.escape(alias)}(?:\s*[:：]\s*|\s+)(.+?)\s*$",
            title,
            flags=re.IGNORECASE,
        )
        if match:
            return field_name, match.group(1).strip()
    return None, ""


def _looks_like_phone_fragment(value: str) -> bool:
    compact_digits = re.sub(r"\D", "", value)
    if re.fullmatch(r"(?:86)?1[3-9]\d{9}", compact_digits):
        return True
    return value.strip().startswith("+") and 7 <= len(compact_digits) <= 15


def _split_logical_lines(lines: list[str]) -> list[str]:
    """只拆分联系方式复合行，避免把技能列表中的竖线误拆为章节。"""
    logical_lines: list[str] = []
    for line in lines:
        parts = [part.strip() for part in re.split(r"\s*[|｜]\s*", line) if part.strip()]
        if len(parts) < 2:
            logical_lines.append(line)
            continue

        metadata_labels = 0
        contact_signals = 0
        for part in parts:
            labeled_field, _ = _metadata_label_and_value(part)
            exact_field = _find_field(part)
            if labeled_field in _SINGLE_VALUE_FIELDS or exact_field in _SINGLE_VALUE_FIELDS:
                metadata_labels += 1
            if re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", part):
                contact_signals += 1
            elif _looks_like_phone_fragment(part):
                contact_signals += 1

        if metadata_labels >= 2 or contact_signals >= 2:
            logical_lines.extend(parts)
        else:
            logical_lines.append(line)
    return logical_lines


def _looks_like_location_candidate(value: str) -> bool:
    candidate = value.strip(" ：:|·•,，")
    if not candidate or len(candidate) > 40:
        return False
    location_keys = {_compact_key(location) for location in _COMMON_LOCATION_NAMES}
    if _compact_key(candidate) in location_keys:
        return True
    return bool(
        re.fullmatch(
            r"[\u4e00-\u9fff]{2,12}(?:市|区|县|州|省|自治区|特别行政区)",
            candidate,
        )
    )


def _clean_city_name(value: str) -> str:
    city = value.strip(" ：:|/\\·•,，;；()（）")
    city = re.sub(r"^(?:中国|china)\s*", "", city, flags=re.IGNORECASE)
    if city.endswith("市") and len(city) > 2:
        city = city[:-1]
    if re.fullmatch(r"[A-Za-z .'-]+", city):
        city = city.title()
    return city.strip()


def _extract_city_candidate(value: str, *, allow_embedded: bool) -> str:
    """从省市组合、联系方式混排行或英文地点中提取城市。"""
    candidate = value.strip()
    if not candidate:
        return ""

    location_by_key = {
        _compact_key(location): location for location in _COMMON_LOCATION_NAMES
    }
    exact_location = location_by_key.get(_compact_key(candidate))
    if exact_location:
        return _clean_city_name(exact_location)

    tokens = [
        token.strip(" ：:|/\\·•,，;；()（）")
        for token in re.split(r"[|｜/\\·•,，;；()（）\s]+", candidate)
        if token.strip()
    ]
    for token in tokens:
        location = location_by_key.get(_compact_key(token))
        if location:
            return _clean_city_name(location)

    compact_candidate = _compact_key(candidate)
    for province in _CHINESE_PROVINCE_NAMES:
        province_key = _compact_key(province)
        if province_key not in compact_candidate:
            continue
        remainder = compact_candidate.split(province_key, 1)[1].removeprefix("省")
        for location in sorted(_COMMON_LOCATION_NAMES, key=len, reverse=True):
            if _compact_key(location) and _compact_key(location) in remainder:
                return _clean_city_name(location)

    province_pattern = "|".join(
        re.escape(province) for province in _CHINESE_PROVINCE_NAMES
    )
    province_city = re.search(
        rf"(?:{province_pattern})(?:省|自治区)?\s*"
        r"([一-鿿]{2,10}?)(?:市|自治州|地区|盟)(?=$|[\s|｜/·,，;；])",
        candidate,
    )
    if province_city:
        return _clean_city_name(province_city.group(1))

    suffixed_city = re.search(
        r"(?<![一-鿿])([一-鿿]{2,10}?)(?:市|自治州|地区|盟)"
        r"(?=$|[\s|｜/·,，;；])",
        candidate,
    )
    if suffixed_city:
        return _clean_city_name(suffixed_city.group(1))

    if allow_embedded:
        for location in sorted(_COMMON_LOCATION_NAMES, key=len, reverse=True):
            if _compact_key(location) in compact_candidate:
                return _clean_city_name(location)
    return ""


def _guess_heading_field(title: str) -> str | None:
    """根据常见关键词兼容用户自定义的章节标题。"""
    compact = _compact_key(title)
    if len(compact) > 24:
        return None
    if re.search(r"\d|@|[：:]", title):
        return None
    if any(
        marker in compact
        for marker in ("工作地点", "工作城市", "期望城市", "薪资", "到岗", "联系电话", "电子邮箱")
    ):
        return None
    keyword_groups = (
        ("target_role", ("求职", "应聘", "期望", "目标岗位", "objective", "target")),
        ("education", ("教育", "学历", "学业", "学习", "academic")),
        ("work_experience", ("工作", "实习", "任职", "职业", "就业", "employment")),
        ("project_experience", ("项目", "实践", "作品", "project")),
        ("skills", ("技能", "能力", "证书", "资质", "语言", "荣誉", "奖项", "skill", "certificate")),
        ("summary", ("自我", "个人简介", "个人概述", "职业简介", "profile", "summary", "about")),
    )
    for field_name, keywords in keyword_groups:
        if any(_compact_key(keyword) in compact for keyword in keywords):
            return field_name
    return None


def _heading_prefix_and_value(line: str) -> tuple[str | None, str]:
    """识别“工作经历 2020—2024”或“工作经历 / Work Experience”。"""
    title = re.sub(r"^\s*(?:[-•●▪·]\s*|\d+[.、)]\s*)+", "", line).strip()
    aliases: list[tuple[str, str]] = []
    for field_name, field_aliases in _SECTION_ALIASES.items():
        aliases.extend((field_name, alias) for alias in field_aliases)
    for field_name, alias in sorted(aliases, key=lambda item: len(item[1]), reverse=True):
        match = re.match(
            rf"^{re.escape(alias)}(?=$|[\s:：|/\\()（）—–-])",
            title,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        remainder = title[match.end() :].strip()
        cleaned_remainder = remainder.strip(" ：:|/\\()（）—–-·•")
        if not cleaned_remainder or _find_field(cleaned_remainder):
            return field_name, ""
        return field_name, cleaned_remainder
    return None, ""


def _heading_and_value(
    line: str,
    current_field: str | None = None,
) -> tuple[str | None, str]:
    """识别单独章节标题，也识别“邮箱：xxx”这种行内字段。"""
    match = re.match(r"^\s*(?:\d+[.、)]\s*)?([^:：|]{1,20})\s*[:：|]\s*(.*)$", line)
    if match:
        field = _find_field(match.group(1))
        if field:
            return field, match.group(2).strip()

    metadata_field, metadata_value = _metadata_label_and_value(line)
    if metadata_field:
        return metadata_field, metadata_value

    prefix_field, prefix_value = _heading_prefix_and_value(line)
    if prefix_field:
        return prefix_field, prefix_value

    title = re.sub(r"^\s*(?:\d+[.、)]\s*)", "", line).strip(" ：:|·•")
    field = _find_field(title)
    if field and len(title) <= 60:
        return field, ""
    guessed_field = _guess_heading_field(title)
    if (
        guessed_field
        and guessed_field != current_field
        and len(title) <= 24
        and not re.search(r"[。；，,.;]$", title)
    ):
        return guessed_field, ""
    return None, ""


def _looks_like_unrecognized_heading(line: str) -> bool:
    """发现未知章节标题时切断上一章节，避免后续内容整体错位。"""
    title = re.sub(r"^\s*(?:[-•●▪·]\s*|\d+[.、)]\s*)+", "", line).strip()
    if not title or len(title) > 20 or re.search(r"[，,。；;：:]", title):
        return False
    compact = _compact_key(title)
    endings = (
        "经历",
        "经验",
        "背景",
        "信息",
        "情况",
        "能力",
        "技能",
        "介绍",
        "评价",
        "概述",
        "目标",
        "意向",
        "履历",
        "资质",
        "证书",
        "实践",
        "荣誉",
        "奖项",
        "experience",
        "background",
        "education",
        "skills",
        "projects",
        "summary",
        "profile",
        "contact",
    )
    return compact.endswith(endings)


def _guess_labeled_value(lines: list[str], field_name: str) -> str:
    aliases = _FIELD_LABELS[field_name]
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    pattern = re.compile(rf"^\s*(?:{alias_pattern})\s*[:：|]\s*(.+?)\s*$", re.IGNORECASE)
    for line in lines:
        labeled_field, labeled_value = _metadata_label_and_value(line)
        if labeled_field == field_name and labeled_value:
            return labeled_value
        match = pattern.match(line)
        if match:
            return match.group(1).strip()
    return ""


def _guess_name(lines: list[str]) -> str:
    labeled = _guess_labeled_value(lines, "name")
    if labeled:
        return labeled
    for line in lines[:12]:
        candidate = line.strip(" ：:|·•")
        if not candidate or candidate.lower() in _IGNORED_NAME_LINES:
            continue
        if _find_field(candidate) or _guess_heading_field(candidate):
            continue
        if _looks_like_location_candidate(candidate):
            continue
        if any(candidate.endswith(suffix) for suffix in _ROLE_SUFFIXES):
            continue
        if re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", candidate):
            continue
        if re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", candidate):
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,6}", candidate):
            return candidate
        if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,40}", candidate) and " " in candidate:
            return candidate
    return ""


def _guess_phone(text: str, lines: list[str]) -> str:
    match = re.search(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)", text)
    if match:
        return re.sub(r"\D", "", match.group(0))[-11:]
    labeled = _guess_labeled_value(lines, "phone")
    if labeled:
        return labeled
    international = re.search(r"(?<!\d)\+\d{1,3}(?:[\s().-]*\d){6,14}(?!\d)", text)
    return international.group(0).strip() if international else ""


def _guess_email(text: str, lines: list[str]) -> str:
    match = re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", text)
    return match.group(0) if match else _guess_labeled_value(lines, "email")


def _guess_city(lines: list[str]) -> str:
    labeled = _guess_labeled_value(lines, "city")
    if labeled:
        return _extract_city_candidate(labeled, allow_embedded=True) or _clean_city_name(labeled)

    city_aliases = sorted(_FIELD_LABELS["city"], key=len, reverse=True)
    for line in lines:
        for alias in city_aliases:
            match = re.match(
                rf"^\s*{re.escape(alias)}\s*(?:[:：|/\\—–-]\s*)?(.+?)\s*$",
                line,
                flags=re.IGNORECASE,
            )
            if match:
                value = match.group(1).strip()
                return (
                    _extract_city_candidate(value, allow_embedded=True)
                    or _clean_city_name(value)
                )

    non_location_markers = ("大学", "学院", "学校", "公司", "集团", "项目", "负责")
    for index, line in enumerate(lines[:40]):
        candidate = line.strip(" ：:|·•,，")
        header_like = (
            len(candidate) <= 80
            and not any(marker in candidate for marker in non_location_markers)
            and (
                index < 12
                or bool(re.search(r"[@|｜/·,，;；]|\d+岁|(?:男|女)(?:士)?", candidate))
            )
        )
        city = _extract_city_candidate(candidate, allow_embedded=header_like)
        if city:
            return city
    return ""


def _join_section(lines: list[str]) -> str:
    return "\n".join(line for line in lines if line.strip()).strip()


def _remove_consumed_metadata_lines(
    lines: list[str],
    result: dict[str, Any],
) -> list[str]:
    """从未分类内容中移除已经成功提取的页眉信息。"""
    kept: list[str] = []
    phone_digits = re.sub(r"\D", "", str(result.get("phone", "")))
    email = str(result.get("email", "")).lower()
    exact_values = {
        _compact_key(str(result.get(field_name, "")))
        for field_name in ("name", "city", "target_role")
        if result.get(field_name)
    }

    for line in lines:
        labeled_field, _ = _metadata_label_and_value(line)
        if labeled_field in _SINGLE_VALUE_FIELDS:
            continue
        compact_line = _compact_key(line)
        if compact_line and compact_line in exact_values:
            continue
        if email and email in line.lower():
            continue
        line_digits = re.sub(r"\D", "", line)
        if phone_digits and len(phone_digits) >= 7 and phone_digits in line_digits:
            continue
        kept.append(line)
    return kept


def _build_parse_warnings(
    normalized: str,
    result: dict[str, Any],
    unclassified_text: str,
) -> list[str]:
    warnings: list[str] = []
    section_fields = (
        "summary",
        "education",
        "work_experience",
        "project_experience",
        "skills",
    )
    recognized_sections = sum(bool(result.get(field_name)) for field_name in section_fields)
    if recognized_sections == 0:
        warnings.append("没有识别到经历、教育或技能章节，请查看完整原文并手动分配。")

    compact_length = len(re.sub(r"\s+", "", normalized))
    unclassified_length = len(re.sub(r"\s+", "", unclassified_text))
    if compact_length and unclassified_length >= 30 and unclassified_length / compact_length >= 0.35:
        warnings.append("未分类内容较多，文件可能使用了多栏、表格或非标准章节标题。")
    if not result.get("name"):
        warnings.append("没有可靠识别到姓名，请检查页眉。")
    if not result.get("phone") and not result.get("email"):
        warnings.append("没有识别到手机或邮箱，请检查联系方式。")
    return warnings


def _summary_content_score(line: str) -> int:
    compact = _compact_key(line)
    score = sum(
        1
        for marker in (
            "本人",
            "自我",
            "拥有",
            "具备",
            "经验",
            "责任心",
            "学习能力",
            "沟通能力",
            "团队协作",
            "执行力",
            "性格",
            "积极",
            "认真",
            "热爱",
            "吃苦",
            "抗压",
            "善于",
            "擅长",
            "years of experience",
            "team player",
            "self-motivated",
        )
        if _compact_key(marker) in compact
    )
    if re.search(r"\d+\s*年[^。；;\n]{0,20}经验", line):
        score += 3
    if len(line) >= 45 and re.search(r"[，。；,.]", line):
        score += 1
    return score


def _skill_content_score(line: str) -> int:
    compact = _compact_key(line)
    score = sum(
        1
        for marker in (
            "技能",
            "熟练",
            "掌握",
            "精通",
            "办公软件",
            "编程",
            "数据库",
            "数据分析",
            "语言能力",
            "英语",
            "证书",
            "python",
            "java",
            "javascript",
            "typescript",
            "sql",
            "excel",
            "office",
            "word",
            "powerpoint",
            "photoshop",
            "figma",
            "axure",
            "cad",
            "c++",
            "cet",
            "ielts",
            "toefl",
        )
        if _compact_key(marker) in compact
    )
    if len(line) <= 100 and re.search(r"[、/|｜]", line):
        score += 1
    return score


def _rebalance_summary_and_skills(result: dict[str, Any]) -> None:
    """纠正两栏提取中“自我介绍标题、技能标题、两栏内容”交替造成的串栏。"""
    if result.get("summary") or not result.get("skills"):
        return
    skill_lines = [line for line in str(result["skills"]).split("\n") if line.strip()]
    if len(skill_lines) < 2:
        return

    summary_lines: list[str] = []
    remaining_skill_lines: list[str] = []
    for line in skill_lines:
        summary_score = _summary_content_score(line)
        skill_score = _skill_content_score(line)
        if summary_score >= 2 and summary_score > skill_score:
            summary_lines.append(line)
        else:
            remaining_skill_lines.append(line)

    if summary_lines and remaining_skill_lines and any(
        _skill_content_score(line) > 0 for line in remaining_skill_lines
    ):
        result["summary"] = _join_section(summary_lines)
        result["skills"] = _join_section(remaining_skill_lines)


def _deduplicate_result_sections(result: dict[str, Any]) -> None:
    """在最终字段内再次折叠整段副本，覆盖 PDF 交错后才显现的重复。"""
    for field_name in (
        "summary",
        "education",
        "work_experience",
        "project_experience",
        "skills",
    ):
        value = str(result.get(field_name, ""))
        if value:
            result[field_name] = _join_section(
                _deduplicate_paragraphs(value.split("\n"))
            )


def parse_resume_text(text: str) -> dict[str, Any]:
    """使用常见中文/英文章节标题将简历文本分成表单字段。"""
    normalized = _normalize_text(text)
    source_lines = _deduplicate_paragraphs(
        [line for line in normalized.split("\n") if line.strip()]
    )
    normalized = "\n".join(source_lines)
    lines = _split_logical_lines(source_lines)
    buckets: dict[str, list[str]] = {field: [] for field in _FIELD_NAMES}
    unclassified: list[str] = []
    current_field: str | None = None

    for line in lines:
        field, inline_value = _heading_and_value(
            line,
            current_field=current_field,
        )
        if field:
            if inline_value:
                buckets[field].append(inline_value)
                current_field = None if field in _SINGLE_VALUE_FIELDS else field
            else:
                current_field = field
            continue
        if _looks_like_unrecognized_heading(line):
            current_field = None
            unclassified.append(line)
            continue
        if current_field:
            buckets[current_field].append(line)
            if current_field in _SINGLE_VALUE_FIELDS:
                current_field = None
        else:
            unclassified.append(line)

    result: dict[str, Any] = {field: _join_section(buckets[field]) for field in _FIELD_NAMES}
    result["name"] = result["name"] or _guess_name(lines)
    result["phone"] = result["phone"] or _guess_phone(normalized, lines)
    result["email"] = result["email"] or _guess_email(normalized, lines)
    if result["city"]:
        result["city"] = (
            _extract_city_candidate(result["city"], allow_embedded=True)
            or _clean_city_name(result["city"])
        )
    else:
        result["city"] = _guess_city(lines)
    result["target_role"] = result["target_role"] or _guess_labeled_value(lines, "target_role")
    _rebalance_summary_and_skills(result)
    _deduplicate_result_sections(result)
    unclassified = _remove_consumed_metadata_lines(unclassified, result)
    unclassified_text = _join_section(unclassified)
    result["raw_text"] = normalized
    result["unclassified_text"] = unclassified_text
    result["recognized_fields"] = [
        field_name for field_name in _FIELD_NAMES if result.get(field_name)
    ]
    result["parse_warnings"] = _build_parse_warnings(
        normalized,
        result,
        unclassified_text,
    )
    result["parse_quality"] = (
        "高"
        if not result["parse_warnings"]
        else "中"
        if len(result["parse_warnings"]) == 1
        else "低"
    )
    return result


def _resume_text_structure_score(text: str) -> float:
    """比较 PDF 的普通提取与布局提取，优先选择栏目结构更完整的一份。"""
    if not text.strip():
        return float("-inf")
    parsed = parse_resume_text(text)
    section_fields = (
        "summary",
        "education",
        "work_experience",
        "project_experience",
        "skills",
    )
    score = 6.0 * sum(bool(parsed.get(field_name)) for field_name in section_fields)
    score += 2.0 * sum(
        bool(parsed.get(field_name))
        for field_name in ("name", "phone", "email", "city", "target_role")
    )
    compact_length = len(re.sub(r"\s+", "", text)) or 1
    unclassified_length = len(re.sub(r"\s+", "", parsed.get("unclassified_text", "")))
    score -= 8.0 * (unclassified_length / compact_length)
    return score
