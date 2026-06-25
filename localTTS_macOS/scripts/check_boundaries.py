#!/usr/bin/env python3
"""Static native↔legacy boundary check for localTTS_macOS.

原生 QwenTTS（`QwenTTS/` + `backend/`）不得在**依赖层面**耦合旧版 `QwenTTS-App/`，
也不得从父级 `../URL-Reader` / `../mlx_audio` 复制源码。本脚本做静态扫描，发现违规
即非零退出，可接入手动发布检查 / CI。

检查项（针对真实依赖，而非单纯的名字出现）：
  1. 发布脚本不从父级 `QwenTTS-App` / `../URL-Reader` / `../mlx_audio` 复制源码。
  2. Swift（`QwenTTS/`）不硬编码 `QwenTTS-App` 路径字面量。
  3. 后端运行时（`backend/core`、`backend/URL-Reader`）不 import `QwenTTS-App`、
     不把它加入 `sys.path` / `PYTHONPATH`。

允许（不算违规）：注释/文档字符串、`core/paths.py` 中显式的一次性 legacy data
migration、print/log 里的命名字符串（属命名而非依赖）。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

violations: list[str] = []


def _iter_files(rel_dir: str, suffix: str):
    base = os.path.join(ROOT, rel_dir)
    for dirpath, _dirs, files in os.walk(base):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if f.endswith(suffix):
                yield os.path.join(dirpath, f)


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT)


def check_release_scripts() -> None:
    """发布脚本里出现 QwenTTS-App / ../URL-Reader / ../mlx_audio（非注释）即违规。"""
    bad = re.compile(r"QwenTTS-App|\.\./URL-Reader|\.\./mlx_audio")
    for name in ("package_release.py", "make_dmg.py", "notarize_dmg.py", "run_diagnostics.py"):
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            continue
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if line.lstrip().startswith("#"):
                continue
            if bad.search(line):
                violations.append(f"{name}:{i}: 发布脚本引用了父级 legacy 源码 -> {line.strip()}")


def check_swift() -> None:
    """Swift 代码出现 QwenTTS-App 字面量（非 // 注释）即违规。"""
    for path in _iter_files("QwenTTS/QwenTTS", ".swift"):
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if line.lstrip().startswith("//"):
                continue
            if "QwenTTS-App" in line:
                violations.append(f"{_rel(path)}:{i}: Swift 硬编码 legacy 路径 -> {line.strip()}")


def check_backend_runtime() -> None:
    """后端运行时 import QwenTTS-App 或把它加入 sys.path/PYTHONPATH 即违规。
    （migration 的数据路径拼接、print 字符串不匹配这些模式，不会误报。）"""
    dep_patterns = [
        re.compile(r"(sys\.path|PYTHONPATH).*QwenTTS[-_]?App", re.IGNORECASE),
        re.compile(r"\b(from|import)\s+QwenTTS[-_]?App"),
    ]
    for rel_dir in ("backend/core", "backend/URL-Reader"):
        for path in _iter_files(rel_dir, ".py"):
            for i, line in enumerate(open(path, encoding="utf-8"), 1):
                if line.lstrip().startswith("#"):
                    continue
                for pat in dep_patterns:
                    if pat.search(line):
                        violations.append(
                            f"{_rel(path)}:{i}: 后端运行时依赖 legacy QwenTTS-App -> {line.strip()}"
                        )


def main() -> int:
    check_release_scripts()
    check_swift()
    check_backend_runtime()
    if violations:
        print("native↔legacy 边界检查：发现违规")
        for v in violations:
            print("  ✗ " + v)
        return 1
    print("native↔legacy 边界检查：通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
