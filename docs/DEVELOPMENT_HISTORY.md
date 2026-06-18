# Qwen3-TTS 项目开发总志 (Total History)

本项目在 Mac (Apple Silicon) 环境下成功部署并进化了基于 **MLX-Audio** 框架的 **Qwen3-TTS 1.7B 8-bit 量化版** 系统。

---

## 📅 第一阶段：环境构建与脚本化 (2026-05-07)
*   **成果**：完成了 MLX-Audio 框架的安装，解决了 `Transformers 5.x` 的源码装饰器兼容性问题。
*   **突破**：实现了系统级的“选中即朗读”功能（macOS 快捷指令 + `qwen_reader.py`）。
*   **性能**：实测生成速度达到 **3.3x 实时速率**，内存占用约 2.5GB-4.9GB。

---

## 📅 第二阶段：浏览器插件化 (2026-05-08)
*   **目标**：实现网页正文的一键流式朗读。
*   **技术演进**：
    1.  **WAV 模式**：初步实现，但存在句末吞词问题。
    2.  **MP3 模式**：解决了吞词，但引入了断句感和电流声。
    3.  **PCM 裸流模式**：实现了 0 延迟、0 噪音的丝滑听感。
*   **核心逻辑**：引入了 **`smartSplit`（智能语义分段）**，解决了大模型无法处理超长文本的通病。

---

## 📅 第三阶段：独立桌面 App 服务化 (2026-05-09)
*   **目标**：高性能、高稳定性、局域网共享、断点续传。
*   **架构跃迁 (Client-Server)**：
    *   **UI 进程 (`app.py`)**：轻量级菜单栏控制台 (~40MB RAM)。
    *   **后端进程 (`core/backend.py`)**：常驻内存，模型加载一次，永久秒开。
*   **稳定性封锁**：
    *   **GIL 锁修复**：暴力禁用 `tqdm` 监控，解决了 macOS 菜单栏 Python 闪退问题。
    *   **MLX 流修复**：采用“任务队列”模式，解决了多线程环境下的 GPU Stream 报错。
*   **交互升级**：支持语速调节 (`0.8x-1.5x`)、自动记录播放进度 (`state.json`)。

---

## 🏆 最终黄金配置参数
*   **音色锁定**：物理 ID 绑定 (`Serena: 2084`, `Ryan: 2086`)。
*   **采样控制**：`Seed 42` / `Temperature 0.2` / `Top-P 0.5` / `Top-K 10`。
*   **指令优化**：`"Professional female anchor, steady and clear."`

---

## 📅 第四阶段：稳定性与音质深度调优 (2026-05-10)
*   **目标**：彻底解决长时间运行后的卡顿、发热、爆音及电流声。
*   **架构演进（从过度工程回归稳健平衡）**：
    1.  **原生音频驱动**：废弃了不稳定的外部进程 `ffplay`，改用 **Python 原生 `sounddevice` (PortAudio)**。
    2.  **全链路“防窒息”设计**：
        *   将重型计算（Mono->Stereo、自动增益、归一化）全部移至 **MLX 推理线程**。利用 MLX 在 C++/Metal 层的运算优势，彻底释放 Python GIL 锁。
        *   播放器回调函数退化为纯粹的“搬运工”，仅执行 `copyto` 操作，确保毫秒级的硬件响应。
    3.  **智能预缓冲 (2-Chunk Preroll)**：
        *   引入了“起播水位”机制：只有当队列中攒够 2 个完整音频块后才正式开嗓。
        *   提供了 1.5s-3s 的物理缓冲，完美抵消了 AI 推理时的瞬时波动。
*   **音质黑科技**：
    *   **物理哨兵同步 (Sentinel Synchronization)**：引入唯一的 `SENTINEL` 对象。引擎在文末投下哨兵，只有当声卡硬件确认“吃掉”哨兵后，引擎才允许清理资源。彻底解决了“读到一半被掐断”的顽疾。
    *   **智能动态增益 (Auto-Gain)**：在推理层实现 RMS 归一化，将语音峰值锁定在 0.7 左右，解决中英混读音量不一的问题。
*   **逻辑加固**：
    *   **温控限速 (Thermal Backpressure)**：当待播放缓存超过 30 秒时，引擎自动休眠，等待消耗。GPU 负载从 100% 降至平均 25%。
    *   **Obsidian 专项脱敏**：`TextProcessor` 增加了对 YAML、图片语法及 URL 的深度清洗，并保护了英文单词间的空格。
*   **性能监控**：
    *   内置了 5s 一次的性能诊断线程，实时监控 CPU Load、VRAM 占用及缓冲区水位。

---

## 📅 第五阶段：工业级效能与极致降温 (2026-05-10)
*   **目标**：解决 Unified Memory 架构下的总线竞争，实现长效稳定的低功耗运行。
*   **核心架构突破：进程级物理隔离 (Multiprocessing)**：
    *   **架构分家**：将主进程（API & 播放）与推理子进程彻底分离。
    *   **物理屏障**：确保 MLX 的显存回收抖动被锁死在子进程，主进程音频回调环境绝对纯净。
*   **巡航模式与静默优化 (Cruise & Stealth)**：
    *   **温控巡航**：将缓冲区维持在 8s-15s 的极窄区间，配合 10ms Token 级节流，彻底平抑风扇转速。
    *   **智能静默监控**：性能诊断线程在 IDLE 状态下完全静默，仅在朗读任务期间实时上报数据。
*   **极限资源管理**：
    *   **16000Hz 物理减负**：降采样输出减少 33% 算力与内存带宽需求。
    *   **VRAM 自动卸载**：10 分钟空闲自动释放 2.4GB 模型显存，按需瞬间重载。
    *   **会话级 LRU 缓存**：基于 MD5 哈希保留最近 10 个切片的音频，二次阅读实现 **0 GPU 负载**。
    *   **跨进程状态机 (SharedState)**：引入 `mp.Value` 同步引擎状态，解决了子进程推理与主进程监控之间的“信息孤岛”。
    *   **孤儿进程清理**：主程序启动时自动扫描并清理旧后端，杜绝端口占用与内存泄露。
*   **模型多样化支持**：
    *   **动态模型切换**：UI 新增“模型尺寸”选择，支持在 1.7B（高质量）与 0.6B（极冷速）之间一键切换，子进程自动重载。

---

## 📅 第六阶段：前端列表打通与极速离线合成 (2026-06-07)
*   **目标**：打通 Extension Popup 收藏卡片中对单条播客的直接生成与播放，移除了浮动 UI 的设置项配置，解决由于无缓存引发的 "cache file not found" 报错及高强度推理造成的 FastAPI 事件循环卡死，以及 macOS 菜单栏子菜单 clear 崩溃。
*   **逻辑与架构优化**：
    1.  **就地合成退避（Fallback）**：修改 `/cache/export` 接口。对于尚未被朗读过的收藏条目（无 `.npy` 文件），后端检测到文件缺失时，会自动检索 `saved_for_later.json`，在独立线程中同步完成离线合成并保存缓存，完美杜绝 `"Cache file not found"` 报错。
    2.  **异步执行器（Async Executor）**：将重型模型推理与分段合成完全外包给独立子线程运行（`asyncio.run_in_executor`），彻底隔离了 C++/Metal 推理对 FastAPI 主事件循环的强占，解决了收藏与保存文本时的网络卡死，交互体验极其丝滑。
    3.  **App 启动与退出物理清空**：在 `app.py` 中实现了物理级的 `clean_assets`。每次关闭或启动 App 时，均会自动清空 `data/cache/*.npy` 临时音频分段、SQLite `cache_metadata` 表、以及 `data/exported/*.wav` 中导出的单条音频，防止旧缓存跨会话残留。
    4.  **Mac 菜单栏“最近播客”一键查听**：
        *   在右上角菜单栏中引入了 `🎙️ 最近播客` 子菜单，自动以 1Hz 频次扫描 `data/podcasts/` 和 `data/exported/` 音频，并以时间命名展示最近 10 条（如 `📻 播客 06-07 14:05`）。
        *   利用特征 Hash 做变更检测，过滤了多余的主线程渲染，防菜单闪烁。
        *   兼容前缀识别，点击任意生成的播客均能立刻发起播放。
    5.  **Popup 卡片与网页悬浮卡片去重**：移除了网页浮动卡片和 Popup 界面内的所有“配置选择”控件，将语速、音色、模型切换等设置权统一归口到 Mac 菜单栏，实现极简纯净的交互。

---

## 📅 第七阶段：URL-Reader 网页净化、反爬穿透与性能稳定性加固 (2026-06-13)
*   **目标**：解决 URL 正文净化、WAF 绕过、YouTube 独立音色朗读、监控线程日志干扰以及僵尸进程死锁残留问题。
*   **突破与优化**：
    1.  **智能网页净化与 URL Reader**：建立 `URL-Reader/` 工具模块，利用原生 `urllib` 伪造 Chrome Headers 绕过 TLS 特征流控拦截，自动进行 Clash/V2Ray 本地代理退避。
    2.  **Chrome 浏览器 OuterHTML 提取**：在遭遇重型 WAF、动态 SPA 渲染或论文登录墙时，可通过 AppleScript 零延迟抓取 Chrome 浏览器当前活动标签页的渲染后 HTML，实现终极网络穿透。
    3.  **YouTube 视频字幕专属 Ryan 男声朗读**：智能检测 YouTube 视频 ID，利用 `youtube-transcript-api` 免登录抓取中英文字幕。为区分普通网页，YouTube 视频朗读默认使用 Ryan (男声) 音色进行内容声线区分。
    4.  **物理级端口占用强杀与 SIGINT 信号处理器**：在 `app.py` 启动时引入 `pkill` 与 `lsof -t -i:8001 | xargs kill -9` 双重物理强杀；在 `backend.py` 注册顶层退出信号处理器，调用 `os._exit(0)` 直接终止进程，彻底解决退出死锁。
    5.  **诊断日志干扰关闭**：注释并停用周期性控制台输出 `--- [DIAGNOSE] ---` 的性能诊断监控线程，彻底解决了高频打 log 占用终端的干扰。
    6.  **路径环境变量解耦**：移除 `../../mlx_audio` 的相对路径绑定，支持通过 `MLX_AUDIO_PATH` 与 `TTS_WORKSPACE_PATH` 环境变量驱动，实现部署解耦。
    7.  **Gemini 智能翻译与临时文件缓存**：引入 `gemini_engine.py` 模块（解耦自 `obsidian_llm_wiki` 的多 Key/级别降级调用架构），命令行支持 `-t` / `--translate` 选项。可自动将抓取的原文保存到 `temp_source.md`，经 Gemini 自动翻译成流畅中文并写入 `temp_translated.md` 后，再投喂朗读。
    8.  **文本净化规则升级 (Paper 与 URL 过滤)**：
        *   **参考文献全段切除**：在 `processor.py` 中新增 `filter_references` 方法，自动识别并切除论文最后的 References/参考文献段落，避免朗读无价值的文献列表并极大地节省 GPU 推理开销。
        *   **超链接净化加固**：除了剔除标准的 `[text](url)`，还增加了对翻译后错乱变异格式的清洗（如中英文括号 `(url)` / `（url）` 及带空格的链接结构），确保最终合成发音时 URL 网址被完全过滤，体验极其流畅。
        *   **字幕与非言语噪声过滤**：自动过滤字幕中的 `>>` 说话人标识，并剔除单独存在的 `[掌声]`, `[欢呼]`, `[Laughter]`, `【注】` 等 12 字以内非言语声音标记，防止生硬发音，净化听觉流。
    9.  **Chrome 插件一键朗读与翻译当前页**：
        *   **异步非阻塞路由**：后端 `backend.py` 引入 `/read_url` 接口，采用异步子进程方式拉起 `read_url_cli.py` 任务，实现秒级响应反馈。
        *   **主面板 UI 升级与丁香紫渐变**：最顶部新增 URL 输入框及“原文/翻译”下拉菜单，自动捕获活动 URL。启动朗读按钮由突兀的蓝色重构为了高雅的 **丁香紫 (Lilac Purple)** 渐变圆角微立体按钮，视觉上极具品质。
        *   **UI 概念重命名与折叠隐藏**：将最近收藏改名为“稍后朗读 (Saved Text)”，容量放宽至 **`5` 条**；本地音频缓存改名为“快速倒带缓存 (Temp Cache)”并改用 `<details>` 标签包裹以在主面板**默认折叠隐藏**，界面极致清爽。
        *   **依次播放逻辑**：单项收藏的播放事件升级为“依次朗读模式”，点击任意条目即可自动从当前条目开始顺序朗读直至队列末尾。
        *   **物理路径归一**：后端成品播客 (WAV) 导出路径统一重构指向项目最外层的 `podcasts/` 目录，实现了“临时缓存”与“个人音频资产”的物理层彻底分离。

---

## 📅 第八阶段：播客并发管线与散热降级架构 (2026-06-14)
*   **目标**：解决多播客并发导致 Apple Silicon 过热/OOM 的核心难题，重构播客与阅读任务的优先级调度，分离播客提取与稍后朗读工作流，并强化 Mac 顶栏的快速干预能力。
*   **并发阻断与温控护航 (Thermal Backpressure & Concurrency Limits)**：
    1.  **进程级 GPU 独占锁 (Global Mutex Lock)**：利用 `mp.Lock()` 强制对所有 `mp.Process` 播客生成线程进行排队。提交再多任务，后台只会有一只模型在占用 Metal 进行运算，杜绝 OOM 和多重总线抢占。
    2.  **强制散热间隙 (Duty-cycle Cooldown)**：在离线播客推理的每个 Chunk 之间，强行注入了 `1.5` 秒的物理休眠。以极小的总体时间代价，将 GPU 利用率强压至 50% 的脉冲工作模式，使得机身温度呈指数级下降。
*   **任务优先级降级与闲置唤醒 (Task Downgrading Scheduler)**：
    *   **两分钟全局闲置调度器**：在主进程注入 `podcast_manager_loop` 监控。任何来自前端的第一优先级交互（如点击抓取 URL，或者正在进行流式朗读），会立刻通过 `mp.Event()` 强行向后台所有播客线程下达 `PAUSE` 挂起指令，让出 100% 算力。
    *   只有在一切交互静默长达 **120 秒** 后，调度器才会释放 `PAUSE` 信号，让挂起的播客任务无感在后台恢复生成。
*   **工作流分离与数据流向梳理**：
    *   **解耦提取与收藏**：将原本揉杂的“保存并生成播客”拆分为独立的【一键提取网页生成播客】工作流。现在点紫色的播客按钮，将不再污染“稍后朗读 (Saved Text)”列表。
    *   **人类可读命名法**：底层文件命名全盘重构为 `podcast_单篇_{source}_{safe_title}_{hash}_{timestamp}.wav`。前端放弃死板的 UNIX 时间戳，转而提取并显示文章前 20 字作为标题。
    *   **动态来源徽章**：从底层贯穿了 `source` 参数。如果是 YouTube 视频解析，生成的播客卡片将自带惹眼的红色 `[视频]` 标签；普通网页则是紫色的 `[网页]`。
*   **交互补足与容灾修复**：
    *   **一键设备重启**：解决 `sounddevice` 在连接 AirPods 后不切通道的盲区。在 Mac 顶栏菜单直接加入【🎧 重启音频设备】。点击后后端 `/restart_audio` 接口会瞬间强杀底层 Stream 并重绑系统默认音源。
    *   **一键紧急制动**：前端的 `⏹️ 停止` 按钮升级为大杀器。按下后不仅停止朗读，还会立刻向 `ACTIVE_PODCAST_PROCS` 发送 `terminate()` 信号，并物理清扫 `.pending_` 占位文件，瞬间中止所有正在占用 GPU 的后台任务。
    *   **断点防丢**：前端默认“翻译”选项。补回了上一个版本在架构大重构时被截断的 5 个 Temp Cache 专属接口，缓存重见天日。

---

## 📅 第九阶段：双人/单人播客体验升级与语种崩溃防护 (2026-06-14)
*   **目标**：优化双人模式命名及排序，加入稍后朗读时长估算，打通剪切板联动备份，解决 SQLite 缓存元数据缺失、删除索引错位，以及最核心的“中英混杂内容大模型自回归死循环（sisisi电流声）崩溃”难题。
*   **重构与修复亮点**：
    1.  **功能命名统一与排序**：将“双人对谈翻译”与“双人对谈解释/总结”统称为“双人-翻译”与“双人-总结”，并在插件弹窗中将“双人-总结”提至上方。
    2.  **极简阅读估时 (~13min)**：
        *   在“稍后朗读 (Saved Text)”的元数据里增加了时长估算，算法设计为：中文 ~250字/分钟，英文 ~150词/分钟。
        *   精简格式展示：小于一分钟展示为 `~45s`，大于一分钟仅展示分钟 `~13min`，大于一小时仅展示小时 `~2hr`。方便用户一眼甄别长文以放入后台进行播客异步生成，短文直接收听。
    3.  **剪贴板阅读双联动**：在 Chrome 插件与 macOS App 顶栏触发“朗读剪切板”时，后端 `/read` 接口会识别来源并自动在后台调用 `/save_for_later` 存入稍后朗读，免去二次备份烦恼。
    4.  **SQLite 缓存注册与物理同步**：
        *   修复了“快速倒带缓存 (Temp Cache)”展开后为空的遗留 Bug。在推理子进程生成 `.npy` 文件时，同步调用 `storage.add_cache_metadata` 将 MD5、语速音色、段落文本及物理时长（采样数/24000）等关键信息写入 SQLite 数据库。
        *   实现了 `manage_cache_limit`（超出 10 个缓存时）和 `clear_all_cache`（清空时）对 SQLite 记录与磁盘 `.npy` 文件的物理双向清除。
    5.  **MD5 唯一哈希删除（防错位）**：彻底修复了删除收藏项失效的 Bug。原先删除仅依赖数组下标，若有异步抓取中的待决任务插入到前端头部会发生错位。重构为了通过唯一的 `md5` 字段精确删除，彻底免疫错位风险。
    6.  **中英混杂自回归死循环崩溃防御 (ICL 语种重定向)**：
        *   **痛点**：当用英文 ICL 参考音频去朗读带有数字、大写英文的中文句时，Qwen3-TTS 模型会因为跨语言注意力机制发生混乱，产生长达 130s 的 `sisisi` 静态电流死循环噪音，并彻底污染后面的所有中文发音。
        *   **锁音升级**：离线生成并配置了 Serena 专属的中文锁音音频 `ref_serena_zh.wav`（文字：“欢迎收听本期播客，我是女主持塞蕾娜。”）。
        *   **同语种重定向逻辑**：在推理引擎底层拦截。如果当前朗读文本有中文，则自动重定向使用 Serena 中文锁音（或 Ryan 中文锁音）；如果当前文本是纯英文，则使用 English ICL 基准（`bbc_news.wav`）。如果语种无法匹配且没有备用音源，自动剥离 ICL 并安全退避至内置零样本模式（通过 Speaker ID 锁音），完美解决中英混杂无限死循环问题。
    7.  **降温限流与句间冷却升级**：针对后台长文本生成播客时，GPU 满负荷运转导致风扇狂转（5000+ RPM）与 CPU 升温的问题，在 `backend.py` 播客生成循环中，每次 yield 音频片时强制注入了 `0.2` 秒的限速休眠以降低 GPU 占空比，并将句子间的冷却冷却时间由 `1.5` 秒大幅延长至 `3.0` 秒，实现极致降温与静音。

---

## 📅 第十阶段：macOS 音频设备切换容灾加固 (2026-06-15)
*   **目标**：彻底解决播放 Podcast 时，因 macOS 切换输出设备（蓝牙耳机、AirPods、HDMI 等）导致 `sounddevice.OutputStream` 开流失败、音频静默的顽疾。
*   **根本原因分析**：
    *   macOS 在切换默认音频输出设备时，CoreAudio 内部的 **Audio Unit (AU) 图**需要经历"拆解 → 重建 → 稳定"三个阶段，这个过程往往伴随着多次 `AudioObjectPropertyListener` 回调抖动（如日志中 `89→175→89→89→195` 的连续切换事件）。
    *   旧代码收到切换事件后仅等待 `0.5s` 就立刻调用 `sd.OutputStream.start()`。此时 AU 图尚未稳定，导致 PortAudio 内部连续报错：
        ```
        Audio Unit: Invalid Property Value (-10851 / kAudioUnitErr_InvalidPropertyValue)
        → Internal PortAudio error (PaErrorCode -9986 / paInternalError)
        ```
    *   `_ensure_stream_started` 和 `_recreate_stream` 均无重试机制，一次失败即放弃，造成播放彻底静默。
*   **修复方案（三处改动，见 `core/player.py`）**：

    | 位置 | 改动前 | 改动后 | 效果 |
    |---|---|---|---|
    | `_device_monitor_loop` | 防抖等待 `0.5s` | 防抖等待 **`1.5s`** | 过滤蓝牙连接时的快速事件抖动，等待 AU 图完成重建 |
    | `_ensure_stream_started` | 单次尝试，失败即放弃 | **指数退避重试**（间隔 0→0.5s→1s→2s，共 4 次） | 失败后自动 `sd._terminate()/_initialize()` 重置 PortAudio 再试 |
    | `_recreate_stream` | 单次尝试，失败即放弃 | **指数退避重试**（同上，共 4 次） | 设备切换后开流失败可自动恢复，无需手动点击"重启音频设备" |

*   **效果**：即便在 AirPods 等蓝牙设备连接时产生多次抖动事件，系统也能在最多 `3.5s` 内自动完成设备切换并恢复无缝播放，彻底告别静默故障。

---

## 📅 第十一阶段：播客播放会话隔离与串台修复 (2026-06-17)
*   **目标**：修复播放已生成播客时偶发混入上一段网页朗读音频的问题，例如正在听政治哲学 podcast，却听到之前“李飞飞老师”文章的残留朗读。
*   **根本原因分析**：
    *   `/podcasts/play` 只调用了 `player.stop()` 清空播放器队列，但没有递增 `S.current_task_id`，也没有给 WAV 播放线程自己的会话身份。
    *   旧 TTS 推理线程、`audio_feeder_thread` 或旧 WAV 播放线程可能在新的播客开始后继续向同一个 `PCMPlayer.audio_queue` 投喂音频。
    *   `stop_event` 是全局事件，新播放入口会很快 `clear()`；旧线程如果只检查 `stop_event`，就可能在事件被清除后“复活”，造成两个来源交叉播放。
*   **修复方案（见 `core/backend.py`）**：
    1.  **播放会话换代**：新增 `PLAYBACK_SESSION_ID` 和 `playback_session_lock`。每次 `/read`、`/seek`、`/podcasts/play`、`/stop` 都会创建或作废播放会话，并同步递增 `S.current_task_id`。
    2.  **旧线程硬失效**：`shared_task_loop` 与 `play_wav_thread` 均捕获自己的 `session_id/task_id`。只要发现不再是当前会话，就立即退出，不能继续 `player.play_chunk()`，也不能发送结束哨兵。
    3.  **播客入口完整清场**：`/podcasts/play` 开始前先 `stop_event.set()`，调用 `player.stop()` 清空队列与 leftover，再创建新会话并清空主进程侧 `audio_q` 旧消息，避免旧 TTS 与旧播客残留进入新队列。
    4.  **状态写保护**：过期线程不再允许把新任务的 `MAIN_IS_PLAYING` 改成 `False`，避免菜单栏状态被旧线程覆盖。
*   **维护约束**：以后新增任何播放入口，都必须通过 `PlaybackController` 换代播放会话与 `S.current_task_id`，并在后台循环中检查捕获的会话仍然有效；不能只依赖 `stop_event`。

---

## 📅 第十二阶段：播放控制层架构收口与诊断入口 (2026-06-17)
*   **目标**：在不重写 TTS、URL 抓取、菜单栏与插件的前提下，将最容易出错的播放生命周期从散落的全局 helper 收口为单一控制层。
*   **核心改动**：
    1.  **PlaybackController**：新增统一播放控制器，集中负责会话换代、`S.current_task_id` 递增、`stop_event` 切换、`PCMPlayer.stop()`、主进程 `audio_q` 清理，以及线程有效性判断。
    2.  **播放入口统一化**：`/read`、`/seek`、`/podcasts/play`、`/stop` 不再直接拼装 session/task/queue 清理逻辑，统一调用 `start_new_session()` 或 `stop_current_session()`。
    3.  **后台线程条件统一**：TTS 朗读线程和 WAV 播客线程统一使用 `can_feed_audio(session_id, task_id)` 判断是否还能投喂音频，减少后续新增入口时漏掉某个条件的风险。
    4.  **诊断入口**：新增只读 `/debug/state`，暴露当前 playback session、task id、stop_event、主/播放器队列长度、当前标题、播客文件、活跃 URL 任务和后台播客进程数，方便排查静音、串台和任务残留。
*   **维护约束更新**：以后新增任何播放能力，都必须通过 `PlaybackController` 创建/作废播放会话，不能重新在 endpoint 内手写 `stop_event + current_task_id + queue` 的组合逻辑。

---

## 📅 第十三阶段：长文/长播客性能模式与静音散热优化 (2026-06-18)
*   **目标**：降低长文朗读和长 podcast 离线生成时的持续 GPU/CPU 占用，减少风扇噪音和机身发热，同时保留可手动切换的速度策略。
*   **核心改动**：
    1.  **性能模式**：新增 `fast`、`balanced`、`quiet` 三档 profile。实时朗读默认 `balanced`，播客生成默认 `quiet`，各档统一控制推理片段 sleep、句间冷却和播放器 buffer 高低水位。
    2.  **长播客小模型策略**：单篇 podcast 超过约 20 分钟时自动切到 `Qwen3-TTS-0.6B`；合集 podcast 默认使用 `quiet + 0.6B`，优先换取低热量和低噪音。
    3.  **后台生成暂停更严格**：播放、URL 解析活跃、播放停止后的 120 秒冷却窗口、以及电池供电状态都会暂停后台 podcast 生成，避免与前台收听抢 Metal/GPU。
    4.  **实时朗读 buffer 高低水位**：实时朗读不再固定按一个队列阈值巡航，而是根据 profile 在 buffer 超过 high 时暂停推理，降到 low 后恢复，减少长文持续满载。
    5.  **分段按 profile 调整**：`TextProcessor.smart_split` 支持按性能模式切块；`quiet` 更短，便于冷却、断点和停止响应，`fast` 更长，减少调度开销。
    6.  **响度单点化**：保留 `tts_engine.py` 的稳健归一化，降低最大增益；`player.py` 不再默认 1.8 倍二次放大，只做用户音量和安全限幅，减少削波和长时间听感疲劳。
    7.  **播客分段断点文件**：离线 podcast 每个 chunk 生成后立即保存到 `data/podcast_chunks/`，失败或中断后同内容可复用已完成 chunk，最后再拼接成 WAV。
*   **诊断补充**：`/debug/state` 新增 `podcast_generation_paused` 与 `on_battery_power`，便于判断后台生成是被播放、冷却窗口还是电池状态暂停。

---

## 📅 第十四阶段：Python 服务层重构与测试基线 (2026-06-18)
*   **目标**：在不改变菜单栏、Chrome extension、URL-Reader 和 MLX 推理主流程的前提下，降低 `backend.py` 的全局状态复杂度，把播放、播客、缓存和稍后朗读拆到明确的服务边界里。
*   **Phase 1-5 实施结果**：
    1.  **RuntimeState 容器**：新增 `core/state/runtime_state.py`，集中管理当前标题、进度、播放状态、当前播客、当前 md5、播客 buffer 与最近活跃时间。
    2.  **PlaybackService**：新增 `core/services/playback_service.py`，把 `PlaybackController` 从路由层抽离出来，统一处理播放 session 换代、`current_task_id` 递增、旧队列清空、TTS 朗读线程和 WAV 播放线程。
    3.  **PodcastService**：新增 `core/services/podcast_service.py`，接管单篇/合集播客后台进程、GPU 独占锁、暂停事件、chunk checkpoint、播客文件列表、置顶、删除与清理。
    4.  **SavedItemsService / CacheService**：把 `saved_for_later.json` 操作和 cache metadata/file 操作从 `backend.py` 中拆出，路由只负责参数转换和响应。
    5.  **backend.py 变薄**：后端保留 FastAPI endpoint、推理 worker、音频 feeder、Bonjour 和 lifespan wiring；业务状态与文件操作交给 service。
    6.  **Smoke tests**：新增 `QwenTTS-App/core/tests/test_services_smoke.py`，覆盖 runtime state、播放 session 失效、播客文件列表与 saved-items FIFO 行为。
*   **验证结果**：
    *   `python -m py_compile QwenTTS-App/core/backend.py QwenTTS-App/core/player.py QwenTTS-App/core/processor.py QwenTTS-App/core/tts_engine.py QwenTTS-App/core/services/*.py QwenTTS-App/core/state/runtime_state.py QwenTTS-App/core/tests/test_services_smoke.py`
    *   `python -m pytest -q QwenTTS-App/core/tests/test_services_smoke.py` → `4 passed`
*   **维护约束更新**：新增播放入口必须走 `PlaybackService`；新增播客生成/文件管理必须走 `PodcastService`；不要把新的长期状态重新堆回 `backend.py`。

---

## 📅 第十五阶段：运行时可观测性与播客任务持久化 (2026-06-18)
*   **目标**：让串台、静音、后台任务残留和长播客生成状态可以被追踪，而不是只能靠主观听感判断。
*   **核心改动**：
    1.  **结构化事件日志**：新增 `core/services/runtime_log.py`，写入 `QwenTTS-App/data/runtime_events.jsonl`，记录播放 session、TTS/WAV 线程、URL 抓取、播客任务、暂停/恢复和错误事件。
    2.  **播客任务状态文件**：新增 `core/services/podcast_jobs.py`，写入 `QwenTTS-App/data/podcast_jobs.json`，记录 `queued/running/done/failed/canceled` 状态、PID、标题、md5、输出路径和错误信息。
    3.  **诊断接口**：新增 `GET /debug/events?limit=50` 和 `GET /podcasts/jobs`；`/debug/state` 也返回最近 podcast jobs。
    4.  **URL 任务引用修复**：`ACTIVE_URL_TASKS` 改为原地清理，避免 `PodcastService` 持有旧 dict 引用后漏判前台活动。
    5.  **测试补充**：service smoke tests 从 4 个扩展到 6 个，覆盖 runtime event log 和 podcast job store。

---
**当前状态**: 🏆 服务层架构 + PlaybackService 播放隔离 + PodcastService 后台生成 + 三档性能模式 + runtime event log + podcast_jobs.json + smoke tests，长文/长播客的串台、发热、任务残留和排障复杂度都已进入可观测状态 | **负责人**: Codex
