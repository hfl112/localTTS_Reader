# QwenTTS 原生 macOS 客户端分发与配置指南

本项目是基于 AppKit + FastAPI + MLX-Audio 开发的 native macOS 菜单栏 TTS 辅助工具。如果您希望在其他干净的 macOS 设备上分发和使用此应用，请按照以下步骤进行配置：

## 1. 安装应用

1. 从 Github Releases 下载最新打包的 `QwenTTS.dmg` 安装包。
2. 双击打开 `QwenTTS.dmg`，将 `QwenTTS.app` 拖入您的 **应用程序 (Applications)** 文件夹中。
3. **绕过 Gatekeeper 拦截**：由于应用尚未进行苹果官方开发者公证，在首次启动时可能会被拦截。请打开终端并运行以下命令以绕过限制：
   ```bash
   xattr -cr /Applications/QwenTTS.app
   ```
4. 现在您可以双击 `/Applications/QwenTTS.app` 启动它。启动后它将常驻在您的 macOS 顶部菜单栏中。

---

## 2. 本地模型配置

为了减小安装包体积，模型权重（约 5.2GB）**没有**打包在应用内。应用首次启动时会处于“未安装模型”的状态。

1. 右键点击菜单栏的 **QwenTTS** 图标，选择 **“设置”** 或者选择 **“打开主窗口” -> “系统设置”**。
2. 找到 **“本地模型管理”** 板块：
   - 推荐使用 `Qwen3-TTS-1.7B-8bit` 模型以获得最佳合成音质。
   - 点击该模型旁边的 **“开始下载”**。应用会调用后台 `ModelManager` 多线程拉取模型压缩包并自动解压校验到 `~/Library/Application Support/QwenTTS/Models/` 中。
3. 下载并原子安装成功后，应用状态将更新为 `✅ 已安装`，后端 Python 推理层将自动加载模型。

---

## 3. 接入 Chrome 网页朗读扩展

应用自带安全配对机制，保障本地 API 不被恶意网页滥用。

1. 在应用的 **“系统设置”** 中，找到 **“扩展配对码”** 字段。
2. 点击 **“生成配对码”**，系统会随机生成一个 8 位安全令牌。
3. 点击底部的 **“保存修改”**。
4. 打开您的 Chrome/Edge 浏览器，安装配套的 `qwen-tts-extension` 扩展。
5. 在扩展的配置面板中，将生成的 8 位配对码填入 **Pairing Token** 栏中，保存即可成功配对连接！

---

## 4. 运行时文件夹结构

对于高级用户，配置和生成物存储于系统 Application Support 中：
- **配置文件**: `~/Library/Application Support/QwenTTS/Data/`
- **下载模型**: `~/Library/Application Support/QwenTTS/Models/`
- **生成播客**: `~/Library/Application Support/QwenTTS/Podcasts/`
- **临时缓存**: `~/Library/Application Support/QwenTTS/Cache/`
- **诊断日志**: `~/Library/Application Support/QwenTTS/Logs/`
