# QwenTTS — URL Reader 网页净化朗读指南

本模块是 QwenTTS 生态中专门用于 **“输入网页 URL，自动抓取、净化正文并一键朗读”** 的核心控制与测试工具。它将重型的网页提取、WAF 绕过、正文去噪逻辑留在本地 Python 侧，直接利用正在运行的 macOS 状态栏应用进行语音播放。

---

## 🛠️ 1. 核心架构与技术方案

网页朗读看似简单，但在实际运行中会遇到反爬流控（HTTP 429）和网页正文掺杂广告的痛点。本方案采用了以下三层防御体系：

```
 用户输入 URL 
      ↓
 模拟 Chrome 请求头 (urllib.request) ──[失败]──> 本地代理检测 (Clash 7890/V2Ray 1087) ──[失败]──> Chrome 浏览器缓存提取 (AppleScript)
      ↓ (获取 HTML)
 临时写入本地 html 文件
      ↓
 调用 defuddle 净化 ──> 提取干净的 Markdown 格式文章正文 (去除广告、边栏、导航栏)
      ↓
 投喂给 QwenTTS-App (8001端口) ──> 分片推理并使用原生声卡硬件 (sounddevice) 朗读
```

1. **多重网络穿透 (urllib.request + 代理退避 + 浏览器联动)**：
   * **原生 `urllib` 绕过 TLS 拦截**：弃用容易暴露 TLS 握手特征的 `requests` 库，改用原生 `urllib` 并注入伪造的 Chrome Headers。
   * **代理自动退避**：若直连受阻（如 429 报错），脚本会自动检测本地运行的 Clash（`7890`）和 V2Ray（`1087`）等代理服务进行重试。
   * **Chrome 缓存联动**：若前两层均受阻，只要您的 Chrome 浏览器正开着该网页，脚本会直接通过 AppleScript 强行抓取当前活动页面中的 HTML 源码，实现 0网络请求穿透。
2. **正文去噪净化 (Defuddle CLI)**：
   * 抓取下来的 HTML 喂给全局的 `defuddle parse --md` 净化器，完美滤除广告、弹窗及无关的脚本，只提取纯净的 Markdown 格式正文。

---

## 📂 2. 文件结构与分工

* **控制说明文档**：[README.md](file:///Users/funanhe/00_MyCode/TTS/URL-Reader/README.md)（本文件）
* **独立命令行测试工具**：[read_url_cli.py](file:///Users/funanhe/00_MyCode/TTS/URL-Reader/read_url_cli.py)（包含完整 Type Hints 的 Python 控制脚本）

---

## 🚀 3. 快速上手使用

在进行网页朗读前，请确保满足以下依赖条件：

### A. 依赖准备
1. **安装 Defuddle 命令行净化工具**：
   ```bash
   npm install -g defuddle
   ```
2. **确认 QwenTTS 后端已在运行**：
   确保您在终端或状态栏中已经启动了 [QwenTTS-App](file:///Users/funanhe/00_MyCode/TTS/QwenTTS-App) 服务（占用 8001 端口）。
   ```bash
   cd /Users/funanhe/00_MyCode/TTS/QwenTTS-App
   python app.py
   ```

### B. 执行网页朗读
在项目根目录下，直接在终端中运行 [read_url_cli.py](file:///Users/funanhe/00_MyCode/TTS/URL-Reader/read_url_cli.py) 并传入网页 URL。您还可以加入 `-t` 或 `--translate` 参数让其通过 Gemini 自动翻译为中文再朗读：

```bash
cd /Users/funanhe/00_MyCode/TTS/URL-Reader

# 1. 直接朗读原文 (如果是 YouTube 视频，将提取原文语种字幕朗读)
# 💡 提示：在 zsh 中，包含问号 '?' 的 URL 建议用单引号包裹，防止 zsh 误认作通配符报错
python read_url_cli.py '<您要朗读的网页URL>'

# 2. 翻译成中文后朗读 (调用 Gemini 翻译并输出临时文件)
python read_url_cli.py '<您要朗读的网页URL>' --translate
```

**运行与翻译示例**：
```bash
python read_url_cli.py 'https://aeon.co/essays/why-did-measuring-earths-true-shape-matter-for-science' -t
```

运行后流程：
1. **保存临时源码**：脚本将抓取净化的原始网页正文写入到本地临时文件 `temp_source.md` 中。
2. **AI 智能翻译**：检测到翻译参数，脚本读取 `temp_source.md` 内容并请求 Gemini 翻译（利用 [gemini_engine.py](file:///Users/funanhe/00_MyCode/TTS/URL-Reader/gemini_engine.py) 的 Pool 与降级算法）。
3. **保存临时译文**：将翻译得到的中文写入到本地临时文件 `temp_translated.md` 中。
4. **投喂并朗读**：打印翻译后的前 400 字预览，并将译文发送到 `QwenTTS-App` (8001端口)，立刻开始中文语音朗读。
