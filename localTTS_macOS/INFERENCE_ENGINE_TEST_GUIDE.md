# InferenceEngine 重构 — 统一手动测试指南

本次重构（ADR-001，见 `CONTEXT.md`）把推理收敛成单一 `InferenceEngine`：单进程、单次模型加载、GPU 串行、读优先，并修了一个缓存串音 bug。**代码 + 单元测试已全部完成（54 passed）**，但「真实出声」无法由单测覆盖 —— 下面这一套就是把所有 🔊 验收一次性跑完。

整套大约 **15–20 分钟**。建议按顺序做，每步记下 ✅/❌。

---

## 0. 准备（2 分钟）

### 0.1 跑单元测试（应全绿）
```bash
cd /Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend
/Users/funanhe/miniconda3/envs/gemini/bin/python -m pytest core/tests/ -q
```
**通过标准**：`54 passed`。

### 0.2 起后端
两种方式选一：

**A. 跑整个 App（推荐，最接近真实）** — 用 Xcode 构建并运行 `QwenTTS`（模型在 `~/Library/Application Support/QwenTTS/Models`）。下面凡是「点 X 按钮」都走 App。

**B. 只起后端 + curl（快循环）**：
```bash
source activate gemini
cd /Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend
python core/backend.py    # 监听 127.0.0.1:8001
```
> 起来后**立刻看日志第一屏**：应有 `[InferenceProcess] 启动成功, PID: …`，且**不应**再出现旧的 `[TTSEngine] …` 字样（那个类已删）。主进程不再加载 mlx。

辅助观测窗口（整场开着）：
```bash
# 实时看后端日志关键字
# （App 模式日志在 Console.app 搜 "InferenceProcess"/"InferenceEngine"）
open -a "Activity Monitor"   # 用来数“有几个 python 进程吃了几个 GB 内存”
```

---

## 1. 🔊 朗读出声 + 一键试音（Step 3/4 验收）

**目的**：确认 `engine.run_loop` 是 `inference_worker` 的行为保持替换，朗读链路照常出声。

- **App**：主窗口 Console 粘一段中文，点播放 → 应正常出声、卡拉OK 滚动正常。
- **curl**：
  ```bash
  curl -s -X POST 127.0.0.1:8001/selftest/voice
  ```
  **通过标准**：返回 `{"ok": true, "frames": N}`（N>0），且**耳朵真的听到**「你好，欢迎使用 QwenTTS。」。
  ❌ 若返回 `{"ok": false, "error": ...}` → 把 error 文本贴给我。

再测一段普通朗读：
```bash
curl -s -X POST 127.0.0.1:8001/read -H 'Content-Type: application/json' \
  -d '{"text":"这是一段用于验收的测试朗读，看看声音是否连续自然。"}'
# 听是否连续出声；然后停止
curl -s -X POST 127.0.0.1:8001/stop
```
**通过标准**：连续清晰出声；`/stop` 后立即静音。

---

## 2. 🔊 缓存串音修复（本次重点 bug）

**目的**：同一句话用不同音色，**不能**再放出上一次的音色（旧 key 只按文字算 md5 会串音）。

1. 用 **Serena** 读一句：
   ```bash
   curl -s -X POST 127.0.0.1:8001/read -H 'Content-Type: application/json' \
     -d '{"text":"音色测试同一句话。","voice":"Serena"}'
   ```
   听清楚（女声）。`/stop`。
2. 用 **Ryan** 读**完全相同**的一句：
   ```bash
   curl -s -X POST 127.0.0.1:8001/read -H 'Content-Type: application/json' \
     -d '{"text":"音色测试同一句话。","voice":"Ryan"}'
   ```
   **通过标准**：第 2 次是**男声 Ryan**，不是重放第 1 次的女声。
   ❌ 若第 2 次仍是女声 → 串音未修复，告诉我。

> App 里等价操作：设置里切换音色后，读同一句话。

---

## 3. 🔊 缓存命中 = 不烧 GPU

**目的**：第二次读同一句（同音色）应**秒回**且不跑 GPU。

```bash
# 第一次（会真推理，慢）
time curl -s -X POST 127.0.0.1:8001/read -H 'Content-Type: application/json' \
  -d '{"text":"缓存命中验收用的一句话。","voice":"Serena"}' >/dev/null
curl -s -X POST 127.0.0.1:8001/stop >/dev/null
# 第二次（应命中缓存，明显更快；日志无新的“模型生成”，Activity Monitor 里 GPU/能耗不飙）
time curl -s -X POST 127.0.0.1:8001/read -H 'Content-Type: application/json' \
  -d '{"text":"缓存命中验收用的一句话。","voice":"Serena"}' >/dev/null
curl -s -X POST 127.0.0.1:8001/stop >/dev/null
```
**通过标准**：第二次明显更快、依然出声、机器不发热。

---

## 4. 🔊 暂停 / 继续 / 进度（回归）

App 里朗读一段长文，依次点 **暂停 → 继续 → 拖动进度/seek**。
**通过标准**：暂停立刻静音、继续从原处接上、seek 跳转正确，卡拉OK 文字不错乱。
（curl 等价：`/pause`、`/resume`、`/seek`。）

---

## 5. 🔊 播客生成（Step 5 验收）

> 需要在「AI 引擎」页配置好 LLM key（双主持播客要 LLM 生成对话稿）。

1. App 内容中心选一篇文章 → **生成播客（单篇）**。
2. 等待完成（后台生成，可继续用别的功能）。
3. 完成后**播放生成的 .wav**。

**通过标准**：
- 生成成功，产出 `.wav`（在 `~/Library/Application Support/QwenTTS/Podcasts/`）。
- 播放**双主持音色正确**（Serena 女声 / Ryan 男声**不串**）。
- 生成过程中后端日志能看到 `chunk_00000.npy`、`chunk_00001.npy` … 陆续出现在 `…/PodcastChunks/single_*/`（这就是引擎在写）；**不应**出现旧的「播客子进程加载模型」式日志。
- ❌ 若某 chunk 卡住，去 `…/PodcastChunks/single_*/` 看有没有 `chunk_xxxxx.npy.err` 文件，把里面的报错贴给我。

---

## 6. 🔊🔥 关键：并发「朗读 + 播客」只有一份模型（降温验收）

**这是整次重构的核心目标。**

1. 开始生成一集播客（Step 5），**不要等它完成**。
2. 生成过程中，**同时**在 Console 朗读一段文字。
3. 观察：
   - **朗读能正常插进来出声**（最多等当前一个播客 chunk 跑完，约一两句话的时间）—— 读优先生效。
   - 打开 **Activity Monitor → 内存**，按内存排序找 `python`/`Python` 进程：
     - 应只有**一个**推理进程吃掉大块内存（约模型大小，几个 GB）。
     - 播客那个 orchestration 子进程**内存很小**（它现在只轮询文件，不加载模型）。
   - 机器**不应**像以前那样两个推理同时抢 GPU 而明显发烫/风扇狂转。

**通过标准**：朗读可插队 + 内存里只有一份模型 + 不双重发热。
❌ 若看到两个都吃了几 GB 的 python 进程 → 说明还有第二份模型，告诉我。

---

## 7. 🔊 模型切换 / 空闲卸载（回归）

- 在设置里切换性能档（`balanced` ↔ `quiet`，quiet 用 0.6B）后朗读：日志应出现一次 `[InferenceEngine] 模型切换 -> …`，之后出声正常。
- 放着不动 >10 分钟（600s）后再看：日志应出现 `[InferenceEngine] 空闲自动卸载模型…`，下次朗读会重新加载（首句稍慢）。

---

## 完成后

- 全部 ✅：在 `CONTEXT.md` §4 把各 🔊 步骤的「待用户 smoke」标注划掉即可；这次重构落地。
- 任一 ❌：把对应步骤号 + 现象/日志/`.err` 内容发我，我来定位（这些都在我能改的代码范围内）。

> 提醒：本次所有改动**尚未提交**。验收通过后建议按 `CONTEXT.md` 的 tiny-commit 思路分步提交（Step 0–2 一个 commit、Step 3–6 一个 commit），或让我来帮你提交。
