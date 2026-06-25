# localTTS_macOS 仓库整理计划（修订版）

本文档给出一个**低风险、可逐步执行**的整理方案。目标不是重写架构，而是把原生 macOS App、原生 Python 后端、发布产物、实验文件和旧版 App 的边界收紧，降低误删、误改、误打包的风险。

> **修订说明（与初版的关键差异）**
> 1. **新增 Step 0（前置）**：`localTTS_macOS/` 当前对父级 git 仓库**完全未跟踪**（`git ls-files` 返回 0 个文件）。在无版本控制的目录上做"移动/删除"是**不可恢复**的，因此必须先纳入 git 或备份，"低风险"才成立。
> 2. **修正 Step 7 的 Xcode 判断**：完整 **Xcode 26.5 在 `/Applications/Xcode.app`**，用 `DEVELOPER_DIR` 即可编译验证，不存在"只能 CLT 无法验证"的借口。
> 3. **澄清 Step 4 现状**：经核实 `package_release.py` **只从 `ROOT/backend` 复制**源码，无任何父级 `QwenTTS-App` 引用——native↔legacy 边界在打包脚本里**已干净**，本步从"修复风险"降级为"加护栏防回归"。

## 目标状态

整理完成后，`localTTS_macOS/` 根目录只保留入口级文件和一级模块：

```text
localTTS_macOS/
├── AGENTS.md / README.md / CLAUDE.md
├── QwenTTS/                  # Swift/AppKit 原生 App
├── backend/                  # 原生版独立 Python 后端
├── docs/                     # 架构、计划、记录
├── scripts/                  # 本地检查、维护脚本
├── experiments/              # 仍有参考价值的 spike / 临时测试
├── package_release.py / make_dmg.py / notarize_dmg.py / run_diagnostics.py
└── requirements.prod.txt / requirements.prod.lock
```

构建产物（`build/`、`dist/`、`release_runtime/`）和缓存可本地存在，但**不作为源码边界的一部分、不进版本控制**；旧版稳定 App 保持在父级 `QwenTTS-App/`，不参与 native 开发。

## 总体验收（全部步骤完成后）

```bash
cd /Users/funanhe/00_MyCode/TTS/localTTS_macOS
git -C .. ls-files localTTS_macOS | wc -l        # > 0（源码已纳入版本控制）
git -C .. ls-files localTTS_macOS | grep -E '^localTTS_macOS/(build|dist|release_runtime)/' ; echo "exit=$?"  # 应无输出
find . -maxdepth 1 -type f \( -name '*.swift' -o -perm -111 \) | grep -vE 'package_release|make_dmg|notarize|run_diagnostics'  # 根目录无散落 spike/二进制
python scripts/check_boundaries.py               # 边界检查通过
cd backend && python -m pytest core/tests/ -v    # 后端测试全过（当前 34 项）
```

---

## Step 0（前置·最高优先）：纳入版本控制 / 建立可恢复点

- **目标**：在动任何文件之前，让 `localTTS_macOS/` 的源码进入 git（产物除外），使后续每一步移动/删除都可回滚。
- **步骤**：
  1. 先做一次性物理备份（保险）：
     ```bash
     cd /Users/funanhe/00_MyCode/TTS
     tar --exclude='localTTS_macOS/build' --exclude='localTTS_macOS/dist' \
         --exclude='localTTS_macOS/release_runtime' --exclude='*/__pycache__' \
         -czf /tmp/localTTS_macOS_backup_$(date +%Y%m%d).tgz localTTS_macOS
     ```
  2. 确认父级 `.gitignore` 覆盖 native 产物（缺则补）：`localTTS_macOS/build/`、`localTTS_macOS/dist/`、`localTTS_macOS/release_runtime/`、`**/__pycache__/`、`**/.pytest_cache/`、`**/*.dmg`、`.DS_Store`。
     ```bash
     cd /Users/funanhe/00_MyCode/TTS
     git check-ignore localTTS_macOS/build localTTS_macOS/dist localTTS_macOS/release_runtime \
       localTTS_macOS/dist/QwenTTS.dmg   # 每个都应被命中（有输出=已忽略）
     ```
  3. 纳入源码并提交（产物因 .gitignore 不会被加入）：
     ```bash
     git add localTTS_macOS
     git status --short | grep '^A' | grep -E 'build/|dist/|release_runtime/' && echo "!! 产物误入暂存，先修 .gitignore" || echo "ok: no products staged"
     git commit -m "chore: bring localTTS_macOS native project under version control (pre-cleanup baseline)"
     ```
- **验收标准**：
  - `git -C .. ls-files localTTS_macOS | wc -l` > 0；
  - 暂存/提交中**不含** `build/`、`dist/`、`release_runtime/`、`__pycache__`、`.dmg`；
  - `/tmp/localTTS_macOS_backup_*.tgz` 存在。
- **未达标不得进入后续任何删除步骤。**

---

## Step 1：盘点并分类根目录文件（只盘点，不删）

- **目标**：在移动/删除前明确每个根目录条目的归属，确认无源码引用。
- **步骤**：
  ```bash
  cd /Users/funanhe/00_MyCode/TTS/localTTS_macOS && find . -maxdepth 1 -print | sort
  rg -n "ProcessSupervisorSpike|QwenTTS_bin|test_click|test_menu|watchdog_helper" . -g '!dist/*' -g '!build/*'
  ```
- **分类建议**：

  | 路径 | 处理 | 原因 |
  |---|---|---|
  | `QwenTTS/`、`backend/`、`docs/` | 保留 | 主工程 / 后端 / 文档 |
  | `package_release.py`、`make_dmg.py`、`notarize_dmg.py`、`run_diagnostics.py` | 保留 | 发布与诊断入口 |
  | `requirements.prod.txt`、`requirements.prod.lock` | 保留 | 依赖声明/锁 |
  | `ProcessSupervisorSpike.swift` | 移到 `experiments/process-supervisor/` | 进程监督 spike，仍有参考价值 |
  | `watchdog_helper.py` | **随 spike 一起**移到 `experiments/process-supervisor/` | 仅被 `ProcessSupervisorSpike.swift` 引用（真实 app 走 BackendLauncher 的 FD 继承，不用它） |
  | `test_click.swift`、`test_menu.swift` | 移到 `experiments/ui-smoke/` | 临时 UI 测试 |
  | `ProcessSupervisorSpike`、`QwenTTS_bin` | 删除 | 编译后二进制，可由源码重建 |
  | `__pycache__/`、`.pytest_cache/` | 删除 | 纯缓存 |
  | `build/`、`dist/`、`release_runtime/` | 本地保留、不入 git | 产物 |

- **验收标准**：每个"移动/删除"项都在上面 `rg` 输出中确认**无真实源码（非 spike、非产物）引用**；分类表覆盖 `find` 列出的所有条目，无遗漏。

---

## Step 2：建立 `scripts/` 与 `experiments/`，迁移 spike

- **目标**：根目录不再出现 spike、临时测试、编译二进制。
- **步骤**（用 `git mv` 保留历史；Step 0 后才可用）：
  ```bash
  mkdir -p experiments/process-supervisor experiments/ui-smoke scripts
  git mv ProcessSupervisorSpike.swift experiments/process-supervisor/
  git mv watchdog_helper.py           experiments/process-supervisor/
  git mv test_click.swift test_menu.swift experiments/ui-smoke/
  git rm ProcessSupervisorSpike QwenTTS_bin     # 编译产物，删除
  # 若 spike 内硬编码了 watchdog_helper.py 旧路径，同步更新为新相对路径
  rg -n "watchdog_helper.py" experiments/
  ```
- **验收标准**：
  - `find . -maxdepth 1 -type f` 中不再有 `*Spike*`、`test_click/menu.swift`、`*_bin`；
  - `git status` 显示为 rename（历史保留）而非 delete+add；
  - `rg "ProcessSupervisorSpike|QwenTTS_bin|test_click|test_menu" QwenTTS backend` 无命中（主工程未引用被移动项）。

---

## Step 3：清缓存，确认产物不入库

- **目标**：缓存目录清除；`build/`/`dist/`/`release_runtime/` 本地存在但不被 git 跟踪。
- **步骤**：
  ```bash
  find . -type d -name __pycache__ -not -path './dist/*' -prune -exec rm -rf {} +
  rm -rf .pytest_cache
  cd /Users/funanhe/00_MyCode/TTS
  git ls-files localTTS_macOS/build localTTS_macOS/dist localTTS_macOS/release_runtime localTTS_macOS/__pycache__
  ```
- **验收标准**：
  - 上面 `git ls-files` **无输出**（这些路径未被跟踪）；
  - `git status --ignored --short localTTS_macOS | grep -E 'build/|dist/|release_runtime/'` 显示它们处于 ignored 状态；
  - 删除缓存不影响 `pytest`（见 Step 6）。

---

## Step 4：固化 native↔legacy 边界（加护栏，防回归）

- **目标**：保证 native 工程永不依赖父级 `QwenTTS-App/`、父级 `URL-Reader/`、父级 `mlx_audio/`。
- **现状**：已核实 `package_release.py` 仅从 `ROOT/backend` 复制（`BACKEND_SOURCE = ROOT/"backend"`，mlx_audio/URL-Reader/reference 均在其下），**无父级 `QwenTTS-App` 引用**；`paths.migrate_legacy_data` 指向 `backend/QwenTTS-App/data`（不存在→no-op）。即边界当前**已干净**，本步是防止未来回归。
- **步骤**（核心原则）：
  - `QwenTTS/` 只管原生 App；`backend/` 只管原生后端；父级 `QwenTTS-App/` 不被 native 构建脚本读取、不自动同步、不在 native 开发中修改。
  - 扫描风险点：
    ```bash
    rg -n "QwenTTS-App|\.\./URL-Reader|\.\./mlx_audio" QwenTTS backend package_release.py *.py
    ```
  - **允许**保留的引用：文档历史说明、`AGENTS.md`/`CLAUDE.md` 边界约束、明确标注的一次性 legacy data migration。
  - **不允许**：打包脚本从父级复制源码、Swift 启动逻辑硬编码旧版路径、后端把旧版 `QwenTTS-App/core` 加入 `PYTHONPATH`。
- **验收标准**：上述 `rg` 的命中**全部**落在"允许"类别（migration / 文档 / 边界约束）；运行 `scripts/check_boundaries.py`（Step 5）通过。

---

## Step 5：新增边界检查脚本 `scripts/check_boundaries.py`

- **目标**：把 Step 4 的边界从"口头约定"变成可执行的静态检查。
- **步骤**：新增脚本，静态扫描并在违规时非零退出：
  1. `package_release.py` 不从父级 `QwenTTS-App/`、父级 `URL-Reader/`、父级 `mlx_audio/` 复制；
  2. `QwenTTS/`（Swift）与 `backend/`（运行时 Python）不硬编码旧版 App 源码路径；
  3. 允许文档与显式 migration 注释出现 `legacy`/`QwenTTS-App` 字样。
- **验收标准**：
  - `python scripts/check_boundaries.py` 在当前代码上**退出码 0**；
  - 人为在 `package_release.py` 插入一行 `QwenTTS-App` 复制后，脚本**非零退出**（验证它真的能拦住）。

---

## Step 6：修正文档旧命名 + 跑后端测试

- **目标**：文档不把 native 后端与 legacy `QwenTTS-App` 混淆；改动未破坏后端。
- **现状**：`backend/URL-Reader/README.md` 的 `gemini_engine` 链接、`podcast_service` 的 legacy 路径已在先前清理中修正；本步处理**剩余**命名。
- **步骤**：
  ```bash
  rg -n "QwenTTS-App" backend docs AGENTS.md CLAUDE.md
  # 把"native 后端依赖旧版 App"式的误导表述改为 native QwenTTS backend / localTTS_macOS/backend；
  # 历史/兼容说明明确写成 "legacy QwenTTS-App"
  cd backend && python -m pytest core/tests/ -v
  ```
- **验收标准**：
  - 剩余 `QwenTTS-App` 命中**全部**属于边界说明 / 历史说明 / 显式 legacy migration；
  - `pytest core/tests/` 全过（当前基线 **34 项**）。

---

## Step 7（可后置·重构阶段）：收紧 Swift 后端管理职责

- **目标**：避免 `BackendProcessManager` 持续膨胀，保持其为"协调状态机"，职责外移。
- **步骤**：按职责拆分，并**用真实 Xcode 编译验证**：

  | 职责 | 建议归属 |
  |---|---|
  | `posix_spawn`/管道/进程组/终止 | `BackendLauncher`（已存在） |
  | `/health` 轮询、超时判断 | `BackendHealthMonitor`（新） |
  | 重启退避、崩溃恢复策略 | `BackendSupervisor` 或 manager 内小模块 |
  | HTTP endpoint 调用 | `BackendAPIClient`（已存在） |
  | 日志/环境/bundle 检查 | `DiagnosticsManager`（已存在） |

  编译验证（本机有完整 Xcode 26.5，用 `DEVELOPER_DIR` 覆盖，无需 sudo 切 xcode-select）：
  ```bash
  cd QwenTTS && xcodegen generate
  DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
    -project QwenTTS.xcodeproj -scheme QwenTTS -configuration Debug \
    -derivedDataPath /tmp/qwentts_build CODE_SIGNING_ALLOWED=NO build
  ```
- **验收标准**：
  - `BUILD SUCCEEDED`，且无新增 actor/Sendable/隔离 warning；
  - `BackendProcessManager` 行数明显下降，新职责类可独立单测/复用；
  - 启动 Debug 构建后 `~/Library/Application Support/QwenTTS/runtime.json` 正常、`curl /health` 200。

---

## 建议执行顺序（低风险 → 高收益）

0. **Step 0：纳入版本控制 + 备份（前置，未完成不得删任何东西）。**
1. Step 3 清缓存（`__pycache__`、`.pytest_cache`）。
2. Step 1 盘点 → Step 2 移 spike/临时测试到 `experiments/`、删二进制。
3. Step 3 余下：确认 `build/`/`dist/`/`release_runtime/` 未被 git 跟踪。
4. Step 6 修文档旧命名 + 跑 `pytest`。
5. Step 5 新增 `scripts/check_boundaries.py`（Step 4 的可执行护栏）。
6. Step 7 有完整 Xcode 时再做 Swift 职责拆分 + 编译验证。

## 完成后的预期效果

- 新贡献者一眼看出 `QwenTTS/`=原生 UI、`backend/`=原生后端。
- 根目录不再混杂 spike、临时测试、缓存、二进制。
- **源码已进版本控制**，任何整理动作可回滚（不再是"untracked 树上 rm 即永久丢失"）。
- 打包脚本只从 `localTTS_macOS/backend/` 取 native 资源，边界由 `check_boundaries.py` 守护。
- 文档不再把 native 后端与 legacy `QwenTTS-App` 混为一谈。
- Swift 进程管理有清晰的职责拆分目标与可执行的编译验证入口。
