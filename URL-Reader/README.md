# QwenTTS — URL Reader 网页净化朗读指南

本模块是 QwenTTS 生态中专门用于 **“输入网页 URL，自动抓取、净化正文并一键朗读”** 的核心控制与测试工具。它将重型的网页提取、WAF 绕过、正文去噪逻辑留在本地 Python 侧，直接利用正在运行的 macOS 状态栏应用进行语音播放。

当前 URL-Reader 已拆成两层：`reader_service.py` 是可被 QwenTTS-App 后端直接调用的输入管线；`read_url_cli.py` 只是手工调试用的命令行壳。浏览器插件触发 `/read_url` 时不再每次拉起 CLI 子进程。

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
 reader_service 缓存与 Gemini 处理模式 (可选：翻译 / 双人翻译 / 双人总结)
      ↓
 投喂给 QwenTTS-App (8001端口) ──> service 层分发到朗读、稍后朗读或后台播客生成
```

1. **多重网络穿透 (urllib.request + 代理退避 + 浏览器联动)**：
   * **原生 `urllib` 绕过 TLS 拦截**：弃用容易暴露 TLS 握手特征的 `requests` 库，改用原生 `urllib` 并注入伪造的 Chrome Headers。
   * **代理自动退避**：若直连受阻（如 429 报错），脚本会自动检测本地运行的 Clash（`7890`）和 V2Ray（`1087`）等代理服务进行重试。
   * **Chrome 缓存联动**：若前两层均受阻，只要您的 Chrome 浏览器正开着该网页，脚本会直接通过 AppleScript 强行抓取当前活动页面中的 HTML 源码，实现 0网络请求穿透。
2. **正文去噪净化 (Defuddle CLI)**：
   * 抓取下来的 HTML 喂给全局的 `defuddle parse --md` 净化器，完美滤除广告、弹窗及无关的脚本，只提取纯净的 Markdown 格式正文。
3. **本地 TTS 服务分工**：
   * `POST /read`：直接朗读当前正文。
   * `POST /save_for_later`：保存到 QwenTTS-App 的“稍后朗读”列表。
   * `POST /generate_single_podcast`：启动后台单篇 podcast 生成，成品写入项目根目录 `podcasts/`。
4. **缓存与任务状态**：
   * `URL-Reader/cache/source_{hash}.md`：缓存抓取/清洗后的正文。
   * `URL-Reader/cache/{mode}_{hash}.md`：缓存 Gemini 翻译、双人翻译或双人总结结果。
   * `QwenTTS-App/data/url_jobs.json`：记录 URL 任务 `queued/running/done/failed` 和 `fetching/parsing/gemini/dispatching` 阶段。

---

## 📂 2. 文件结构与分工

* **控制说明文档**：[README.md](file:///Users/funanhe/00_MyCode/TTS/URL-Reader/README.md)（本文件）
* **可复用输入管线**：[reader_service.py](file:///Users/funanhe/00_MyCode/TTS/URL-Reader/reader_service.py)
* **独立命令行测试工具**：[read_url_cli.py](file:///Users/funanhe/00_MyCode/TTS/URL-Reader/read_url_cli.py)（薄 CLI 壳，复用 `reader_service.py`）

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

### B. 执行网页朗读 / 生成
在项目根目录下，直接在终端中运行 [read_url_cli.py](file:///Users/funanhe/00_MyCode/TTS/URL-Reader/read_url_cli.py) 并传入网页 URL。默认模式是 `--podcast-discuss`，会先通过 Gemini 生成双人总结稿，再投喂给 QwenTTS-App。

```bash
cd /Users/funanhe/00_MyCode/TTS/URL-Reader

# 1. 双人总结稿朗读（默认）
# 💡 提示：在 zsh 中，包含问号 '?' 的 URL 建议用单引号包裹，防止 zsh 误认作通配符报错
python read_url_cli.py '<您要朗读的网页URL>'

# 2. 直接朗读原始正文
python read_url_cli.py '<您要朗读的网页URL>' --original

# 3. 翻译成中文后朗读
python read_url_cli.py '<您要朗读的网页URL>' --translate

# 4. 生成双人翻译稿后朗读
python read_url_cli.py '<您要朗读的网页URL>' --podcast-trans

# 5. 保存到稍后朗读，不立刻播放
python read_url_cli.py '<您要朗读的网页URL>' --translate --save

# 6. 启动后台单篇播客生成
python read_url_cli.py '<您要朗读的网页URL>' --podcast-discuss --podcast

# 7. 使用浏览器插件上传的本地 HTML 文件解析
python read_url_cli.py '<原始URL>' --html-file /path/to/page.html --podcast-discuss
```

**运行与翻译示例**：
```bash
python read_url_cli.py 'https://aeon.co/essays/why-did-measuring-earths-true-shape-matter-for-science' -t
```

运行后流程：
1. **保存临时源码**：脚本将抓取净化的原始网页正文写入 `temp_source.md`。
2. **AI 智能处理**：除 `--original` 外，脚本会调用 [gemini_engine.py](file:///Users/funanhe/00_MyCode/TTS/URL-Reader/gemini_engine.py) 执行中文翻译、双人翻译或双人总结。
3. **保存处理结果**：将处理后的正文写入 `temp_translated.md`。
4. **投喂本地服务**：CLI 模式下根据参数调用 `QwenTTS-App` 的 `/read`、`/save_for_later` 或 `/generate_single_podcast`；后端 `/read_url` 模式下则直接在进程内分发到对应 endpoint。

## 4. 模式参数速查

| 参数 | 行为 |
|---|---|
| `--original` / `-o` | 保留抓取正文，不调用 Gemini |
| `--translate` / `-t` | 翻译成中文 |
| `--podcast-trans` / `-pt` | 生成双人翻译稿 |
| `--podcast-discuss` / `-pd` | 生成双人总结稿；当前默认模式 |
| `--save` / `-s` | 保存到 QwenTTS-App 稍后朗读 |
| `--podcast` / `-p` | 启动后台单篇 podcast 生成 |
| `--html-file PATH` | 从本地 HTML 文件提取正文，供 extension 上传页面源码时使用 |

YouTube 链接会优先提取字幕；在非双人播客模式下，YouTube 默认使用 Ryan 男声朗读。
