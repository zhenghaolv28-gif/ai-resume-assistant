"""调用 DeepSeek API 优化简历文字。"""

import json
import os
import re
import shutil
import ssl
import subprocess
import time

import httpx

from resume_review import normalize_review_changes


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
CONNECTION_MODES = (
    ("direct", "windows_certificates", "system_environment")
    if os.name == "nt"
    else ("direct", "system_environment")
)
MAX_ATTEMPTS = len(CONNECTION_MODES)

SYSTEM_INSTRUCTIONS = """
你是一名专业、谨慎的中文简历顾问。请根据用户提供的真实经历，生成一份适合目标岗位的中文简历内容。

必须遵守以下规则：
1. 只能使用用户明确提供的事实，绝不编造公司、学校、技能、数据、职责或成果。
2. 缺少量化结果时，不得自行添加数字；可以把表达改得更清晰、专业、有行动力。
3. 优先突出与目标岗位及岗位要求相关的经历，但不要删除重要事实。
4. 使用简洁、自然的中文纯文本；章节标题单独一行，项目内容可使用中文间隔号“·”。
5. 不输出姓名、手机、邮箱、城市等联系方式，这些信息由应用在导出时添加。
6. 不使用 Markdown，不输出星号、井号、反引号、表情或代码块。
7. 不解释你的思考过程，只输出优化后的简历正文。

建议结构：职业概述、教育经历、工作或实习经历、项目经历、技能与证书。没有内容的章节可以省略。
""".strip()


class ResumeOptimizationError(Exception):
    """可直接显示给用户的简历优化错误。"""


class _CurlTransportError(Exception):
    """系统 curl 备用连接失败。"""


def _windows_ssl_context() -> ssl.SSLContext:
    """创建并补充 Windows 系统根证书的 TLS 上下文。"""
    context = ssl.create_default_context()
    if hasattr(ssl, "enum_certificates"):
        for store_name in ("ROOT", "CA"):
            for certificate, encoding, _trust in ssl.enum_certificates(store_name):
                if encoding == "x509_asn":
                    pem_certificate = ssl.DER_cert_to_PEM_cert(certificate)
                    context.load_verify_locations(cadata=pem_certificate)
    return context


def _create_http_client(mode: str = "direct") -> httpx.Client:
    """按指定模式创建 DeepSeek HTTPS 客户端。"""
    options = {
        "base_url": DEEPSEEK_BASE_URL,
        "timeout": httpx.Timeout(120.0, connect=20.0),
        "trust_env": mode == "system_environment",
    }
    if mode == "windows_certificates":
        options["verify"] = _windows_ssl_context()
    return httpx.Client(**options)


def _safe_transport_detail(error: Exception, api_key: str) -> str:
    """提取底层网络原因，同时隐藏密钥并限制显示长度。"""
    details = []
    current_error: BaseException | None = error
    while current_error is not None and len(details) < 5:
        text = str(current_error).strip()
        if text and text not in details:
            details.append(text)
        current_error = current_error.__cause__

    detail = " → ".join(details) or type(error).__name__
    if api_key:
        detail = detail.replace(api_key, "[API Key 已隐藏]")
    return detail[:300]


def _curl_config_value(value: str) -> str:
    """把内容安全放入 curl 配置；密钥不会出现在命令行或磁盘中。"""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _request_with_system_curl(
    method: str,
    path: str,
    api_key: str,
    json_body: dict | None = None,
) -> httpx.Response:
    """使用系统 curl 连接，兼容 Windows 本地运行和 Linux 云端部署。"""
    if method not in {"GET", "POST"} or not path.startswith("/"):
        raise _CurlTransportError("不支持的备用请求。")

    url = f"{DEEPSEEK_BASE_URL}{path}"
    status_marker = b"\n__DEEPSEEK_HTTP_STATUS__:"
    config_lines = [
        f"url = {_curl_config_value(url)}",
        f"request = {_curl_config_value(method)}",
        f"header = {_curl_config_value(f'Authorization: Bearer {api_key}')}",
        f"header = {_curl_config_value('Content-Type: application/json')}",
        "silent",
        "show-error",
        "connect-timeout = 20",
        "max-time = 120",
        f"write-out = {_curl_config_value(chr(10) + '__DEEPSEEK_HTTP_STATUS__:%{http_code}')}",
    ]
    if json_body is not None:
        payload = json.dumps(
            json_body,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        config_lines.append(f"data-binary = {_curl_config_value(payload)}")

    curl_config = "\n".join(config_lines) + "\n"
    curl_executable = shutil.which("curl.exe" if os.name == "nt" else "curl")
    if not curl_executable:
        raise _CurlTransportError("系统未找到 curl 命令。")

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [curl_executable, "--config", "-"],
            input=curl_config.encode("utf-8"),
            capture_output=True,
            timeout=130,
            check=False,
            creationflags=creation_flags,
        )
    except FileNotFoundError as exc:
        raise _CurlTransportError("系统未找到 curl 命令。") from exc
    except subprocess.TimeoutExpired as exc:
        raise _CurlTransportError("curl 连接 DeepSeek 超时。") from exc

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise _CurlTransportError(
            f"curl 返回代码 {result.returncode}：{detail[:200]}"
        )

    try:
        response_body, status_text = result.stdout.rsplit(status_marker, 1)
        status_code = int(status_text.strip())
    except (ValueError, TypeError) as exc:
        raise _CurlTransportError("curl 返回了无法识别的 HTTP 结果。") from exc

    request = httpx.Request(method, url)
    return httpx.Response(
        status_code,
        content=response_body,
        request=request,
    )


def _request_with_curl_fallback(
    method: str,
    path: str,
    api_key: str,
    json_body: dict | None,
    original_error: Exception,
) -> httpx.Response:
    """Python HTTPS 失败后使用 curl，并保留可读的最终错误。"""
    try:
        response = _request_with_system_curl(
            method,
            path,
            api_key,
            json_body=json_body,
        )
    except _CurlTransportError as curl_error:
        raise ResumeOptimizationError(
            "DeepSeek HTTPS 连接失败。已尝试 Python 直连、系统证书、"
            "系统代理和系统 curl。"
            f"Python 原因：{_safe_transport_detail(original_error, api_key)}；"
            f"curl 原因：{_safe_transport_detail(curl_error, api_key)}"
        ) from curl_error

    if response.is_error:
        raise ResumeOptimizationError(
            _status_error_message(response.status_code, response)
        )
    return response


def _status_error_message(status_code: int, response: httpx.Response) -> str:
    """按照 DeepSeek 官方状态码返回具体中文提示。"""
    messages = {
        400: "DeepSeek 请求格式不正确。",
        401: "DeepSeek API Key 无效，请重新创建或检查密钥。",
        402: "DeepSeek 账户余额不足，请充值后重试。",
        422: "DeepSeek 请求参数无效。",
        429: "DeepSeek 请求过快，请稍等一分钟后重试。",
        500: "DeepSeek 服务器内部错误，请稍后重试。",
        503: "DeepSeek 当前负载过高，请稍后重试。",
    }
    message = messages.get(status_code, f"DeepSeek 返回错误状态 {status_code}。")

    try:
        error_data = response.json().get("error", {})
        detail = error_data.get("message", "") if isinstance(error_data, dict) else ""
    except (ValueError, AttributeError):
        detail = ""

    if detail and status_code in (400, 422):
        return f"{message} 服务器说明：{detail}"
    return message


def _request_deepseek(
    method: str,
    path: str,
    api_key: str,
    json_body: dict | None = None,
) -> httpx.Response:
    """直接请求 DeepSeek；对瞬时网络错误和服务器繁忙进行有限重试。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt, connection_mode in enumerate(CONNECTION_MODES, start=1):
        try:
            with _create_http_client(connection_mode) as client:
                response = client.request(method, path, headers=headers, json=json_body)

            if response.status_code in (500, 503) and attempt < MAX_ATTEMPTS:
                time.sleep(attempt)
                continue
            if response.is_error:
                raise ResumeOptimizationError(
                    _status_error_message(response.status_code, response)
                )
            return response
        except ResumeOptimizationError:
            raise
        except httpx.TimeoutException as exc:
            if attempt < MAX_ATTEMPTS:
                time.sleep(attempt)
                continue
            return _request_with_curl_fallback(
                method,
                path,
                api_key,
                json_body,
                exc,
            )
        except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            if attempt < MAX_ATTEMPTS:
                time.sleep(attempt)
                continue
            return _request_with_curl_fallback(
                method,
                path,
                api_key,
                json_body,
                exc,
            )

    raise ResumeOptimizationError("DeepSeek 请求未完成，请稍后重试。")


def _build_private_resume_input(resume_data: dict) -> str:
    """只选择优化必需的信息，排除个人联系方式。"""
    safe_fields = {
        "目标岗位": resume_data.get("target_role", ""),
        "岗位要求": resume_data.get("job_description", ""),
        "原始自我介绍": resume_data.get("summary", ""),
        "教育经历": resume_data.get("education", ""),
        "工作或实习经历": resume_data.get("work_experience", ""),
        "项目经历": resume_data.get("project_experience", ""),
        "技能与证书": resume_data.get("skills", ""),
    }
    return json.dumps(safe_fields, ensure_ascii=False, indent=2)


def test_deepseek_connection(api_key: str) -> str:
    """验证密钥和模型列表，不发送简历内容，也不生成文字。"""
    cleaned_key = api_key.strip()
    if not cleaned_key:
        raise ResumeOptimizationError("请先在左侧填写 DeepSeek API Key。")

    try:
        response = _request_deepseek("GET", "/models", cleaned_key)
        model_ids = {model["id"] for model in response.json().get("data", [])}
        if DEFAULT_MODEL in model_ids:
            return f"连接成功，模型 {DEFAULT_MODEL} 可用。"
        return "DeepSeek 连接和 API Key 均正常。"
    except (ValueError, KeyError, TypeError) as exc:
        raise ResumeOptimizationError("DeepSeek 返回了无法识别的模型列表。") from exc


def optimize_resume_text(
    resume_data: dict,
    api_key: str,
    model: str = DEFAULT_MODEL,
    optimization_guidance: str = "",
) -> str:
    """通过 DeepSeek Chat Completions API 返回优化后的简历正文。"""
    cleaned_key = api_key.strip()
    if not cleaned_key:
        raise ResumeOptimizationError("请先在左侧填写 DeepSeek API Key。")

    user_content = _build_private_resume_input(resume_data)
    if optimization_guidance.strip():
        user_content += (
            "\n\n请根据以下岗位匹配建议优化简历。只改写用户已经提供的真实内容，"
            "不要编造新的经历、技能、数字或证书：\n"
            f"{optimization_guidance.strip()}"
        )

    try:
        response = _request_deepseek(
            "POST",
            "/chat/completions",
            cleaned_key,
            json_body={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": 1600,
                "stream": False,
                "thinking": {"type": "disabled"},
            },
        )
        optimized_text = (
            response.json()["choices"][0]["message"].get("content") or ""
        ).strip()
        if not optimized_text:
            raise ResumeOptimizationError("AI 没有返回文字，请稍后重试。")
        return optimized_text
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise ResumeOptimizationError("DeepSeek 返回了无法识别的简历结果。") from exc


REVIEW_SYSTEM_INSTRUCTIONS = """
你是一名专业、谨慎的中文简历修改顾问。你的任务不是输出一份全新的简历，而是返回可以逐条审阅的修改建议。

必须遵守以下规则：
1. AI 不得编造公司、项目、技能、证书和业绩数字。
2. 只能改写用户已经提供的真实内容。不得新增原文没有的公司、学校、岗位、项目、工具、技能、证书、职责、成果、日期、金额、比例或数量。
3. original_text 必须从对应章节的原文中逐字复制一段连续且完整的句子、段落或项目行，不能概括，不能改字。
4. revised_text 只能改善 original_text 的清晰度、顺序、专业程度和与目标岗位相关的表达，不能改变事实含义。
5. 每条建议只修改一个原文片段。不同建议不得使用相同 original_text，也不要让修改片段互相重叠。
6. 如果没有安全且有价值的修改，可以返回空 changes，不要为了凑数量而修改。
7. 不得输出姓名、手机、邮箱和城市。
8. 只返回一个 JSON 对象，不使用 Markdown，不输出 JSON 之外的解释。

JSON 格式：
{
  "changes": [
    {
      "section": "summary、education、work_experience、project_experience 或 skills",
      "original_text": "从对应原文逐字复制的完整片段",
      "revised_text": "不改变事实的修改后片段",
      "reason": "说明为什么这样修改，不得声称添加了原文不存在的事实"
    }
  ]
}
""".strip()


def _parse_resume_review_result(content: str, resume_data: dict) -> list[dict]:
    """解析结构化修改建议，并执行原文锚定和数字防编造校验。"""
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise ResumeOptimizationError("AI 没有返回有效的逐条修改建议，请重试。")
    try:
        raw_result = json.loads(content[start : end + 1])
    except (json.JSONDecodeError, TypeError) as exc:
        raise ResumeOptimizationError("AI 返回的逐条修改格式不正确，请重试。") from exc

    changes = normalize_review_changes(raw_result, resume_data)
    if not changes:
        raise ResumeOptimizationError(
            "AI 没有给出可安全应用的修改。可能是建议没有对应原文，或包含了原文没有的数字。"
        )
    return changes


def optimize_resume_changes(
    resume_data: dict,
    api_key: str,
    model: str = DEFAULT_MODEL,
    optimization_guidance: str = "",
) -> list[dict]:
    """通过 DeepSeek 返回可逐条接受或拒绝的简历修改建议。"""
    cleaned_key = api_key.strip()
    if not cleaned_key:
        raise ResumeOptimizationError("请先在左侧填写 DeepSeek API Key。")

    user_content = _build_private_resume_input(resume_data)
    if optimization_guidance.strip():
        user_content += (
            "\n\n可以参考以下岗位匹配建议，但仍须遵守不得编造事实的规则：\n"
            f"{optimization_guidance.strip()}"
        )

    try:
        response = _request_deepseek(
            "POST",
            "/chat/completions",
            cleaned_key,
            json_body={
                "model": model,
                "messages": [
                    {"role": "system", "content": REVIEW_SYSTEM_INSTRUCTIONS},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": 3600,
                "stream": False,
                "thinking": {"type": "disabled"},
            },
        )
        content = (
            response.json()["choices"][0]["message"].get("content") or ""
        ).strip()
        return _parse_resume_review_result(content, resume_data)
    except ResumeOptimizationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise ResumeOptimizationError("DeepSeek 返回了无法识别的逐条修改结果。") from exc


MATCH_SYSTEM_INSTRUCTIONS = """
你是一名严谨的招聘岗位匹配分析助手。请比较岗位 JD 与候选人的简历内容，并只依据用户明确提供的信息评分，不能推测或编造经历。

评分总分为 100 分：
1. 核心技能和工具匹配度：40 分。
2. 工作职责、项目经验和行业经验匹配度：35 分。
3. 教育背景、证书和年限要求匹配度：15 分。
4. 业绩证据、岗位关键词和表达完整度：10 分。

必须只返回一个 JSON 对象，不要使用 Markdown，不要输出解释文字。JSON 格式：
{
  "score": 0到100之间的整数,
  "matched_points": ["已经匹配的具体要求"],
  "missing_points": ["简历中没有体现或证据不足的要求"],
  "suggestions": ["在不编造事实的前提下可以怎样修改简历"]
}

当分数低于 80 分时，给出 3 到 6 条具体建议。建议只能帮助用户重写、补充真实信息或调整顺序，不能要求用户编造经历、技能、数字或证书。分数达到 80 分时，suggestions 返回空数组。
""".strip()


IMPORT_STRUCTURE_SYSTEM_INSTRUCTIONS = """
你是一名严谨的中文简历信息归类助手。请把用户提供的原始简历文字，按语义归入指定字段。

必须遵守：
1. 只做归类，不润色、不改写、不总结、不补充事实。
2. 每一条原文只能出现在一个字段中，严禁把同一段复制到多个字段。
3. 工作经历只放正式工作、实习、任职内容；项目经历只放项目、作品、实践、科研项目内容。
4. 教育经历只放学校、专业、学历、毕业时间和教育相关内容。
5. 技能与证书只放技能、工具、语言、证书、培训、奖项和荣誉。
6. 自我介绍只放个人概述、优势、职业简介等内容。
7. 章节标题本身不要写入字段正文；无法判断的内容放入 unclassified，不要猜测。
8. 保留原文事实和换行，输出中文纯文本。

只返回一个 JSON 对象，不要 Markdown，不要解释：
{
  "target_role": "",
  "summary": "",
  "education": "",
  "work_experience": "",
  "project_experience": "",
  "skills": "",
  "unclassified": ""
}
""".strip()


def _build_job_match_input(
    resume_data: dict,
    current_resume_text: str = "",
) -> str:
    """构造不含姓名和联系方式的 JD 匹配输入。"""
    original_sections = {
        "自我介绍": resume_data.get("summary", ""),
        "教育经历": resume_data.get("education", ""),
        "工作或实习经历": resume_data.get("work_experience", ""),
        "项目经历": resume_data.get("project_experience", ""),
        "技能与证书": resume_data.get("skills", ""),
    }
    safe_input = {
        "目标岗位": resume_data.get("target_role", ""),
        "岗位JD": resume_data.get("job_description", ""),
        "当前简历内容": current_resume_text.strip() or original_sections,
    }
    return json.dumps(safe_input, ensure_ascii=False, indent=2)


def _parse_job_match_result(content: str) -> dict:
    """解析并规范化模型返回的 JD 匹配 JSON。"""
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise ResumeOptimizationError("AI 没有返回有效的 JD 匹配结果，请重试。")

    try:
        raw_result = json.loads(content[start : end + 1])
        if not isinstance(raw_result, dict):
            raise ValueError("JD 匹配结果不是 JSON 对象")
        score = int(round(float(raw_result.get("score", 0))))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ResumeOptimizationError("AI 返回的 JD 匹配结果格式不正确，请重试。") from exc

    score = max(0, min(100, score))

    def clean_list(name: str, limit: int = 6) -> list[str]:
        value = raw_result.get(name, [])
        if not isinstance(value, list):
            return []
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ][:limit]

    if score >= 80:
        level = "适配"
    elif score >= 60:
        level = "一般适配"
    else:
        level = "不适配"

    suggestions = clean_list("suggestions") if score < 80 else []
    if score < 80 and not suggestions:
        suggestions = [
            "根据缺失项补充真实经历或技能证据；如果没有相关经历，请不要编造。",
            "把与目标岗位最相关的工作和项目经历放到更靠前的位置。",
            "使用岗位 JD 中与你真实经历一致的关键词重新描述相关内容。",
        ]

    return {
        "score": score,
        "level": level,
        "matched_points": clean_list("matched_points"),
        "missing_points": clean_list("missing_points"),
        "suggestions": suggestions,
    }


def analyze_job_match(
    resume_data: dict,
    api_key: str,
    current_resume_text: str = "",
    model: str = DEFAULT_MODEL,
) -> dict:
    """使用 DeepSeek 分析简历与岗位 JD 的匹配度。"""
    cleaned_key = api_key.strip()
    if not cleaned_key:
        raise ResumeOptimizationError("请先在左侧填写 DeepSeek API Key。")
    if not resume_data.get("job_description", "").strip():
        raise ResumeOptimizationError("请先填写岗位要求或粘贴完整的岗位 JD。")

    try:
        response = _request_deepseek(
            "POST",
            "/chat/completions",
            cleaned_key,
            json_body={
                "model": model,
                "messages": [
                    {"role": "system", "content": MATCH_SYSTEM_INSTRUCTIONS},
                    {
                        "role": "user",
                        "content": _build_job_match_input(
                            resume_data,
                            current_resume_text=current_resume_text,
                        ),
                    },
                ],
                "max_tokens": 1200,
                "stream": False,
                "thinking": {"type": "disabled"},
            },
        )
        content = (
            response.json()["choices"][0]["message"].get("content") or ""
        ).strip()
        return _parse_job_match_result(content)
    except ResumeOptimizationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise ResumeOptimizationError("DeepSeek 返回了无法识别的 JD 匹配结果。") from exc


IMPORT_STRUCTURE_FIELDS = (
    "target_role",
    "summary",
    "education",
    "work_experience",
    "project_experience",
    "skills",
    "unclassified",
)


def _redact_imported_resume_text(
    raw_text: str,
    sensitive_values: list[str] | None = None,
) -> str:
    """在发送给 AI 前隐藏姓名、电话、邮箱和城市。"""
    redacted = raw_text
    redacted = re.sub(
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        "[电子邮箱已隐藏]",
        redacted,
    )
    redacted = re.sub(
        r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)",
        "[手机号码已隐藏]",
        redacted,
    )
    values = sorted(
        {
            str(value).strip()
            for value in (sensitive_values or [])
            if len(str(value).strip()) >= 2
        },
        key=len,
        reverse=True,
    )
    for value in values:
        redacted = redacted.replace(value, "[个人信息已隐藏]")
    return redacted.strip()


def _structured_text_value(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if value is None:
        return ""
    return str(value).strip()


def _parse_import_structure_result(content: str) -> dict[str, str]:
    """解析 AI 归类结果，并去除字段之间的重复行。"""
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise ResumeOptimizationError("AI 没有返回有效的简历归类结果，请重试。")
    try:
        raw_result = json.loads(content[start : end + 1])
        if not isinstance(raw_result, dict):
            raise ValueError("归类结果不是 JSON 对象")
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ResumeOptimizationError("AI 返回的简历归类格式不正确，请重试。") from exc

    result: dict[str, str] = {}
    lines_seen: set[str] = set()
    for field_name in IMPORT_STRUCTURE_FIELDS:
        value = _structured_text_value(raw_result.get(field_name, ""))
        unique_lines: list[str] = []
        for line in value.splitlines():
            cleaned_line = line.strip()
            if not cleaned_line:
                continue
            comparison_key = re.sub(r"[\s，,。；;：:·•\-—_]+", "", cleaned_line).lower()
            if len(comparison_key) >= 6 and comparison_key in lines_seen:
                continue
            if len(comparison_key) >= 6:
                lines_seen.add(comparison_key)
            unique_lines.append(cleaned_line)
        result[field_name] = "\n".join(unique_lines).strip()
    return result


def classify_imported_resume(
    raw_text: str,
    api_key: str,
    sensitive_values: list[str] | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, str]:
    """使用 DeepSeek 将脱敏后的原始简历按语义归入标准章节。"""
    cleaned_key = api_key.strip()
    if not cleaned_key:
        raise ResumeOptimizationError("请先在左侧填写 DeepSeek API Key。")
    safe_text = _redact_imported_resume_text(raw_text, sensitive_values)
    if len(safe_text) < 10:
        raise ResumeOptimizationError("没有足够的简历文字可供 AI 识别。")

    try:
        response = _request_deepseek(
            "POST",
            "/chat/completions",
            cleaned_key,
            json_body={
                "model": model,
                "messages": [
                    {"role": "system", "content": IMPORT_STRUCTURE_SYSTEM_INSTRUCTIONS},
                    {"role": "user", "content": safe_text},
                ],
                "max_tokens": 2600,
                "stream": False,
                "thinking": {"type": "disabled"},
            },
        )
        content = (
            response.json()["choices"][0]["message"].get("content") or ""
        ).strip()
        return _parse_import_structure_result(content)
    except ResumeOptimizationError:
        raise
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise ResumeOptimizationError("DeepSeek 返回了无法识别的简历归类结果。") from exc
