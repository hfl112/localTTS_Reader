"""Provider-agnostic machine-translation engine (translate mode).

Defines a `TranslationProvider` base and three implementations (free Google
endpoint, Microsoft Translator, DeepL) plus a `translate_text` router that
walks the configured order and falls back. If the order contains "llm", the
final fallback delegates to llm_engine.call_llm using the existing translate
prompt.
"""
from typing import List, Optional

import requests

from engine_config import load_engines, translate_setting


# 常见目标语言映射：canonical code -> 各家供应商所需的语言码 + 展示名。
LANG_MAP = {
    "zh":    {"name": "简体中文",   "google": "zh-CN", "microsoft": "zh-Hans", "deepl": "ZH"},
    "zh-TW": {"name": "繁體中文",   "google": "zh-TW", "microsoft": "zh-Hant", "deepl": "ZH"},
    "en":    {"name": "English",   "google": "en", "microsoft": "en", "deepl": "EN"},
    "ja":    {"name": "日本語",     "google": "ja", "microsoft": "ja", "deepl": "JA"},
    "ko":    {"name": "한국어",     "google": "ko", "microsoft": "ko", "deepl": "KO"},
    "fr":    {"name": "Français",  "google": "fr", "microsoft": "fr", "deepl": "FR"},
    "de":    {"name": "Deutsch",   "google": "de", "microsoft": "de", "deepl": "DE"},
    "es":    {"name": "Español",   "google": "es", "microsoft": "es", "deepl": "ES"},
    "ru":    {"name": "Русский",   "google": "ru", "microsoft": "ru", "deepl": "RU"},
    "pt":    {"name": "Português", "google": "pt", "microsoft": "pt", "deepl": "PT"},
    "it":    {"name": "Italiano",  "google": "it", "microsoft": "it", "deepl": "IT"},
}


def _provider_code(code: str, provider: str) -> str:
    entry = LANG_MAP.get(code)
    return entry.get(provider, code) if entry else code


def lang_name(code: str) -> str:
    entry = LANG_MAP.get(code)
    return entry["name"] if entry else code


class TranslationProvider:
    """Base class / protocol for machine-translation providers."""

    name: str = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def translate(self, text: str, target: str = "zh") -> str:
        raise NotImplementedError


def _split_chunks(text: str, max_len: int) -> List[str]:
    """Split text into <= max_len chunks, preferring paragraph then line
    boundaries, then hard-splitting overly long fragments."""
    chunks: List[str] = []
    for para in text.split("\n\n"):
        unit = para + "\n\n"
        if len(unit) <= max_len:
            chunks.append(unit)
            continue
        # Paragraph too long: split by lines.
        buf = ""
        for line in unit.splitlines(keepends=True):
            if len(line) > max_len:
                # Flush buffer, then hard-split the long line.
                if buf:
                    chunks.append(buf)
                    buf = ""
                for i in range(0, len(line), max_len):
                    chunks.append(line[i : i + max_len])
                continue
            if len(buf) + len(line) > max_len:
                chunks.append(buf)
                buf = line
            else:
                buf += line
        if buf:
            chunks.append(buf)
    return [c for c in chunks if c]


class GoogleTranslateProvider(TranslationProvider):
    """Free unofficial Google translate endpoint. No key, always available."""

    name = "google"
    _ENDPOINT = "https://translate.googleapis.com/translate_a/single"
    _MAX_CHUNK = 1500  # endpoint has a query-length limit

    def is_available(self) -> bool:
        return True

    def _translate_chunk(self, chunk: str, tl: str) -> str:
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": tl,
            "dt": "t",
            "q": chunk,
        }
        resp = requests.get(self._ENDPOINT, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # data[0] is a list of [translated, original, ...] segments.
        segments = data[0] or []
        return "".join(seg[0] for seg in segments if seg and seg[0])

    def translate(self, text: str, target: str = "zh") -> str:
        tl = _provider_code(target, "google")
        out: List[str] = []
        for chunk in _split_chunks(text, self._MAX_CHUNK):
            out.append(self._translate_chunk(chunk, tl))
        return "".join(out)


class MicrosoftTranslatorProvider(TranslationProvider):
    """Microsoft Translator. Requires microsoft_key + microsoft_region."""

    name = "microsoft"
    _ENDPOINT = "https://api.cognitive.microsofttranslator.com/translate"
    _MAX_CHUNK = 9000

    def is_available(self) -> bool:
        return bool(translate_setting("microsoft_key") and translate_setting("microsoft_region"))

    def translate(self, text: str, target: str = "zh") -> str:
        key = translate_setting("microsoft_key")
        region = translate_setting("microsoft_region")
        if not key or not region:
            raise RuntimeError("Microsoft Translator key/region not configured")
        to = _provider_code(target, "microsoft")
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Ocp-Apim-Subscription-Region": region,
            "Content-Type": "application/json",
        }
        out: List[str] = []
        for chunk in _split_chunks(text, self._MAX_CHUNK):
            resp = requests.post(
                self._ENDPOINT,
                params={"api-version": "3.0", "to": to},
                headers=headers,
                json=[{"Text": chunk}],
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            out.append(data[0]["translations"][0]["text"])
        return "".join(out)


class DeepLProvider(TranslationProvider):
    """DeepL free API. Requires deepl_key."""

    name = "deepl"
    _ENDPOINT = "https://api-free.deepl.com/v2/translate"
    _MAX_CHUNK = 50000

    def is_available(self) -> bool:
        return bool(translate_setting("deepl_key"))

    def translate(self, text: str, target: str = "zh") -> str:
        key = translate_setting("deepl_key")
        if not key:
            raise RuntimeError("DeepL key not configured")
        target_lang = _provider_code(target, "deepl")
        headers = {"Authorization": f"DeepL-Auth-Key {key}"}
        out: List[str] = []
        for chunk in _split_chunks(text, self._MAX_CHUNK):
            resp = requests.post(
                self._ENDPOINT,
                headers=headers,
                data={"text": chunk, "target_lang": target_lang},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            out.append(data["translations"][0]["text"])
        return "".join(out)


_PROVIDER_FACTORIES = {
    "google": GoogleTranslateProvider,
    "microsoft": MicrosoftTranslatorProvider,
    "deepl": DeepLProvider,
}
_PROVIDER_CACHE: dict = {}


def _get_provider(name: str) -> Optional[TranslationProvider]:
    factory = _PROVIDER_FACTORIES.get(name)
    if factory is None:
        return None
    if name not in _PROVIDER_CACHE:
        _PROVIDER_CACHE[name] = factory()
    return _PROVIDER_CACHE[name]


# LLM 翻译 prompt（按目标语言动态生成）。中文翻译时与原 gemini 行为一致。
def _llm_translate_prompt(target_name: str) -> str:
    return (
        f"你是一个专业的技术和学术翻译专家。请将以下 Markdown/文本内容翻译成通俗易懂、流畅自然的{target_name}。\n"
        "要求：\n"
        "1. 保留原本的 Markdown 格式，例如标题（#）、链接、图片、加粗、代码块等。\n"
        f"2. 翻译要符合{target_name}的表达习惯，术语要准确，通俗易懂。\n"
        f"3. 如果是 YouTube 视频字幕，请将口语化的表达转化为书面、连贯的{target_name}段落。\n"
        f"4. 仅返回翻译后的{target_name}内容，不要包含任何多余的解释、前言或 Markdown 标记符（如 ```markdown）。\n\n"
        "待翻译内容：\n"
    )


class AllTranslationProvidersFailed(Exception):
    pass


def probe_provider(name: str):
    """探测单个翻译供应商连通性。返回 (ok, message)。供 /engines/check 用。"""
    if name == "llm":
        from llm_engine import probe_provider as _llm_probe
        engines = load_engines()
        sel = (engines.get("llm", {}) or {}).get("selected") or "gemini"
        return _llm_probe(sel)
    provider = _get_provider(name)
    if provider is None:
        return False, f"未知翻译供应商: {name}"
    try:
        if not provider.is_available():
            return False, "未配置（缺少 Key / Region）"
    except Exception as e:
        return False, f"不可用: {e}"
    try:
        provider.translate("hello", target="zh")
        return True, "连接成功"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def translate_text(text: str, target: Optional[str] = None) -> str:
    """Route through configured translation providers in order, falling back
    on failure. The special order entry 'llm' delegates to llm_engine.
    target=None 时取配置的 target_lang（默认 zh）。"""
    engines = load_engines()
    if not target:
        target = (engines.get("translate", {}) or {}).get("target_lang") or "zh"
    order = list(engines.get("translate", {}).get("order", []) or [])
    # 配置页选定的 MT 供应商优先。普通翻译只走机器翻译（Google 等），
    # 不再优先 LLM —— LLM 仅用于双人总结/双人翻译。
    selected = engines.get("translate", {}).get("selected")
    if selected:
        order = [selected] + [o for o in order if o != selected]
    tried: List[str] = []
    last_err: Optional[Exception] = None
    for name in order:
        if name == "llm":
            tried.append("llm")
            try:
                print("[Translate] falling back to LLM engine for translation")
                from llm_engine import call_llm
                return call_llm(
                    _llm_translate_prompt(lang_name(target)) + text,
                    tier="standard",
                    step_name="Translate",
                )
            except Exception as e:
                last_err = e
                print(f"[Translate] LLM fallback failed: {type(e).__name__}: {e}")
                continue
        provider = _get_provider(name)
        if provider is None:
            continue
        try:
            if not provider.is_available():
                continue
        except Exception:
            continue
        tried.append(name)
        try:
            print(f"[Translate] trying provider '{name}'")
            return provider.translate(text, target=target)
        except Exception as e:
            last_err = e
            print(f"[Translate] provider '{name}' failed: {type(e).__name__}: {e}. Falling back...")
            continue
    raise AllTranslationProvidersFailed(
        f"All translation providers failed (attempted={tried}). Last error: {last_err}"
    )
