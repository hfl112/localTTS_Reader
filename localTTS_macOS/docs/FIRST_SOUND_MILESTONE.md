# First Sound Milestone

目标:让一台干净 Apple Silicon Mac 在下载安装后,5 分钟内听到第一段声音。

这不是功能清单,而是公开分享前的北极星验收。凡是不能提升首次成功率的改动,都排到这个 milestone 之后。

## 成功定义

用户不打开终端、不读源码、不理解 Python / FastAPI / MLX / 端口,完成以下流程:

```text
下载 QwenTTS.dmg
-> 拖入 Applications
-> 打开 App
-> 首次启动向导检查环境和模型
-> 按提示补齐缺失项
-> 点击“一键试音”
-> 5 分钟内听到“你好,欢迎使用 QwenTTS。”
```

## 验收环境

- Apple Silicon Mac
- macOS 14+
- 未安装开发依赖也应可用
- 用户目录中没有既有 QwenTTS App Support 数据
- 不依赖父级 `QwenTTS-App/`、根 `mlx_audio/`、根 `URL-Reader/` 源码

建议每次发布前用临时 App Support 路径模拟干净用户:

```bash
TTS_APP_SUPPORT_PATH=/tmp/qwentts-first-sound ./QwenTTS.app/Contents/MacOS/QwenTTS
```

## P0 用户路径

### 1. 安装可信

验收:

- DMG 可打开
- App 可拖入 Applications
- 首次打开不需要 `xattr -cr`
- Gatekeeper 不拦截已公证版本

当前缺口:

- README 仍把 `xattr -cr` 作为主路径,只适合内部测试版。
- 还需要 Developer ID 签名、公证、staple 和 `spctl` 验证。

### 2. 首启检查完整

Wizard 必须检查并显示通过/需要处理:

| 检查项 | 通过条件 | 失败动作 |
|---|---|---|
| macOS | 14+ | 告知最低系统要求 |
| Apple Silicon | arm64 | 告知仅支持/推荐 Apple Silicon |
| bundled Python | `Contents/Resources/PythonRuntime/bin/python3` 可执行 | 提示安装包损坏,导出诊断 |
| backend | `Contents/Resources/Backend/core/backend.py` 存在 | 提示安装包损坏,导出诊断 |
| ffmpeg | `Contents/Resources/Tools/ffmpeg` 可执行或配置路径可用 | 提示重新下载完整 DMG/选择路径 |
| reference audio | `reference/bbc_news.wav` 和 `reference/ref_ryan.wav` 存在 | 提示安装包损坏,导出诊断 |
| 模型 | 推荐 `Qwen3-TTS-0.6B` 可用 | 选择已有模型目录/下载说明/可靠自动下载 |
| 音频输出 | 默认输出设备可用 | 提示检查系统声音输出 |

当前状态(2026-06-25 更新):

- ✅ 全部 7 项检查已实现(`EnvironmentDiagnostics.swift`):macOS / Apple Silicon / bundled Python / backend / ffmpeg / 模型(0.6B 或 1.7B)/ 参考音频 / 音频输出设备(CoreAudio)。路径与 `BackendLauncher` 注入的一致,诊断通过即后端能拉起。
- ✅ 模型下载入口存在,失败可重试、可「选择已有模型目录」、可看「下载说明」,安装后自动重检。
- 备注:音频输出无设备时给 `warn`(不硬阻塞),真正无声由末页一键试音兜底。

### 3. 模型缺失处理

验收:

- 无模型时,用户知道为什么不能试音。
- 用户至少有一个可执行动作:
  - 选择已有模型目录
  - 打开下载说明
  - 自动下载推荐模型(只有在可靠时启用)
- 模型安装后 Wizard 自动重新检查。

默认策略:

- 首次体验优先 `Qwen3-TTS-0.6B`。
- `Qwen3-TTS-1.7B-8bit` 放到高级/高质量选项。

### 4. 后端启动

验收:

- Wizard 或主流程启动 native backend。
- 30 秒内 `/health` ready。
- 启动失败时展示原因、日志入口、重试按钮。
- 不显示 backend port、Python path 等普通用户不需要理解的信息。

### 5. 一键试音

验收:

- Wizard 最后一页提供“一键试音”按钮。
- 固定文本:

```text
你好,欢迎使用 QwenTTS。
```

- 成功听到声音后才允许完成 Wizard,并写入 `hasCompletedWizard = true`。
- 失败时按错误类型给出下一步:
  - 模型缺失
  - backend 未就绪
  - 音频输出不可用
  - Metal/内存不足
  - TTS 生成失败

当前状态(2026-06-25 更新):

- ✅ 一键试音已用**真实出声判定**,不再是假阳性。后端新增 `POST /selftest/voice`:走与 `/read`
  同一链路,**阻塞到真的产生音频帧(`ok:true`)或捕获到推理错误(`ok:false`+原因)**才返回;
  `SharedState.audio_frames` 计真实出声帧,worker 异常写 `inference_error`,均经 `/snapshot` 暴露。
  向导 `waitReadyAndTestRead` 改调它,失败时把后端真实错误透传到「试读未通过：…」。
- ✅ 关键修复:默认模型从 `1.7B-8bit` 改为 `0.6B`(`storage.py` default_config 等),与向导下载/推荐
  一致;否则干净用户只装 0.6B 会因加载不存在的 1.7B 而**听不到声音却被判成功**。
- ⏳ 失败「下一步」目前是展示后端真实错误文案 + 通用提示;按错误类型(模型/输出/内存)分支细化仍待做。

### 6. 退出清理

验收:

- 首次试音后退出 App,不残留 backend Python 进程。
- 再次启动不会重复弹 Wizard。
- 删除模型或配置损坏时,会重新进入修复路径。

## P0 工程任务

按顺序推进:

1. ✅ 补 Wizard 检查项:bundled Python/backend/ffmpeg/reference audio/audio output。(7 项已全, 2026-06-25)
2. ✅ 把 Settings 的 voice/performance/seed/temperature 接到真实 backend settings。(phase-5)
3. ✅ 给 Wizard 增加“一键试音”。(已接 `/selftest/voice` 真实出声判定, 2026-06-25)
4. ⏳ 给 `run_diagnostics.py` 增加 bundled backend 启动 smoke。(已用 `/selftest/voice` 手动验证过打包 bundle;尚未固化进 `run_diagnostics.py`)
5. ⬜ 修正 README:内部测试版与公证发布版分开写。
6. ⬜ 做 Developer ID 签名、公证、DMG staple。(当前为 adhoc 签名,Gatekeeper 仍拦)

## 发布前验收清单

每个 release candidate 都跑:

```bash
cd /Users/funanhe/00_MyCode/TTS/localTTS_macOS
python scripts/check_boundaries.py
# package_release 要求自包含 arm64 静态 ffmpeg(Homebrew 动态版会被拒,在干净机会崩)。
# 用静态二进制(如 eugeneware/ffmpeg-static 的 darwin-arm64,只链 /usr/lib+/System):
TTS_FFMPEG_PATH=/path/to/static/ffmpeg python package_release.py
python run_diagnostics.py dist/QwenTTS.app
python make_dmg.py
```

> 注意:用打包后 bundle 内的 python 做冒烟时务必设 `PYTHONDONTWRITEBYTECODE=1`,否则写入的
> `.pyc` 会破坏 app 代码签名,导致 `codesign --verify --deep --strict` 与 `make_dmg.py` 失败。

签名/公证版本额外跑:

```bash
codesign --verify --deep --strict --verbose=2 dist/QwenTTS.app
spctl --assess --type execute --verbose dist/QwenTTS.app
xcrun stapler validate dist/QwenTTS.dmg
spctl --assess --type open --verbose dist/QwenTTS.dmg
```

人工验收:

- 干净 App Support,无模型
- 干净 App Support,已有 0.6B 模型
- 无网络
- backend 启动失败
- 系统扬声器
- 蓝牙耳机
- sleep/wake 后再试音

## 非目标

以下不阻塞 First Sound:

- Chrome extension 完整配对体验
- 批量 podcast
- 多 LLM provider 精细配置
- cache 高级管理
- Sparkle 自动更新
- 高级 TTS 参数调优

这些功能保留,但不应该影响首次试音路径。
