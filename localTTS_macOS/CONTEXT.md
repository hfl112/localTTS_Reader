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
- **playback-status** —（ADR-003）播放真相的唯一表达：枚举 `idle | generating | playing | paused`，由 `PlaybackService.playback_status()` 从 player 现算（计算式，无存储标志）。见 §3c。
- **playback-presentation** —（ADR-003）前端纯映射模块：`status →（按钮动作, 图标, 文案）`一张表，替掉 Console/Popover/render 三份重复按钮逻辑。

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

## 3b. 架构决策 ADR-002：PlaybackService 收口为唯一播放拥有者（#1 + #3）

**状态**：已接受（2026-06-26，经 grilling 全树确认）
**背景**：#2(ADR-001)已把推理/缓存/模型生命周期搬出 `backend.py`。剩下的播放编排仍散：`audio_feeder_thread`(在 backend.py)、会话身份分散在 `PlaybackController._session_id` + `SharedState.current_task_id`(二者在 `_next_session` 同步自增、**永远相等**=冗余) + `runtime_state`；`/read`、`/seek`、`/play_saved` 各自重复「`start_new_session()` + `start_tts_thread(7参)`」两步。另外 **#2 之后 `audio_feeder` 的播客 buffer 分支已成死代码**（无人再 `set_podcast_file`）。

**决定**：
1. **形状**：深化现有 `PlaybackService` 为播放的**唯一深拥有者**；不新建并列模块。`backend.py` 路由只调它。
2. **会话身份**：崩成单一身份——去掉 `_session_id`，以 `SharedState.current_task_id`(mp.Value，跨进程必需)为唯一会话计数器。`PlaybackSession` 值对象创建时捕获该 id，拥有 `chunks/index/config/title`，`is_current()` = `id == current_task_id.value`。生产线程、推理引擎、audio_feeder 全部据此判活。
3. **接口**：把两步崩成单个 `play(chunks, config, *, start_idx=0, title=None) -> PlaybackSession`：内部建会话(bump current_task_id)、置 runtime main 播放中、启动生产线程。`/read`、`/seek`、`/play_saved` 只保留内容准备(LLM 模式/parse/存 state)后调 `play()`。**`seek` = `stop` + `play(start_idx=new_idx)` 的薄包装**。`pause/resume/stop` 改为会话感知。
4. **audio_feeder 归位 + 删死码**：`audio_feeder_thread` 搬进 `PlaybackService`(单一去向=player)；删 `audio_feeder` 的播客 buffer 分支 + `runtime_state` 的 `set_podcast_file/append_podcast_audio/consume_podcast_buffer/podcast_file`，并改 `test_services_smoke` 对应断言。

**删除测试结论**：会话身份合并后 `_session_id` 删之复杂度被**消除**（不是搬走）；`play()` 把三处重复的两步编排收成一处=集中。

新增术语：[[playback-session]] `PlaybackSession`（一次朗读的生命周期值对象）。

---

## 4b. 迁移计划 #2（ADR-002，/loop 按此逐步执行）

同 §4 总原则（tiny commits、每步跑 `pytest core/tests/ -v`）。🔊 标记=改动真实播放/出声路径，需用户 App 内 smoke（朗读/saved/seek/暂停）确认后才算过。

### [x] P0–P4 完成（2026-06-26，代码+单测+后端 live smoke）
- P1 ✅ `PlaybackSession` 值对象；`PlaybackController` 去 `_session_id`，单一身份=`current_task_id`；`is_current(id)`/`can_feed_audio(id)` 单参；`_shared_task_loop(session)` 去掉死的 is_podcast 分支。
- P2 ✅ 单入口 `play(chunks, config, start_idx, title)`；`/read`、`/seek`(=play at new_idx)、`/play_saved`(经 read_text) 全改调；删 `start_tts_thread`。
- P3 ✅ `audio_feeder_thread` 搬进 `PlaybackService.feed_audio_loop`(单一去向=player)；删 backend 死的播客 buffer 分支 + `runtime_state` 三个 buffer 方法(`set_podcast_file/append/consume`)；lifespan 改起 `playback_service.feed_audio_loop`。**保守取舍**：`podcast_file`/`podcast_buffer` 字段保留(恒 None/空)以维持 `/snapshot` 契约(Swift 有声明但未用),不做路由级连删。
- P4 ✅ 删 backend 未用 import(numpy/scipy)；grep 无 `_session_id`/`start_tts_thread`/`set_podcast_file` 残留；全套 **55 passed**。
- **Live smoke**(真实后端):/read→播放中(is_paused F,is_playing T)→/pause(T,F)→/resume(F,T)→/seek(无崩,继续播)→/stop(IDLE)，`audio_frames` 全程增长、启动无 NameError/AttributeError。🔊 真实出声 + App 内三来源(朗读/saved/播客)暂停按钮 + seek 待用户 App 验证。

### [x] P0 — 基线：`pytest core/tests/ -v` 全绿。
### [ ] P1 — `PlaybackSession` 值对象 + 崩会话身份
- 新增 `PlaybackSession`(id/chunks/index/config/title)；`PlaybackController` 去掉 `_session_id`，`is_current` 以 `current_task_id.value` 为准。
- 验收：单测——新建会话后 `is_current` 真、bump 后旧会话 `is_current` 假；`_session_id` 无残留引用。
### [ ] P2 🔊 — 单入口 `play()`，路由改调
- `PlaybackService.play(chunks, config, start_idx, title)` 收口；`/read`、`/seek`、`/play_saved` 改调；`seek`=`stop`+`play(start_idx)`。
- 验收：单测 play() 启动会话/置 main；🔊 朗读/saved/seek 端到端正常。
### [ ] P3 🔊 — audio_feeder 归位 + 删死播客 buffer 路径
- `audio_feeder` 搬进 `PlaybackService`；删 buffer 分支 + `runtime_state` 四个 podcast-buffer 方法；改 `test_services_smoke`。
- 验收：单测绿；🔊 朗读出声、暂停/恢复、卡拉OK 索引正常。
### [ ] P4 — 收尾：grep 确认无 `_session_id`/`set_podcast_file` 残留；`pytest` 全绿。

## 3c. 架构决策 ADR-003：playback-truth seam（播放真相收口为单一计算式状态）

**状态**：已接受（2026-06-28，经 grilling 全树确认）
**背景**：「是否在播 / 是否暂停」的真相被存在 4 处（`player.is_paused`、`runtime_state.main_is_playing`、`SharedState.status_code`、生产线程是否存活），被 **≥5 处独立派生**（`/status`:580、`/snapshot`:1093 各算一次**且已漂移**——后者多了 `player is not None` 空安全；前端 `AppStateStore` + `ConsoleVC` 两份缓存各派生一次），再跨 500ms 轮询传递。`main_is_playing` 是「play() set True / finally reset False」的**存储标志**，竞态/覆盖/漏发即 bug。2026-06-27~28 的 Bug2（`/snapshot` 漏发 `is_paused`）、Bug3（短播客 `is_playing` 被陈旧线程覆盖）、#1（点下一句后暂停键失灵）**全部出自此缝**。

**决定**（8 项）：

1. **计算式真相，删存储标志。** 不再有 `main_is_playing`。`playback_status` 按需从 player 现算——无标志可被 clobber，那类竞态**结构性消失**（非搬走）。
2. **枚举 = `idle | generating | playing | paused`。** `finished` 并入 `idle`。判据（优先级，全取主进程 player 状态）：`not is_running()→idle`；`is_paused→paused`；`is_prebuffering→generating`；`else→playing`。`COOLING`（buffer 满节流）归 `playing`；`status_code`/`audio_frames` 退化为**纯诊断**。
3. **拥有者 = `PlaybackService.playback_status()`。** `/snapshot`、`/status` 各调它一次，删 backend.py 两处独立派生。线上 = 字符串字段 `playback_status`。
4. **命令回传权威状态。** `/pause /resume /seek /stop` 在响应体回传新 `playback_status`；前端**乐观更新**、立即渲染。按钮读可靠 status 决定 pause/resume；`idle→朗读`留前端（需前端内容）。**不新增 toggle 端点**（决定 #1 已移根因，toggle 只为 ≤500ms 边缘窗口，收益边际）。
5. **对账 = 丢弃过期轮询。** 前端轮询器记每次 fetch **发起时刻**；命令成功后盖 `lastCommandAt`；发起早于 `lastCommandAt` 的轮询结果**丢弃其 status**（其它字段照用）。精确灭「闪一下」。
6. **单一前端缓存 = `AppStateStore`。** `ConsoleVC` 删本地 `lastSnapshot`，按钮/渲染读 store；对账逻辑只写 store 一处 → 两个按钮吃同一真相。
7. **抽纯模块 = `PlaybackPresentation` + 对账 reducer。** `status→(动作,图标,文案)`一张表替三份重复按钮逻辑；对账为纯函数。逻辑出 view controller → 可测（接口即测试面）。
8. **契约焊两侧。** 后端 pytest `test_snapshot_contract`：① `/snapshot`+`/status` 含 `playback_status` 且值合法；② 迁移期旧 `is_paused`/`main_is_playing` 与 status 自洽（**双验**：守并存 + 验新算法）；③ `/snapshot` 含前端消费的 key 集。前端**新建 XCTest target**（XcodeGen `project.yml` + `xcodegen generate`）：测 `PlaybackPresentation` 映射、对账 reducer、`Snapshot` 解码契约、`MockBackend` 一致性。

**评审修订（2026-06-28，subagent design review，approve-with-amendments）**：
- **(C-1 严重) `is_playing`/`is_paused` 作为派生 wire 别名永久保留**（从 `playback_status` 现算）。Chrome 扩展 `../qwen-tts-extension`（content/background/popup）在读这三个字段；C1 只删**内部存储标志** `main_is_playing` + backend 两处重复派生，**wire 零变化**，扩展不受影响。（同时定了原 m-2「is_paused 去留」=保留。）
- **(M-1 重大) A4 一致性断言只在稳态校验**：`play()` 先 `set_main(is_playing=True)`（playback_service.py:163-165）后台线程才 `player.start()`（:302），存在「`main_is_playing=True` 而 `playback_status=idle`」的**瞬态窗口**（正是本 ADR 要消灭的「存储标志领先真相」）。故契约测试用**同步驱动的 FakePlayer** 校验四个稳态的等价映射，**不对 live 异步路径**断言，否则 flaky。
- **(M-3 重大) `PlaybackPresentation` 的 `idle` 动作 = `.read`**：表拥有「图标/文案/调哪个命令」；`idle→朗读`的**内容来源**（文本框 vs 剪贴板）仍归 VC。否则 idle 分支会在 VC 里重新长出来，重蹈三份分歧。
- **(C-2) B4 拆成 B4a（store 接线，可单测+build）+ B4b 🔊（VC 路由+删本地缓存，手动 smoke）**，只在 B4b 挂 🔊。
- **(范围) `current_article_index`/卡拉OK 路径**（backend.py:1075-1080，取自 `player.currently_playing_index`，与 `main_is_playing` 无关）**不在本次范围,勿动**。

**删除测试结论**：`main_is_playing`（内部存储标志）删之复杂度**被消除**（非搬走）；三份按钮派生收成一张表 = 集中。

**新增术语**：[[playback-status]] · [[playback-presentation]]。

---

## 4c. 迁移计划 #3（ADR-003，/loop 按此逐步执行）

同 §4 总原则（tiny commits、每步跑 `pytest core/tests/ -v`）。🔊 = 改真实播放/出声路径，需用户 App 内 smoke 后才算过。**三步保证前后端版本错配也不炸。**

> 原子步执行规则（/loop）：一次只做一步；做完跑该步**验收命令**，绿了才把 `[ ]`→`[x]` 并进下一步；红了就修到绿、不跳步。标 🔊 的步只有**用户 App 内手动确认**后才能勾 `[x]`——/loop 在该步代码+构建完成后**暂停等用户**。每步尽量「先写测试看红→实现→看绿」。

### 阶段 A — 后端（pytest 关，零前端风险）

#### [x] A1 — `PlaybackService.playback_status()` 纯计算 + 单测 ✅（test_playback_status_predicate；62 passed）
- 改：新增 `playback_status() -> str`，判据 = ADR-003 决定 #2（`not is_running()→idle`；`is_paused→paused`；`is_prebuffering→generating`；`else→playing`）。
- 验收：新增 `test_playback_status`（假 player 覆盖 4 状态）；`cd backend && python -m pytest core/tests/ -k playback_status -v` 绿。

#### [x] A2 — `/snapshot`+`/status` 透出 `playback_status`（旧字段保留）✅（test_snapshot_and_status_expose_agreeing_playback_status；63 passed）
- 改：两端点调 `playback_status()` 加字段；旧 `is_playing`/`is_paused`/`main_is_playing` 暂保留（并存）。
- 验收（m-1 强化，不只验"存在"）：驱动 FakePlayer 到已知态（running 非 paused），断言 `/snapshot.playback_status == "playing"` **且** `== /status.playback_status`（两端点必须相等——这正是"拥有者=一个方法"要消灭的 :580 vs :1093 漂移）；全套绿。

#### [x] A3 — 播放命令回传新状态 ✅（test_playback_commands_return_new_status；64 passed）
- 改：`/pause /resume /seek /stop` 在动作后现算并把 `playback_status` 放进响应体。
- 验收：pytest 断言 `/pause`→`paused`、`/resume`→`playing|generating`、`/stop`→`idle`；全套绿。

#### [x] A4 — `test_snapshot_contract`（契约焊后端侧）✅（test_snapshot_contract；65 passed，连跑 3 次不 flaky）
- 改：新增测试三断言——①状态合法 ②**(M-1 改)稳态等价映射**:用同步驱动的 FakePlayer 把 player 置于四个稳态,断言 `playback_status ⇔ (main_is_playing, is_paused)` 自洽(`paused⇔is_paused`;`playing/generating⇔main_is_playing True`;`idle⇔False`)——**不对 live 异步 `play()` 路径断言**(那有 set_main 领先 start 的瞬态,会 flaky)③`/snapshot` 含前端消费 key 集。
- 验收：`python -m pytest core/tests/ -v` 全套绿(连跑 3 次不 flaky)。

### 阶段 B — 前端（xcodebuild；B4 标 🔊）

#### [x] B1 — 建 XCTest target（M-2 已具体化）✅（QwenTTSTests logic-only + scheme test 动作；xcodebuild test → ** TEST SUCCEEDED **）
- 改：`QwenTTS/project.yml` 加 target `QwenTTSTests`：`type: bundle.unit-test`、`platform: macOS`、`sources: [QwenTTSTests]`、`dependencies: [{target: QwenTTS}]`、`settings: { GENERATE_INFOPLIST_FILE: YES }`；**logic-only,不设 `TEST_HOST`**(本 app 是菜单栏 `LSUIElement`,host-app 测试跑起来别扭)。加顶层 `schemes:` 的 `QwenTTS` → `test: { targets: [QwenTTSTests] }`。被测类型 internal,测试用 `@testable import QwenTTS`。建 `QwenTTSTests/Placeholder.swift` 占位测试。`cd QwenTTS && xcodegen generate`。
- 验收：`DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild test -project QwenTTS/QwenTTS.xcodeproj -scheme QwenTTS -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO` 跑通、占位测试绿。

#### [x] B2 — `PlaybackStatus` 枚举 + `PlaybackPresentation` 纯映射 + 测 ✅（PlaybackPresentationTests；TEST SUCCEEDED）
- 改：`Snapshot.playback_status` Codable 枚举（带 unknown 兜底）；`PlaybackPresentation(status)->(action,icon,label)` 纯映射。**(M-3)** action 枚举含 `.read`(idle)/`.pause`(playing/generating)/`.resume`(paused);`idle→(.read,"play.fill","播放")`。表只管 图标/文案/调哪个命令,`.read` 的内容来源(文本框/剪贴板)仍归 VC。
- 验收：Swift 测 4 状态映射(尤其 idle→.read、generating→.pause)+ 一段 JSON 解码出枚举;`xcodebuild test` 绿。

#### [x] B3 — 对账 reducer 纯函数 + 测 ✅（PlaybackReconcilerTests；TEST SUCCEEDED）
- 改：`reconcile(current, polledStatus, polledIssuedAt, lastCommandAt) -> status`（决定 #5）。
- 验收：Swift 测——命令前发起的轮询被丢、之后被采纳；`xcodebuild test` 绿。

#### [x] B4a — store 接线（可单测,不碰 VC）(C-2 拆分)✅（AppStateStoreTests 乐观更新+过期轮询丢弃+unknown 兜底；test 9 绿 + app BUILD SUCCEEDED）
- 改：`AppStateStore` 持 `playbackStatus`、命令成功后乐观更新 + 用 B3 reducer 对账;暂不改 VC。
- 验收：store 级 XCTest——乐观更新后 `store.playbackStatus` 即时正确、过期轮询不覆盖;`xcodebuild test` 绿。

#### [x] B4b 🔊 — VC 路由 + 删本地缓存（C-2 拆分）✅（代码+BUILD+TEST SUCCEEDED；🔊 手动 smoke 攒到最终复查）— APIClient 命令回传 PlaybackStatus、triggerAction/seek 回写 store.applyCommandResult、Console handlePlayBtn+render+Popover 经 PlaybackPresentation 读 store、删 ConsoleVC.lastSnapshot
- 改：`ConsoleVC`/Popover 经 `PlaybackPresentation` 读 `store.playbackStatus`(`handlePlayBtn` 从读 `ConsoleVC.lastSnapshot` 改读 store);删 `ConsoleVC.lastSnapshot`(:23,:466,:754);命令调用应用回传 status。
- 验收：`xcodebuild build` 绿;🔊 用户 App 内 smoke——朗读/saved/播客三来源暂停键正确、点下一句即时不闪、两个按钮(Console/Popover)状态一致。

#### [x] B5 — MockBackend 透出 `playback_status` + 一致性测 ✅（MockBackendConsistencyTests，COOLING→playing；TEST + BUILD SUCCEEDED）
- 改：`MockBackend.snapshotDict()` 按 state 映射加 `playback_status`,**(m-3)固定映射**:`.idle→"idle"`、`.speaking→"playing"`、`.paused→"paused"`、`.cooling→"playing"`(COOLING 归 playing,与后端判据一致,**别误映成 generating**)。
- 验收：Swift 测 mock 四态输出解码成 `Snapshot` 且 `playback_status` 与上表一致;`xcodebuild test` 绿。

### 阶段 C — 收尾

#### [x] C1 — 后端内部收尾（C-1 改:不删 wire 别名）✅（部分,见下）— wire `is_playing/is_paused/main_is_playing` 在 /snapshot+/status 全改为**从 `playback_status` 导出**(消除最后那处 racy 派生 `main_is_playing && !is_paused`),两端点同源不矛盾;Chrome 扩展读的字段照常有值。65 passed×2。
> **诚实延后**:`RuntimeState.main_is_playing` 存储字段 + 所有 `set_main(is_playing=…)` 调用点**未物理删除**——现已 vestigial(无任何「真相读者」,wire 由 playback_status 导出覆盖),物理删除需改 ~8 处 set_main 调用、高触碰且零行为收益,留作独立 cleanup。ADR-003 决定 #1「删存储标志」的**行为目标已达成**(真相=计算式,无 racy 读者),仅物理移除待办。
- 改：仅移除**内部存储标志** `RuntimeState.main_is_playing` + backend 两处重复派生;`is_playing`/`is_paused`/`main_is_playing` **作为 wire 别名继续从 `playback_status` 现算导出**(Chrome 扩展在读,wire 必须零变化);A4 契约测试去掉「并存期」字样(别名恒等于 status 推导,常态成立)。
- 验收：`python -m pytest core/tests/ -v` 绿;`grep -rn 'main_is_playing' backend/core` 仅余「从 status 导出 wire 别名」一处、无其它内部 reader 残留;扩展三处(`../qwen-tts-extension` content/background/popup)读到的 `is_playing/is_paused` 仍有值(手验或保留集成断言)。

### [x] 最终复查 ✅（2026-06-28）
- **后端 65 passed · 前端 11 XCTest TEST SUCCEEDED · app BUILD SUCCEEDED**（已构建到 build/DerivedData）。
- ADR-003 八决定核实:#1 计算式真相✅(存储标志 vestigial,物理删除延后) ·#2 四状态 from player✅ ·#3 playback_status() 唯一所有者✅ ·#4 命令回传+乐观更新无 toggle✅ ·#5 对账丢弃过期轮询✅ ·#6 单一 AppStateStore 缓存✅ ·#7 PlaybackPresentation+reducer 抽出✅ ·#8 两侧契约测试✅。
- 🔊 待用户手动 smoke(见会话末「手动检查清单」)。

---

## 4d. 修复计划 #4（ADR-003 后续 + 内容中心行动作，用户 smoke 反馈，2026-06-29）

> 来源：用户 App 内 smoke ADR-003 后的反馈。三来源暂停 ✅、短播客暂停 ✅、新播客不卡 ✅；但发现以下 4 个问题。规则同 §4c（tiny commits、先测后改、🔊 手动验攒到最后）。
> **已过 subagent 设计评审（2026-06-29，原判 REWORK→已按 5 项修订）**：C1 F1 须迁移现有 prebuffer 测试+跑全套；C2 F4 **不在 load() 排序**(否则 savedIndex 错位播错条目)、排序只在前端展示层；C3 F3 **去掉 LLM key 检查**(单人 TTS 不需 key)；M1 F2 后端补 RESUME_MODE 空状态防 500；M2 F2 前端 resume 绕过 mode/LLM 检查、popover 出范围。下方各步已含修订。

### 根因（已查证）
- **A**（点下一句后暂停键变播放、且只能停止）：`play()` 在后台生产线程才 `player.start()`（playback_service.py:302），而 `/seek` 在 `play()` 返回后**立刻**算 `playback_status()` → 此刻 `is_running()=False` → 返回 `"idle"` → 前端 `applyCommandResult("idle")` 把按钮设成播放。即评审 M-1 的「set_main 领先 player.start」竞态，咬到了命令响应。
- **B**（停止后点播放不播）：idle 时播放键动作 `.read`→`triggerInstantRead()` 读**输入框**，空则 `return`（ConsoleViewController:643）；读 saved 内容时输入框为空 → 无反应。缺「恢复当前文章」路径。
- **C**（即时/稍后阅读行无「生成播客」）：`LibraryRowView` 从未接该动作；但 saved/instant 行**已带 `fullText`**（LibraryView:99），后端 `generate_single_podcast` 现成——无需新端点。
- **D**（即时/稍后阅读行无置顶）：`SavedItemsService` 无任何 pin 支持；置顶后端仅播客版（`/podcasts/toggle_pin`）。

#### [x] F1（P0）— seek/play 命令同步返回正确状态 ✅（play() 同步 start;test_play_starts_player_synchronously_and_marks_generating;65 passed×2）
- 改：把 `_shared_task_loop`（:301-302）的 **`min_chunks_to_start=…` 和 `player.start()` 两行都**移进 `play()`，在 `_start_thread` **之前**同步执行；线程两行都删（**别只删 start() 留 min_chunks** = m2，否则二次 start 会重复 drain/reset 造成 glitch）。使 `play()` 返回时 player 已 running+prebuffering → `playback_status()=="generating"`。
- **(C1 必做)迁移现有测试**：`test_seek_play_uses_larger_prebuffer`（test_services_smoke.py:669-730）现在驱动 `_shared_task_loop` 并断言 `RecPlayer.seen_at_start`——F1 后该方法不再 start()，测试会挂。改成驱动 `service.play(...)` 并断言 prebuffer 在 start 时已应用。
- 验收(m1：以 service 级为权威)：红→绿——`service.play(...)` 后**同步**断言 `playback_status()=="generating"`（假 player 的 `start()` 置 running+prebuffering+清队列）。端点测 `/seek` 为次要、且**必须带 `x-management-token`**（/seek 鉴权）。**跑全套 pytest**（确认 Bug2/Bug3/`test_play_marks_playing_synchronously` 仍绿，非只跑新测）。手动：朗读中点下一句→暂停键仍"暂停"且能停。

#### [x] F2（P1）— 停止后 Console 播放键＝当前文章**从头重读** ✅（RESTART_MODE 从0播+空状态 noop 防 500;test_restart_mode_replays_from_start_and_noops_when_empty;后端 66 passed + 前端 BUILD SUCCEEDED;popover 未动）
> 语义：**续读**(从暂停处继续)是**暂停键**的事，已由 `/resume`(player.resume)实现，不动。**停止后播放键 = 当前文章从头(start_idx=0)重读**，不是续读。
- 后端：`/read` 加 **`RESTART_MODE`** 分支——读 `state.get("current_article",{})` 的 chunks，`curr_idx=0` 且把 `current_index` 重置 0；**若 `not chunks` → 提前返回 `{"status":"noop"}`**(防 :490-491 `state["current_article"]["title"]` KeyError 500)。**(M1)顺带给现有 RESUME_MODE 分支也加同样的空状态 no-op 守卫**(虽前端暂不发它，但防御)。
- 前端：Console idle 且输入框为空时，`.read` 动作**直接** `client.readText(text:"RESTART_MODE", …)`，**绕过** `ensureLLMConfigured`/mode/URL（M2：与 LLM 无关）；输入框有文字仍走即时朗读。`/stop` 只清内存 runtime_state、保留 state.json 的 current_article（:530-543 已确认），故从头重读可行。
- **(M2)Popover 出范围**：`PlaybackPopoverController` 的 idle `.read`=`readClipboard()`(读剪贴板)，**本次不改**。
- 验收：后端 pytest「RESTART_MODE 有文章→从 0 播放；空状态→no-op 不崩」绿；手动——读 saved→停止→Console 点播放→**从头**重读；无文章时点播放不崩、无反应。

#### [x] F3（P1）— 即时/稍后阅读行加「生成播客」🎙️ ✅（waveform 按钮→viewModel.generatePodcast→generateSinglePodcast,无 LLM gate;BUILD SUCCEEDED）
- 改：`LibraryRowView` 给 `.instant/.saved` 行加 🎙️（SF Symbol `waveform`/`mic.fill`）→ `viewModel.generatePodcast(item)`→`client.generateSinglePodcast(text: item.fullText, source: item.source, voice: nil, title: item.title)`。
- **(C3 改正)不加 LLM key 检查**：`/generate_single_podcast`（backend.py:910-947）是**纯单人 TTS、不调 LLM**，`GenerateSinglePodcastRequest` 无 mode 字段——加 `ensureLLMConfigured` 会**无谓拦住没配 key 的用户**。直接调，不 gate。
- (M3 小)`title` 用 saved 项标题(可能是截断串),接受即可或取首行;在验收里写明免得日后误报。
- 验收：`xcodebuild build` 绿；手动——saved 行点🎙️→后台起单人播客任务→播客 tab 出现新条目。按导入前缀分模式留 [[saved-to-podcast-feature]]。

#### [x] F4（P2，最大）— 即时/稍后阅读行加置顶 ✅（SavedItemsService.toggle_pin 不动 load 顺序+/saved/toggle_pin+/saved_items 返回 is_pinned;前端 isPinned 映射+pin 按钮放开 .instant/.saved+展示层 pinned-first 保 savedIndex;test_saved_items_pin_toggle_keeps_order;后端 67 passed+前端 BUILD SUCCEEDED）
- **(C2 关键)绝不在 `SavedItemsService.load()` 里排序**：`/play_saved`(backend.py:1010-1022)按**整数 index 定位**(`selected_text(indices)`),前端 `savedIndex` 取自后端原始数组顺序(LibraryView:97)。一旦 load() 置顶排前,positions 错位 → **播错条目**。所以:load()/存储**保持原顺序**;置顶排序**只在前端 `LibraryViewModel` 展示层做**,且在**捕获 `savedIndex`(原始后端顺序)之后**再排。
- 后端：`SavedItemsService` 加 `pinned` 持久化字段(读用 `.get("is_pinned"/"pinned", False)` 防旧文件无字段);新端点 `POST /saved/toggle_pin`(按 md5;default-deny POST 中间件已自动鉴权,无需改 allow-list);`/saved_items` 的 dict 返回 `is_pinned`。**不改 load 顺序**。
- 前端：`LibraryItem.isPinned` 用于 saved;`fetchSavedItems` 映射 is_pinned;**展示层**把 pinned 排前(保留每项原 `savedIndex`);pin 按钮对 `.instant/.saved` 也显示;`viewModel.togglePin` 对 saved 走 `/saved/toggle_pin`(对 podcast 仍走旧端点)。
- 验收：pytest 测 SavedItemsService pin round-trip（toggle 持久、旧文件无字段默认 False）+ **验证 load() 顺序不变**；`xcodebuild build`；手动——置顶 saved→展示排最前且**播放/删除仍命中正确条目**→重启仍置顶。

### 顺序：F1→F3→F2→F4。

### [x] 最终复查 ✅（2026-06-29）— 后端 67 passed · 前端 xcodebuild test SUCCEEDED · app BUILD SUCCEEDED(→build/DerivedData)。F1/F2/F3/F4 四项均落地。🔊 待用户手动 smoke（见会话末清单）。

---

## 4e. 清理计划 #5（死代码清理 + 读路径裁剪 + 生命周期 bug，2026-06-29）

> 来源:用户"整体 review、清理老逻辑、别变屎山" + "把静音裁剪也加到实时/saved 阅读"。两个 subagent 盘点了前后端死代码(均带 grep 证据)。规则同前(tiny commits、先测后改、🔊 攒到最后)。**带 ⚠️ 的需用户先拍板。**

#### [x] E1 — 删前端废弃 VC(7 个文件,纯删)✅（MainTab+SavedItems+UrlReader+Podcast+Cache+Environment+SettingsViewController；grep 确认仅 MainTab 内部互引；EngineSettingsViewController 保留；TEST SUCCEEDED）
- 删:`MainTabViewController.swift`(已弃用,无人实例化;活路径=`MainSplitViewController`)及只被它引用的 `SavedItemsViewController`/`UrlReaderViewController`/`PodcastViewController`/`CacheViewController`/`EnvironmentViewController`/`SettingsViewController`(后两者已被 `SettingsView`/SwiftUI 取代)。**删前对每个文件 grep 确认无其它活引用**(尤其 StatusItem/Coordinator 的 openSettings 等)。
- 验收:`xcodegen generate` + `xcodebuild build` + `xcodebuild test` 全绿。

#### [x] E2 — 删后端 + Swift 的死 podcast buffer 字段 ✅（runtime_state 删 podcast_file/podcast_buffer+reset_podcast_generation 方法及4调用点+snapshot 2行;Swift Snapshot 删 2 字段;后端 67 passed + build 绿）
- 后端:`runtime_state.py` 删 `podcast_file`/`podcast_buffer`(恒 None/空,无读者)+ `reset_podcast_generation` 的 buffer 行 + snapshot 导出两行(:19-20,67-68,93-94)。
- Swift:`Snapshot` 删 `podcast_file`/`podcast_buffer_chunks`(解码了从不读)。扩展不读这俩,安全。
- 验收:pytest 全绿(含契约测试) + `xcodebuild build`。

#### [x] E3 — 读路径静音裁剪(reads + saved,用户要的)✅（trim_silence 提到 engine.py 共享,podcast_service 改 import 去重;run_loop 读 lane 每句攒完→trim→按帧送;test_engine_loop +2 测;69 passed。🔊 待用户听实时朗读/saved 不卡）
- 把 `_trim_chunk_silence` 提到共享位置(`engine.py`,推理层owns音频整形),`podcast_service` 改 import 它(去重,不留两份)。
- `engine.run_loop` 读 lane:每句"攒完该 chunk 帧→`_trim_chunk_silence`→再按帧送 `audio_q`"。reads 与 saved 都走此 lane,一并生效。
- 代价:每句首声等该句生成完(略增延迟),换不卡。
- 验收:`test_engine_loop` 加用例(FakeBackend 造带首尾静音的帧,断言读 lane 输出已裁剪);全套 pytest 绿;🔊 用户听实时朗读/saved 不再每句顿。

#### [~] E4 — 删 `main_is_playing` 存储标志 —— **DEFER(2026-06-29 用户拍板先延后)**
> 发现 E4 连带重写 6 个回归测试(Bug2/Bug3/F1 断言 `snapshot["main_is_playing"]`)+ 改 ~10 处 + 改 finally 防覆盖逻辑,风险比 E1/E2/E5 大一个量级;而该标志现 vestigial、无害(真相=计算式 `playback_status`,wire 别名已从它派生不依赖存储标志)。**留待单开一轮专门连测试一起重写。**
- 现状:wire 的 `is_playing/main_is_playing` 已从 `playback_status()` 导出,存储标志 vestigial。但仍有 2 个读者:性能监控(backend.py:295 诊断提示)、`podcast_service._frontend_active` 回退(:488)。
- 做法:删字段 + `set_main(is_playing=)` 的 5 处写 + 把 2 读者改成等价判断(perf monitor→`player.is_running()`;podcast 回退→现有 callback 已够)。
- 验收:pytest 全绿 + 契约测试仍绿。
- **风险中等(触面比 E1/E2 大)**,故单列待用户决定是否本轮做。

#### [x] E5 — 删 RESUME_MODE(用户拍板:老 app 不用了)✅（/read 只留 RESTART_MODE,空状态 noop 保留;67 passed）
- `/read` 删 RESUME_MODE 分支,留 RESTART_MODE;:439 的 LLM-skip guard 与 :472 分支条件相应简化为只判 RESTART_MODE。验收:`test_restart_mode_*` 仍绿 + 全套 pytest。

#### [x] E6 — 删 Library 行空的 `ellipsis`「更多」按钮 ✅（BUILD SUCCEEDED）
- `LibraryView.swift` 删那个 `Button(action: {})` 的 `...`。验收:`xcodebuild build`。

### [ ] E7 —(独立,建议另排)App 退出可靠回收后端的生命周期 bug
- 现象:杀/退 App 后,后端主进程 + multiprocessing 子进程(推理 worker)常成孤儿(本会话见 6/15 的远古孤儿)。watchdog 管道关闭未可靠触发后端连同进程组退出。
- 这是 CLAUDE.md「崩溃后不留孤儿」发布标准。**需单独排查 watchdog/进程组回收**,不混进本次删代码,避免一次动太多。

### 建议顺序:E2→E1→E3→(E4/E5/E6 视拍板)→ E7 另排。

### [x] 最终复查 + 测试覆盖审计 ✅（2026-06-29）
- 完成:E2(死 podcast buffer 字段)、E1(7 个废弃 VC)、E6(空 ⋯ 按钮)、E5(RESUME_MODE)、E3(读路径裁剪+trim_silence 去重)。E4 **defer**;E7 另排。
- **测试覆盖审计**(用户要求):全库 test 函数名 HEAD vs 现状对比——唯一"消失"的 `test_runtime_state_snapshot_and_podcast_buffer` 实为 ADR-002 的合法改名(丢的是已删死方法 set_podcast_file/append/consume 的断言),现版还多覆盖 current_podcast_file/md5。其它测试文件**零删除**。基线 54 → 现 **69**(净 +15)。
- 终验:后端 69 passed · 前端 xcodebuild test SUCCEEDED · app BUILD SUCCEEDED(→build/DerivedData)。🔊 待用户手动 smoke。

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
- 2026-06-29：**清理 #5(§4e)完成**(/loop,2 subagent 盘点死代码):删 7 个废弃 VC + 死 podcast buffer 字段(后端+Swift)+ RESUME_MODE + 空 ⋯ 按钮;读路径加静音裁剪(`trim_silence` 提到 engine.py 共享去重)。E4(删 main_is_playing)因连带重写 6 回归测试 defer;E7(App 退出回收后端孤儿)另排。测试覆盖审计:基线 54→69 净 +15,无重要丢失。后端 69 passed、前端 test 绿、app build 绿。
- 2026-06-29：**修复计划 #4（§4d）全部完成**(/loop,先 subagent 评审 REWORK→5 项修订)。F1 seek/play 命令同步返回(点下一句后暂停键不再变播放;play() 同步 start)、F2 停止后播放键从头重读(RESTART_MODE+空状态 noop 防 500)、F3 即时/稍后阅读行加🎙️生成播客(单人 TTS 无 LLM gate)、F4 saved 置顶(后端 pin 持久化+/saved/toggle_pin 不动 load 顺序;前端展示层 pinned-first 保 savedIndex)。终验:后端 67 passed、前端 xcodebuild test SUCCEEDED、app BUILD SUCCEEDED。🔊 待用户手动 smoke。
- 2026-06-28：ADR-003 迁移 **全部 10 步完成**(/loop 自动推进):A1–A4 后端(playback_status 计算式+两端点透出+命令回传+契约测试)、B1–B5 前端(XCTest target + PlaybackStatus/Presentation/Reconciler 纯模块 + AppStateStore 乐观更新对账 + VC 三来源收口 + MockBackend 一致)、C1(wire 别名从 playback_status 导出;存储标志物理删除延后)。终验:后端 65 passed、前端 11 XCTest、app BUILD SUCCEEDED。🔊 待用户手动 smoke。
- 2026-06-28：ADR-003 迁移**先 subagent 设计评审**（approve-with-amendments）→ 按 C-1（保留 `is_playing/is_paused` wire 别名,不碰 Chrome 扩展）/M-1（A4 稳态断言防 flaky）/M-2（B1 测试 target 具体化）/M-3（idle→.read）/C-2（拆 B4a/B4b）/m-1·m-3·范围 修订 §3c+§4c。A1 ✅ 已完成。
- 2026-06-28：**ADR-003 playback-truth seam** 经 grilling 全树确认并记录（§3c）+ 迁移计划 #3（§4c，三步）。背景：本晚 Bug2/Bug3/#1 暴露「播放真相」散在 4 处、派生 ≥5 遍、跨轮询传递。决定：计算式单一 `playback_status` 枚举（删 `main_is_playing` 存储标志）、命令回传状态 + 乐观更新、丢弃过期轮询对账、单一前端缓存、抽 `PlaybackPresentation`/对账 reducer、前后端契约测试双焊（新建 Swift XCTest target）。待按 §4c 三步实施。
- 2026-06-25（原计划暂停点已恢复）：Step 0–2 全为**纯新增**，未改动任何现有代码路径，App 行为不变。Step 3–5 是**活线切换**（改 `start_inference` 起 `engine.run`、朗读/播客改走 `synthesize`、删独立播客进程+`gpu_lock`），彼此耦合、且只能靠真实 MLX 出声验证 —— 需用户在场跑 App 验声。**发现**：原计划 Step 3/4 粒度过细无法各自独立上线（改 `start_inference` 而不同时改读路由会让 worker 收到旧格式任务而崩）；建议 Step 3–5 作为一次有用户监督的切换合并推进。
