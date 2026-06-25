"""Provider-agnostic creative LLM engine (summaries / dual-host podcasts).

Defines a `LLMProvider` protocol and five implementations (Gemini, Claude,
OpenAI, DeepSeek, Local MLX) plus a `call_llm` router that walks the configured
order, skips unavailable providers, and falls back on failure.

All third-party SDK imports are lazy/tolerant: a missing SDK only makes that
provider report `is_available() == False`; the module always imports.
"""
import os
from typing import Optional

from engine_config import llm_key, load_engines, DEFAULT_ENGINES


class AllProvidersFailed(Exception):
    """Raised when every configured LLM provider is unavailable or errored."""


def _model(provider: str) -> str:
    # 默认模型只在 engine_config.DEFAULT_ENGINES 单处定义（此前 llm_engine 另存一份
    # _DEFAULT_MODELS 会与之漂移）。load_engines() 已 merge 默认值，这里的默认仅作为
    # config 显式给了残缺 models dict 时的兜底。
    models = (load_engines().get("llm", {}) or {}).get("models", {}) or {}
    default = (DEFAULT_ENGINES.get("llm", {}) or {}).get("models", {}) or {}
    return models.get(provider) or default.get(provider, "")


class LLMProvider:
    """Base class / protocol for creative LLM providers."""

    name: str = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def generate(self, prompt: str, tier: str = "standard", json_mode: bool = False,
                 max_tokens: Optional[int] = None) -> str:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    """官方 google-genai SDK，单模型（默认 gemini-2.5-flash）。
    key 只来自前端配置（完全解耦本地 .env），不再依赖 gemini_engine。"""

    name = "gemini"

    def is_available(self) -> bool:
        try:
            from google import genai  # noqa: F401
        except Exception:
            return False
        return bool(llm_key("gemini"))

    def generate(self, prompt: str, tier: str = "standard", json_mode: bool = False,
                 max_tokens: Optional[int] = None) -> str:
        from google import genai
        from google.genai import types

        key = llm_key("gemini")
        if not key:
            raise RuntimeError("No Gemini API key configured")
        client = genai.Client(api_key=key)
        cfg_kwargs = {}
        if json_mode:
            cfg_kwargs["response_mime_type"] = "application/json"
        if max_tokens:
            cfg_kwargs["max_output_tokens"] = max_tokens
        cfg = types.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None
        res = client.models.generate_content(model=_model("gemini"), contents=prompt, config=cfg)
        text = getattr(res, "text", "") or ""
        if not text.strip():
            raise RuntimeError("Gemini returned empty content")
        return text


class ClaudeProvider(LLMProvider):
    """Official anthropic SDK. Omits temperature/top_p/thinking (opus-4-8
    family rejects them). Honors stop_reason == 'refusal' by discarding."""

    name = "claude"

    def is_available(self) -> bool:
        try:
            import anthropic  # noqa: F401
        except Exception:
            return False
        return bool(llm_key("claude"))

    def generate(self, prompt: str, tier: str = "standard", json_mode: bool = False,
                 max_tokens: Optional[int] = None) -> str:
        import anthropic

        key = llm_key("claude")
        if not key:
            raise RuntimeError("No Claude API key configured")
        model = _model("claude")
        client = anthropic.Anthropic(api_key=key)
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens or 8192,
                messages=[{"role": "user", "content": prompt}],
            )
        except (anthropic.RateLimitError, anthropic.APIError) as e:
            raise RuntimeError(f"Claude API error: {e}") from e
        if getattr(resp, "stop_reason", None) == "refusal":
            raise RuntimeError("Claude refused the request (stop_reason=refusal)")
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
        if not text.strip():
            raise RuntimeError("Claude returned empty content")
        return text


class OpenAIProvider(LLMProvider):
    """Official openai SDK (chat.completions)."""

    name = "openai"
    _base_url: Optional[str] = None  # overridden by DeepSeek
    _key_name = "openai"

    def is_available(self) -> bool:
        try:
            import openai  # noqa: F401
        except Exception:
            return False
        return bool(llm_key(self._key_name))

    def generate(self, prompt: str, tier: str = "standard", json_mode: bool = False,
                 max_tokens: Optional[int] = None) -> str:
        from openai import OpenAI

        key = llm_key(self._key_name)
        if not key:
            raise RuntimeError(f"No {self.name} API key configured")
        model = _model(self.name)
        kwargs = {"base_url": self._base_url} if self._base_url else {}
        client = OpenAI(api_key=key, **kwargs)
        params = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens or 8192,
        }
        if json_mode:
            params["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**params)
        text = resp.choices[0].message.content or ""
        if not text.strip():
            raise RuntimeError(f"{self.name} returned empty content")
        return text


class DeepSeekProvider(OpenAIProvider):
    """Reuses the openai SDK against the DeepSeek base_url."""

    name = "deepseek"
    _base_url = "https://api.deepseek.com"
    _key_name = "deepseek"


class LocalMLXProvider(LLMProvider):
    """Best-effort local model via mlx_lm. Disabled unless config provides a
    `local_model_path` that exists on disk. Caches the loaded model."""

    name = "local"

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._loaded_path: Optional[str] = None

    def _model_path(self) -> str:
        return (load_engines().get("llm", {}) or {}).get("local_model_path", "") or ""

    def is_available(self) -> bool:
        try:
            import mlx_lm  # noqa: F401
        except Exception:
            return False
        path = self._model_path()
        # A HF repo id (no slash-as-dir) is also acceptable, but to stay
        # conservative we require a local path that exists.
        return bool(path) and os.path.exists(path)

    def _ensure_loaded(self, path: str) -> None:
        if self._model is not None and self._loaded_path == path:
            return
        from mlx_lm import load
        self._model, self._tokenizer = load(path)
        self._loaded_path = path

    def generate(self, prompt: str, tier: str = "standard", json_mode: bool = False,
                 max_tokens: Optional[int] = None) -> str:
        from mlx_lm import generate

        path = self._model_path()
        if not path or not os.path.exists(path):
            raise RuntimeError("No local model path configured")
        self._ensure_loaded(path)
        # Apply chat template when available so the prompt is framed correctly.
        text_prompt = prompt
        try:
            if self._tokenizer is not None and getattr(
                self._tokenizer, "chat_template", None
            ):
                text_prompt = self._tokenizer.apply_chat_template(
                    [{"role": "user", "content": prompt}],
                    add_generation_prompt=True,
                    tokenize=False,
                )
        except Exception:
            text_prompt = prompt
        out = generate(
            self._model,
            self._tokenizer,
            prompt=text_prompt,
            max_tokens=max_tokens or 4096,
            verbose=False,
        )
        if not out or not out.strip():
            raise RuntimeError("Local MLX model returned empty content")
        return out


# Registry of provider factories keyed by config order name.
_PROVIDER_FACTORIES = {
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "deepseek": DeepSeekProvider,
    "local": LocalMLXProvider,
}

# Cache singletons so the local provider keeps its loaded model.
_PROVIDER_CACHE: dict = {}


def _get_provider(name: str) -> Optional[LLMProvider]:
    factory = _PROVIDER_FACTORIES.get(name)
    if factory is None:
        return None
    if name not in _PROVIDER_CACHE:
        _PROVIDER_CACHE[name] = factory()
    return _PROVIDER_CACHE[name]


def llm_selected_available() -> bool:
    """配置页选定的 LLM 供应商是否可用（用于翻译家族判断是否优先走 LLM）。"""
    try:
        selected = (load_engines().get("llm", {}) or {}).get("selected")
        if not selected:
            return False
        provider = _get_provider(selected)
        return bool(provider and provider.is_available())
    except Exception:
        return False


def probe_provider(name: str):
    """探测单个 LLM 供应商连通性。返回 (ok, message)。供 /engines/check 用。"""
    provider = _get_provider(name)
    if provider is None:
        return False, f"未知供应商: {name}"
    try:
        if not provider.is_available():
            return False, "未配置或不可用（缺少 API Key / SDK / 本地模型）"
    except Exception as e:
        return False, f"不可用: {e}"
    try:
        # 探活只验证连通/鉴权：用极小 max_tokens 上限，确保即便模型忽略 prompt
        # 约束也只产生几个 token 的计费输出（避免每次点击 /engines/check 浪费配额）。
        provider.generate("回复一个字符即可", tier="standard", max_tokens=8)
        return True, "连接成功"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def call_llm(
    prompt: str, tier: str = "standard", step_name: str = "", json_mode: bool = False
) -> str:
    """Route through configured LLM providers in order, falling back on
    failure. Raises AllProvidersFailed if none succeed."""
    engines = load_engines()
    order = list(engines.get("llm", {}).get("order", []) or [])
    # 用户在配置页选定的供应商优先
    selected = engines.get("llm", {}).get("selected")
    if selected:
        order = [selected] + [o for o in order if o != selected]
    tried = []
    last_err: Optional[Exception] = None
    for name in order:
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
            print(f"[LLM] {step_name or 'call'} -> trying provider '{name}' (tier={tier})")
            return provider.generate(prompt, tier=tier, json_mode=json_mode)
        except Exception as e:
            last_err = e
            print(f"[LLM] provider '{name}' failed: {type(e).__name__}: {e}. Falling back...")
            continue
    raise AllProvidersFailed(
        f"All LLM providers failed (attempted={tried}). Last error: {last_err}"
    )
