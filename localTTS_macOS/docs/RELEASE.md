# QwenTTS 签名 · 公证 · 发布流程

目标：用户下载 DMG → 拖入 Applications → 双击直接打开，**无需 `xattr -cr`**。

> 标注 **🔑 需要你的 Apple 凭据** 的步骤只能由持有 Apple Developer 账号的人执行；
> 其余步骤（构建、本地校验、DMG、文档）任何人都能跑。仓库脚本：`package_release.py`
> / `make_dmg.py` / `notarize_dmg.py` / `run_diagnostics.py`。

## 0. 一次性前置（🔑）

1. 加入 **Apple Developer Program**。
2. 在钥匙串安装 **Developer ID Application** 证书（Xcode → Settings → Accounts →
   Manage Certificates，或开发者网站下载）。确认：
   ```bash
   security find-identity -v -p codesigning   # 应看到 "Developer ID Application: <Name> (<TEAMID>)"
   ```
3. 创建 **notarytool 钥匙串配置**（用 App 专用密码，避免明文）：
   ```bash
   xcrun notarytool store-credentials QwenTTS-Notary \
     --apple-id "you@example.com" --team-id "TEAMID" --password "app-specific-password"
   ```
   之后用 `--keychain-profile QwenTTS-Notary`，**不再出现明文密码**。
4. 准备一个 **静态 arm64 ffmpeg**（`package_release.py` 会拒绝 Homebrew 动态版，见 P0.5）：
   ```bash
   lipo -archs /path/to/ffmpeg          # 必须含 arm64
   otool -L /path/to/ffmpeg             # 不应依赖 /opt/homebrew 动态库
   ```

## 1. 构建已签名 App

```bash
cd localTTS_macOS
export TTS_FFMPEG_PATH="/path/to/static/ffmpeg"          # 必填（否则按 P0.5 报错）
export TTS_SIGNING_IDENTITY="Developer ID Application: <Name> (<TEAMID>)"   # 🔑 缺省为 "-"（ad-hoc，仅本地测试）
python package_release.py
```
- 内部：xcodebuild（`CODE_SIGNING_ALLOWED=NO`）→ 装入 backend/PythonRuntime/ffmpeg →
  内→外逐个 `codesign`（带 `QwenTTS.entitlements` + `--options runtime`，仅在非 ad-hoc 时）。
- 不带 `TTS_SIGNING_IDENTITY` 会得到 **ad-hoc** 包：可本地跑，但 Gatekeeper 不接受、无法公证。

## 2. 本地校验（无凭据也能跑）

```bash
python run_diagnostics.py dist/QwenTTS.app
codesign --verify --deep --strict --verbose=2 dist/QwenTTS.app
```
`run_diagnostics.py` 现在会检查：bundle 结构 / Python 可重定位 / **entitlements 齐全** /
**Hardened Runtime** / **Gatekeeper(spctl)** / **公证装订**。其中后三项对 Developer ID 构建
是硬门禁，对 ad-hoc 构建仅作 INFO（`[INFO] signing kind: ...` 会标明）。
**发布前这一步必须全 `[PASS]`（signing kind 应为 developer-id）。**

## 3. 打 DMG

```bash
python make_dmg.py        # dist/QwenTTS.dmg（拖拽到 Applications 的常规 DMG）
```

## 4. 公证 + 装订（🔑）

```bash
python notarize_dmg.py --keychain-profile QwenTTS-Notary
```
内部：`xcrun notarytool submit dist/QwenTTS.dmg --keychain-profile … --wait` →
`xcrun stapler staple dist/QwenTTS.dmg`。若 notary 拒绝，用
`xcrun notarytool log <submission-id> --keychain-profile QwenTTS-Notary` 看原因
（最常见：entitlements/Hardened Runtime 缺失 → 回到第 1 步确认 `TTS_SIGNING_IDENTITY` 已设）。

## 5. 公证后验证（务必做，notarize_dmg.py 未自动做）

```bash
xcrun stapler validate dist/QwenTTS.dmg
spctl --assess --type open --verbose=4 dist/QwenTTS.dmg          # DMG: accepted
# 装好后验证 .app 本身：
spctl --assess --type execute --verbose=4 /Applications/QwenTTS.app   # source=Notarized Developer ID
```
三条都通过，才表示用户双击不会被拦。

## 6. 更新 README（去掉 `xattr -cr` 主路径）

正式签名公证后，安装主路径改为：**下载 DMG → 拖入 Applications → 打开**。
把 `xattr -cr /Applications/QwenTTS.app` 降级到「开发/未公证测试版的故障排查」小节，
不再作为正常安装步骤。

## 7. GitHub Release

```bash
cd dist
shasum -a 256 QwenTTS.dmg > SHA256SUMS.txt
gh release create v1.0.0 QwenTTS.dmg SHA256SUMS.txt \
  --title "QwenTTS 1.0.0" \
  --notes-file ../docs/RELEASE_NOTES_1.0.0.md   # 自备：变更、已知问题、隐私政策链接
```
随附：`QwenTTS.dmg`、`SHA256SUMS.txt`、release notes、隐私政策链接（`docs/PRIVACY_POLICY.md`）、已知问题。

## 建议顺序（与三大块对齐）

1. 先出 **ad-hoc internal alpha**（不设 `TTS_SIGNING_IDENTITY`）自测：第 1–3 步，
   `run_diagnostics` 会以 INFO 提示尚未签名——功能层面可验证。
2. 配好 Developer ID 后，第 1–5 步出**已公证**包，确保 `run_diagnostics` 全 PASS。
3. 第 6–7 步公开发布。
