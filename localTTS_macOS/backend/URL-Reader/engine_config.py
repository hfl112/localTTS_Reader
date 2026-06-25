"""Shared config reader for the provider-agnostic LLM / translation engines.

Reads the App's config.json `engines` section.  Path resolution mirrors
core/paths.py: TTS_DATA_PATH overrides, otherwise the App Support default.
API keys missing from config fall back to environment variables.
"""
import copy
import json
import os
from typing import Any, Dict, Optional

# Default App Support root (matches core/paths.py RuntimePaths defaults).
_DEFAULT_APP_SUPPORT = os.path.expanduser("~/Library/Application Support/QwenTTS")


def _default_data_path() -> str:
    explicit = os.environ.get("TTS_DATA_PATH")
    if explicit:
        return os.path.abspath(explicit)
    app_support = os.environ.get("TTS_APP_SUPPORT_PATH") or _DEFAULT_APP_SUPPORT
    return os.path.abspath(os.path.join(app_support, "Data"))


def config_path() -> str:
    return os.path.join(_default_data_path(), "config.json")


# Locked default engines schema (used when config has no `engines` section).
DEFAULT_ENGINES: Dict[str, Any] = {
    "translate": {
        "selected": "google",
        "target_lang": "zh",
        "order": ["google", "microsoft", "deepl"],
        "microsoft_key": "",
        "microsoft_region": "",
        "deepl_key": "",
    },
    "llm": {
        "selected": "gemini",
        "order": ["gemini", "deepseek", "openai", "claude", "local"],
        "keys": {"gemini": "", "claude": "", "openai": "", "deepseek": ""},
        "local_model_path": "",
        "models": {
            "gemini": "gemini-flash-latest",
            "claude": "claude-sonnet-4-6",
            "openai": "gpt-4o",
            "deepseek": "deepseek-chat",
        },
    },
}

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_engines() -> Dict[str, Any]:
    """Return the engines config, merged over locked defaults."""
    cfg: Dict[str, Any] = {}
    path = config_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    engines = cfg.get("engines") if isinstance(cfg, dict) else None
    if not isinstance(engines, dict):
        return copy.deepcopy(DEFAULT_ENGINES)
    return _deep_merge(DEFAULT_ENGINES, engines)


def llm_key(provider: str) -> Optional[str]:
    """Resolve an LLM provider API key. 仅来自前端配置（完全解耦本地 .env / env）。"""
    keys = load_engines().get("llm", {}).get("keys", {}) or {}
    return keys.get(provider) or None


def translate_setting(name: str) -> Optional[str]:
    """Resolve a translate provider setting. 仅来自前端配置。"""
    tr = load_engines().get("translate", {}) or {}
    return tr.get(name) or None
