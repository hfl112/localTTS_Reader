# CONTEXT.md — 领域模型与架构决策

> 本文件是项目的**领域术语表 + 架构决策记录 + 进行中的迁移计划**。
> 架构复查（`improve-codebase-architecture`）与 `/loop` 迁移都以本文件为单一真相来源。
> 命名新模块/概念时在此登记；被否决的方案记入「已否决」一节，避免日后重提。

---

## 1. 架构词汇表（来自 codebase-design）

严格使用这些词，**不要**漂移成 component / service / API / boundary / layer / wrapper：

- **module（模块）** — 一个有清晰职责的代码单元。
- **interface（接口）** — 模块对外暴露的调用面。
- **depth / deep / shallow（深/浅）** — 深模块 = 简单接口藏复杂实现；浅模块 = 接口几乎和实现一样复杂。
- **seam（缝）** — 两个模块相接、且一侧可替换的地方。
- **adapter（适配器）** — 实现某个 seam 一侧的具体实现。**一个适配器 = 假想的 seam，两个 = 真实的 seam。**
- **leverage（杠杆）** — 一个模块为其接口大小做了多少事。
- **locality（局部性）** — 相关逻辑住在一起；当调用方必须跨多个模块协调时，bug 就藏在协调处。
- **deletion test（删除测试）** — 删掉这个模块，复杂度是**被集中**（好 seam）还是**只是挪走**（浅）？

## 2. 项目领域术语

- **read path（朗读路径）** — 用户读一段文字，低延迟流式出声：route → 推理 → `audio_q` → `audio_feeder_thread` → player。
- **podcast（播客）** — 长批处理：把一篇文章拆成多 chunk（含 Serena/Ryan 对话音色），合成后拼成 WAV 文件。编排（任务跟踪、暂停/电量策略、WAV 拼装）属 `PodcastService`。
- **出声证据（audio evidence）** — `SharedState.audio_frames` / `error_buf` / `status_code`，被 `/snapshot` 与一键试音用来判断「是否真的出声」，防「试音假阳性」。**任何重构必须保留这套证据可跨进程读。**
- **performance profile** — `fast` / `balanced` / `quiet`，名字是单一真相（见 `performance.py`）。
- **InferenceEngine** —（本次新增）拥有「文字→声音」全部推理生命周期的深模块。见 §3。
- **ModelBackend** —（本次新增）InferenceEngine 内部一道窄缝，只包住碰 MLX/GPU 的那一下。两个适配器：`MLXBackend`（生产）、`FakeBackend`（测试）。
- **TTSRequest** —（本次新增）提交给引擎的请求：text、config、priority、id、缓存 key 输入。

---

## 3. 架构决策 ADR-001：InferenceEngine seam

**状态**：已接受（2026-06-25，经 grilling 全树确认）
**背景**：推理逻辑散在 `inference_worker`（backend.py:185）、`tts_engine.py`、各 podcast worker 三处。`inference_worker` **不拿 `gpu_lock`**，`gpu_lock`（podcast_service.py:331）只在 podcast worker 间互斥 —— 于是**朗读与播客会同时撞同一块 GPU**，Apple Silicon 统一内存上互相 thrash、空转发热。三处各自 `load model`，互不协调。

**决定**（6 项）：

1. **承重目标 = GPU 串行 + 单次模型加载。** 朗读与播客收敛到同一个推理进程/队列，模型只加载一次，GPU 天然串行。降温的根在此。可测性是副产品。
2. **优先级 = 朗读优先 + 播客逐 chunk 低优先提交。** 播客不再独占引擎，而是把自己拆成 chunk 任务按低优先排队；worker 永远先服务 pending 朗读。一次新朗读最多等「当前这个播客 chunk」跑完即可插队。复用现有 `task_id` 失效机制。**不靠多进程**（单 GPU 多进程不真并行，只会 thrash）。
3. **接口 = 唯一入口 `synthesize(request)`。** 调用方提交 `TTSRequest`，拿到一个客户端 handle，迭代音频帧 + 终态（done/error）。队列、`task_id` 记账、`CHUNK_DONE` 哨兵、缓存查找、归一化全藏在内部。朗读把帧转到 `audio_q`，播客把帧排空到 WAV —— 两者用法一致。
4. **缓存 = 引擎内部读穿（read-through）。** `synthesize` 内部先查缓存：命中直接迭代缓存帧（不跑 GPU、不发热），未命中才真推理并顺手存。**修缓存 key bug**：现 key 只对 text 算 md5（`get_text_hash`），同句不同音色会命中错音色录音 → 新 key = `text + voice + model + lang`。
5. **生命周期 = 引擎拥有整个进程循环。** `while True` 取任务、优先级排队、`task_id` 失效、模型切换、空闲 600s 卸载、写 VRAM/状态/出声证据到 `SharedState` —— 全归引擎。`backend.py` 只负责启动它、路由提交请求（顺带瘦身，呼应架构复查 #1）。
6. **测试缝 = 窄 `ModelBackend` 口子 + 两适配器。** 缝只包住碰 MLX/GPU 的一下（`load` / `generate` / `unload`）。队列、缓存、优先级、归一化、串音 key 修复、空闲卸载全在缝**上面**，用 `FakeBackend`（返回正弦波、不碰 GPU）即可完整测到。生产用 `MLXBackend`。

**形状**：

```
朗读路由 ─┐
          ├─→ engine.synthesize(request) ──┐  （唯一的门）
PodcastService ─┘                          ▼
        ┌────────────────────────────────────────────┐
        │ InferenceEngine (deep module)                │
        │  优先级队列 · task_id 失效 · 模型切换/空闲卸载  │
        │  读穿缓存(新key) · 归一化 · 写出声证据→SharedState│
        │            │ ModelBackend (窄缝)              │
        └────────────┼─────────────────────────────────┘
              MLXBackend │ FakeBackend
             (生产·烧GPU) │ (测试·正弦波·不烧)
```

**删除测试结论**：`tts_engine.py` 原样删掉不损失什么（浅）；真正赚到 seam 的是那个**深**引擎 + 两个真实适配器。

---

## 4. 迁移计划（/loop 按此逐步执行）

**总原则**：tiny commits，每步独立可测、可回滚。每步做完跑 `cd backend && python -m pytest core/tests/ -v`，绿了再进下一步。
**测试约束**：测试不加载真 MLX（CLAUDE.md 既有约定），新逻辑全部用 `FakeBackend` 覆盖。
**音频约束**：凡标 🔊 的步骤改动真实出声路径，pytest 测不到，需**用户手动 smoke（朗读 + 一键试音 + 生成一集播客）确认**后才算通过 —— `/loop` 在这些步骤完成代码 + 单测后**暂停并请用户验证**，不要自行勾选 🔊 验收项。

进度图例：`[ ]` 待办 · `[~]` 进行中 · `[x]` 完成（代码+单测+（如适用）用户 smoke 均通过）

---

### [x] Step 0 — 基线
- **目标**：确认起点干净。
- **改动**：无。
- **验收**：`pytest core/tests/ -v` 全绿；记录当前通过数作为基线。

### [x] Step 1 — 引入 `ModelBackend` 窄缝 + 两适配器
- **目标**：把碰 MLX/GPU 的代码隔离到一道窄缝后面。
- **改动**：
  - 新建 `backend/core/inference/model_backend.py`：定义 `ModelBackend` 协议（`load(model_path)` / `generate(text, generate_kwargs) -> Iterator[np.ndarray]` / `unload()`）。
  - `MLXBackend`：把 `tts_engine.py` 里碰 `mlx_audio.load_model` 和 `model.generate(...)` 的部分搬进来（**只搬碰模型那一下**，参数构建/归一化先留原处不动）。
  - `FakeBackend`：忽略 kwargs，返回固定时长正弦波帧；不 import mlx。
- **验收**：
  - 既有测试仍全绿。
  - 新单测：实例化 `FakeBackend`，`generate` 产出 >0 帧，且**不 import mlx**（可用 `sys.modules` 断言）。

### [x] Step 2 — `InferenceEngine` 接管参数构建 + 归一化 + 读穿缓存
- **目标**：一个对象拥有合成逻辑；修掉串音 key。
- **改动**：
  - 新建 `backend/core/inference/engine.py`：`InferenceEngine(backend: ModelBackend, paths, storage)`。
  - 搬入：`generate_kwargs` 构建（ICL 锁音注入、语种自动检测、`max_tokens`）、归一化（增益/削波到 [-0.98, 0.98]）、缓存查/存/`manage_cache_limit`。
  - **缓存 key 改为** `hash(text + voice + model + lang)`；新增 `cache_key(request)`。
  - 暴露 `synthesize_local(request) -> Iterator[frame]`（同进程、未跨进程版，先单测用）。
- **验收**（全用 `FakeBackend`，不烧 GPU）：
  - 同 text + 不同 voice → 不同 cache key/文件（**串音 bug 回归测试**）。
  - 缓存命中时**不调用** `backend.generate`（用 spy/计数断言）。
  - 输出帧幅值恒在 [-0.98, 0.98]。

### [x] Step 3 🔊 — 引擎拥有进程主循环 + 优先级队列 + 跨进程 `synthesize(request)`
> 代码 + 单测完成；🔊 端到端出声待用户 smoke（见本仓库根 `INFERENCE_ENGINE_TEST_GUIDE.md`）。
- **目标**：引擎拥有生命周期；定义跨进程客户端接口。
- **改动**：
  - `InferenceEngine.run(shared_state)` 成为推理进程入口：`while True` 取任务、**优先级排队（朗读 > 播客）**、`task_id` 失效、模型切换、空闲 600s 卸载、写 `vram_mb/status_code/audio_frames/error_buf`。
  - 定义 `TTSRequest`（text/config/priority/id/cache 输入）+ 客户端 handle：提交请求并按 id 从 `audio_q` 过滤出本请求的帧 yield。
  - `runtime_supervisor.start_inference` 改为起 `engine.run`。
- **验收**：
  - 单测：优先级队列在有 pending 朗读时先出朗读任务（用 Fake 驱动，不烧 GPU）。
  - 🔊 朗读端到端仍出声；`/snapshot` 的 status/audio_frames 仍正确；一键试音仍工作（出声证据未破）。

### [x] Step 4 🔊 — 朗读路由改走 `engine.synthesize`
> 实现方式：`engine.run_loop` 保持 text_q/audio_q 协议不变，是 `inference_worker` 的行为保持替换；朗读生产端（PlaybackService）零改动。🔊 待用户 smoke。
- **目标**：route 不再手动操作 `text_q`。
- **改动**：`/read` 路由用引擎客户端提交请求；`audio_feeder_thread` 消费帧。删除 route 内的手动 `text_q.put({...})` 记账。
- **验收**：🔊 朗读、暂停/继续/seek、一键试音全部端到端正常。

### [x] Step 5 🔊 — 播客改走 `engine.synthesize`，删掉独立进程 + `gpu_lock`
> 播客 orchestration 子进程保留（暂停/电量/jobstore/nice-19），但不再加载模型：每个 chunk 经 `podcast_q` 提交给单一引擎进程，引擎写 `chunk_NNNNN.npy`（失败写 `.err`），子进程轮询文件。删 `gpu_lock` 与 `TTSEngine` 导入。🔊 待用户 smoke（尤其并发朗读+播客只一份模型）。
- **目标**：消除重复模型加载与 GPU 无协调。
- **改动**：
  - `PodcastService` 成为引擎客户端：按 chunk 以**低优先**提交请求，把帧排空进 WAV。
  - 删除 podcast worker 里的 `from core.tts_engine import TTSEngine` + `TTSEngine(...)` 实例化、删 `gpu_lock`、删 `with gpu_lock:`。
- **验收**：
  - 🔊 生成一集播客，WAV 正确、音色对（Serena/Ryan 不串）。
  - 🔊 并发「朗读 + 播客」时**内存只有一份模型**（看 VRAM/日志确认）；朗读不被饿死。

### [x] Step 6 — 清死代码 + 收尾
- **目标**：删掉浅模块与孤儿引用。
- **改动**：
  - `tts_engine.py`：归一化/kwargs 已搬走后，把残余「碰模型」逻辑并入 `MLXBackend`，删除 `tts_engine.py`（或仅留薄 re-export 一个周期）。
  - 删 `llm_engine.py:44` / `reader_service.py:242` 的孤儿 `gemini_engine` 注释。
  - 全局 grep 确认无残留 `TTSEngine` 直接 import（除经引擎外）。
- **验收**：`pytest core/tests/ -v` 全绿；`grep -rn 'TTSEngine\|gemini_engine' backend/core backend/URL-Reader` 仅剩预期引用。

---

## 5. 已否决 / 不走的路（勿重提）
- **多进程 + 修好 gpu_lock**（保留独立播客进程，只把锁加严）：单 GPU 多进程不真并行，仍 N 次模型加载、仍 thrash 发热。否决于 ADR-001 决定 #1/#2。
- **裸队列协议公开**（让播客也直接用 `text_q/audio_q` dict）：接口仍暴露 `task_id`/`CHUNK_DONE` 黑话，门没关严，易回退到双推理撞 GPU。否决于决定 #3。
- **整个引擎做 Real/Fake 两份全量实现**：测的是假货逻辑，抓不到真引擎里的 bug（如串音）。否决于决定 #6。

## 6. 进度日志（/loop 每步追加一行）
- 2026-06-25：CONTEXT.md 与迁移计划建立；待跑 Step 0。
- 2026-06-25：Step 0 ✅ 基线 34 passed。
- 2026-06-25：Step 1 ✅ 新增 `core/inference/model_backend.py`（`ModelBackend` 协议 + `MLXBackend` + `FakeBackend`，mlx 惰性 import）与 `core/tests/test_model_backend.py`（子进程验证 FakeBackend 不触发 mlx）。全套 39 passed。
- 2026-06-25：Step 2 ✅ 新增 `core/inference/engine.py`（`InferenceEngine`：kwargs 构建/ICL 注入/语种自动检测、`normalize_frame` 立体声+鲁棒增益+clip、读穿缓存、**新缓存 key=text+voice+model+lang 修串音**、`evict_cache`）与 `core/tests/test_inference_engine.py`（串音回归、缓存命中不调后端、归一化 clamp、autodetect、驱逐）。全套 48 passed。
- 2026-06-25：Step 3+4 ✅（代码+单测）`engine.run_loop` 取代 `inference_worker`（保持 text_q/audio_q 协议），引擎拥有进程循环/模型切换/空闲卸载/VRAM·状态上报；新增 podcast 低优先 lane（读优先）。backend.py 主进程不再 import mlx。新增 `test_engine_loop.py`（6 测：读 lane、task_id 失效、哨兵透传、读优先、播客 lane 写 chunk 文件、播客不污染 audio_q）。
- 2026-06-25：Step 5 ✅（代码+单测）PodcastService 经 `podcast_q` 提交、轮询 chunk 文件；删 `gpu_lock`/`TTSEngine` 子进程模型加载。
- 2026-06-25：Step 6 ✅ 删除 `core/tts_engine.py` 与死函数 `manage_cache_limit`；清孤儿 `gemini_engine` 注释。grep 仅余文档性引用。
- 2026-06-25：全套 **54 passed**。所有 6 步代码+单测完成；🔊 真实出声验收见 `INFERENCE_ENGINE_TEST_GUIDE.md`，待用户手动 smoke。
- 2026-06-26：🔊 真实后端 smoke（dev 模式 `TTS_LEGACY_LOOPBACK_CLIENTS=1` + `TTS_MODELS_PATH`→`mlx_audio/models`）：
  - Step 1 试音 ✅ `{"ok":true}` 出声；Step 2 串音 ✅ 同句不同音色 → 2 个独立缓存文件、不串；Step 3 缓存命中 ✅ 再读不新增 npy、`audio_frames>0`。
  - **Step 5/6 核心 ✅**：并发「2 个播客任务 + 朗读」时进程内存铁证 —— 推理进程**唯一**持模型(~2.9 GB)，两个播客编排子进程各仅 ~87 MB(**不加载模型**)，主进程 ~69 MB。引擎实时写出 `chunk_00000/00001.npy` + `progress.json`、无 `.err`。电池暂停策略(`battery_podcast_policy=pause` + 电池供电)按设计暂停，切 `quiet` 即恢复生成。
  - 未单独跑：Step 4(暂停/继续/seek)、Step 7(性能档切换/空闲卸载) —— 低风险，留待 App 内顺手验。
- 2026-06-26：**修复既有 pause bug（与本重构无关）**：UI 轮询的 `GET /snapshot` 一直漏发 `is_paused`/`is_playing`（只有 `GET /status` 有），而 Swift（`AppStateStore`/`BackendProcessManager`/`ConsoleViewController`/`PlaybackPopover`）全部用 `snapshot.is_paused ?? false` 判断暂停态 → 前端 `isPaused` 恒为 false → 播放/暂停切换在所有来源（朗读/saved/播客）失灵。修法：把 `is_paused`/`is_playing` 补进 `/snapshot`（与 `/status` 同算法，读共享 player 状态，故三来源统一生效）。实测 `/snapshot` 随 `/pause`·`/resume` 正确翻转 `(F,T)→(T,F)→(F,T)`。前端无需改。**待用户重建 App 验证三来源暂停。**
- 2026-06-26：播客端到端再验(临时把 `battery_podcast_policy` 由 `pause` 改为 `allow`，因用户在办公室用电池要测全流程)：短播客任务跑到 `done`，产出合法 WAV(`24000Hz/立体声/7.2s/int16/峰值32111`)+ `.txt` 文稿，`err=None`，全程单引擎单模型。**⚠️ `battery_podcast_policy` 现为 `allow`，待用户后续改回 `pause`（或在 App 设置里改）。**
- 2026-06-25（原计划暂停点已恢复）：Step 0–2 全为**纯新增**，未改动任何现有代码路径，App 行为不变。Step 3–5 是**活线切换**（改 `start_inference` 起 `engine.run`、朗读/播客改走 `synthesize`、删独立播客进程+`gpu_lock`），彼此耦合、且只能靠真实 MLX 出声验证 —— 需用户在场跑 App 验声。**发现**：原计划 Step 3/4 粒度过细无法各自独立上线（改 `start_inference` 而不同时改读路由会让 worker 收到旧格式任务而崩）；建议 Step 3–5 作为一次有用户监督的切换合并推进。
