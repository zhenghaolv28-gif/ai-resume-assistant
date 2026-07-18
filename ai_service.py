"""调用 DeepSeek API 优化简历文字。"""

import json
import os
import shutil
import ssl
import subprocess
import time

import httpx


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
) -> str:
    """通过 DeepSeek Chat Completions API 返回优化后的简历正文。"""
    cleaned_key = api_key.strip()
    if not cleaned_key:
        raise ResumeOptimizationError("请先在左侧填写 DeepSeek API Key。")

    try:
        response = _request_deepseek(
            "POST",
            "/chat/completions",
            cleaned_key,
            json_body={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                    {"role": "user", "content": _build_private_resume_input(resume_data)},
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
