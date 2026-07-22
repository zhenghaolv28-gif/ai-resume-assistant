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
9. 保存一份完整“主简历”，作为所有岗位版本的基础资料。
10. 从主简历创建多个独立岗位版本，分别保存目标岗位、JD、匹配结果和 AI 优化结果。
11. 在主简历和岗位版本之间切换，修改岗位版本时不会覆盖主简历。
12. 下载主简历备份，或一次下载包含全部岗位版本的 JSON 备份。
13. AI 优化后逐条显示原文、修改后文字、新增内容、删除内容和修改原因。
14. 每条 AI 建议可以单独接受或拒绝，只有接受的修改会进入最终导出稿。
15. AI 不得编造公司、项目、技能、证书和业绩数字；程序还会拦截无法对应原文或凭空新增数字的修改。
16. 支持扫描版 PDF 和 JPG、PNG、WEBP、BMP、TIFF 图片简历，无法直接读取文字时会自动使用中英文 OCR。

## 本地运行

Windows 用户可以双击 `启动简历助手.bat`。

图片和扫描 PDF 会自动选择可用的本地 OCR。Windows 本机优先使用 Tesseract；如果没有安装 Tesseract 或相关 Python 包，会自动切换到 Windows 自带的离线 OCR。Streamlit 云端会根据 `packages.txt` 自动安装中英文 Tesseract。

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
- 扫描 PDF 和图片的 OCR 在当前应用服务器内完成；只有用户主动勾选 AI 精准识别时，隐藏联系方式后的文字才会发送给 DeepSeek。
- 岗位版本同样只保存在当前会话；关闭会话前建议下载“全部版本备份”。

## 推荐使用流程

1. 导入或填写完整经历，保存为主简历。
2. 点击“新建岗位版本”，填写版本名称、目标岗位和岗位 JD。
3. 在岗位版本中进行 JD 匹配、AI 优化和导出。
4. 针对其他岗位继续创建新版本，主简历不会被覆盖。
