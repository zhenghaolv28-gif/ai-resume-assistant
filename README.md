# AI 简历助手

一个适合零基础用户的 Streamlit 简历优化工具。

## 当前功能

1. 填写姓名、联系方式、求职目标和个人经历。
2. 使用 DeepSeek 优化中文简历描述，不编造用户未提供的经历。
3. 只把岗位和经历文字发送给 AI，姓名、手机、邮箱和城市不会发送。
4. 每位使用者填写自己的 DeepSeek API Key，Key 不写入项目文件。
5. 上传 JPG/PNG 证件照并在简历预览中查看。
6. 一键导出带证件照的 Word 和 PDF 简历。
7. 使用招聘网站常见的专业单栏模板，统一个人信息和经历章节。
8. 自动清理 Markdown 符号、表情和异常字符，并在 PDF 中嵌入中文字体。

## 本地运行

Windows 用户可以双击 `启动简历助手.bat`。

也可以在项目目录运行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## 分享到互联网

本项目可以部署到 Streamlit Community Cloud：

1. 把项目上传到 GitHub。
2. 在 Streamlit Community Cloud 中选择该仓库。
3. 入口文件选择 `app.py`。
4. 部署完成后分享生成的 `streamlit.app` 链接。

分享版不需要配置服务器 API Key，每位使用者在页面侧栏填写自己的 DeepSeek API Key。

## 隐私与安全

- 不要把真实 API Key 写进代码、`.env.example` 或 GitHub。
- 本地 `.env`、虚拟环境和 Streamlit Secrets 已通过 `.gitignore` 排除。
- 简历内容目前只保存在当前 Streamlit 会话中，不写入项目文件。
- 证件照只保存在当前 Streamlit 会话，不会发送给 DeepSeek。

## 后续功能

- 生成不同岗位版本。
