"""InferenceEngine — the deep module above the ModelBackend seam.

Per ADR-001 (CONTEXT.md §3): one module owns prompt-kwargs building, audio
normalization, and read-through caching. The model itself is reached only
through a ModelBackend, so all of this is unit-testable with FakeBackend.

Step 2 scope: synthesize_local() — a same-process generator. The cross-process
worker loop, priority queue, and TTSRequest land in Step 3.
"""

import hashlib
import os
import queue as _queue
import time
from typing import Iterator, Optional

import numpy as np

# Frame size used to slice cached audio back out on a cache hit (parity with the
# legacy inference_worker, which replayed cache in ~SR-sized chunks).
_CACHE_REPLAY_FRAME = 16000

_PUNCT_ENDINGS = (".", "。", "!", "！", "?", "？", ";", "；")


def _has_chinese(text: str) -> bool:
    return any("一" <= c <= "鿿" for c in text)


def cache_key(text: str, voice: str, model: str, lang: str) -> str:
    """Composite cache key — fixes the legacy bug where the key hashed *text
    only*, so the same sentence in a different voice/model/lang collided and
    replayed the wrong audio. (CONTEXT.md §3, decision #4.)"""
    raw = f"{model}\x1f{voice}\x1f{lang}\x1f{text}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def normalize_frame(mono: np.ndarray) -> np.ndarray:
    """Mono raw frame -> normalized stereo float32, [N, 2] in [-0.98, 0.98].

    Lifted from tts_engine.generate_stream: stereo broadcast, robust 99.5th-
    percentile gain (caps clicks), 6x ceiling, hard clip.
    """
    mono = np.asarray(mono, dtype=np.float32).reshape(-1)
    stereo = np.stack([mono, mono], axis=1)  # [N, 2]
    abs_samples = np.abs(stereo)
    if abs_samples.size > 0:
        robust_peak = np.percentile(abs_samples, 99.5)
        if robust_peak > 0.002:
            gain = min(0.85 / robust_peak, 6.0)
            stereo = stereo * gain
    return np.clip(stereo, -0.98, 0.98).astype(np.float32)


def trim_silence(audio: np.ndarray, sr: int = 24000, pad_ms: int = 20) -> np.ndarray:
    """Trim leading/trailing near-silence from one synthesized chunk (mono or
    stereo). Each chunk carries model-generated head/tail silence (amplified by
    the trailing ``"。  "`` text padding in build_generate_kwargs); leaving it in
    makes both podcasts (concatenated) and live reads (sequential) audibly choppy
    at every sentence boundary (Bug 1). A short pad keeps word onsets/tails intact.
    Shared by the podcast assembler and the read lane so there's one trim, not two."""
    a = np.asarray(audio)
    if a.size == 0:
        return a
    env = np.abs(a).max(axis=1) if a.ndim == 2 else np.abs(a)
    peak = float(env.max())
    if peak <= 0:
        return a[:0]
    thresh = max(0.02 * peak, 0.004)
    loud = np.where(env > thresh)[0]
    if loud.size == 0:
        return a[:0]
    pad = int(sr * pad_ms / 1000)
    start = max(0, int(loud[0]) - pad)
    end = min(len(a), int(loud[-1]) + 1 + pad)
    return a[start:end]


def build_generate_kwargs(text: str, config: dict, reference_base: Optional[str]):
    """Faithful port of the prompt-kwargs construction from tts_engine: text
    padding, dynamic max_tokens, per-chunk language autodetect, base sampling
    params, global ICL voice-locking injection, and cross-language redirect.

    Returns (text_to_generate, generate_kwargs, resolved_lang).
    """
    text_to_generate = text.strip()
    if not any(text_to_generate.endswith(p) for p in _PUNCT_ENDINGS):
        text_to_generate += "。"
    text_to_generate += "  "

    dynamic_max_tokens = max(2048, len(text_to_generate) * 20)
    dynamic_max_tokens = min(dynamic_max_tokens, 8192)

    current_lang_code = config.get("lang_code", "zh")
    has_zh = _has_chinese(text_to_generate)
    if current_lang_code == "zh" and not has_zh:
        current_lang_code = "en"
    elif current_lang_code == "en" and has_zh:
        current_lang_code = "zh"

    generate_kwargs = {
        "voice": config.get("voice", "Serena"),
        "instruct": config.get("instruct", "Professional female anchor, steady and clear."),
        "temperature": config.get("temperature", 0.2),
        "top_p": config.get("top_p", 0.5),
        "top_k": config.get("top_k", 10),
        "repetition_penalty": config.get("repetition_penalty", 1.1),
        "lang_code": current_lang_code,
        "stream": True,
        "streaming_interval": 0.5,
        "response_format": "pcm",
        "max_tokens": dynamic_max_tokens,
    }
    if "ref_audio" in config:
        generate_kwargs["ref_audio"] = config["ref_audio"]
    if "ref_text" in config:
        generate_kwargs["ref_text"] = config["ref_text"]

    if reference_base:
        _inject_icl(generate_kwargs, reference_base)
        _redirect_cross_language_icl(generate_kwargs, reference_base)

    return text_to_generate, generate_kwargs, current_lang_code


def _inject_icl(generate_kwargs: dict, base_ref_path: str) -> None:
    """Global ICL voice-locking to prevent zero-shot voice drift (verbatim
    branch logic from tts_engine)."""
    if "ref_audio" in generate_kwargs:
        return
    voice = generate_kwargs.get("voice", "Serena")
    lang = generate_kwargs.get("lang_code", "zh")

    serena_zh_audio = f"{base_ref_path}/ref_serena_zh.wav"
    serena_en_audio = f"{base_ref_path}/bbc_news.wav"
    ryan_zh_audio = f"{base_ref_path}/ref_ryan.wav"
    if voice == "Serena":
        if lang == "zh" and os.path.exists(serena_zh_audio):
            generate_kwargs["ref_audio"] = serena_zh_audio
            generate_kwargs["ref_text"] = "欢迎收听本期播客，我是女主持塞蕾娜。"
        elif lang == "en" and os.path.exists(serena_en_audio):
            generate_kwargs["ref_audio"] = serena_en_audio
            generate_kwargs["ref_text"] = (
                "This is the research headquarters for one of the oldest companies in tech, IBM."
            )
    elif voice == "Ryan":
        if lang == "zh" and os.path.exists(ryan_zh_audio):
            generate_kwargs["ref_audio"] = ryan_zh_audio
            generate_kwargs["ref_text"] = "各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。"


def _redirect_cross_language_icl(generate_kwargs: dict, base_ref_path: str) -> None:
    """If the ICL prompt text and the target generation language disagree,
    redirect to a same-language prompt, else fall back to zero-shot to avoid
    autoregressive collapse (verbatim logic from tts_engine)."""
    ref_text = generate_kwargs.get("ref_text", "")
    lang = generate_kwargs.get("lang_code", "zh")
    voice = generate_kwargs.get("voice", "Serena")
    if not (ref_text and lang):
        return
    ref_lang = "zh" if _has_chinese(ref_text) else "en"
    if ref_lang == lang:
        return

    redirected = False
    if voice == "Serena":
        serena_zh_audio = f"{base_ref_path}/ref_serena_zh.wav"
        serena_en_audio = f"{base_ref_path}/bbc_news.wav"
        if lang == "zh" and os.path.exists(serena_zh_audio):
            generate_kwargs["ref_audio"] = serena_zh_audio
            generate_kwargs["ref_text"] = "欢迎收听本期播客，我是女主持塞蕾娜。"
            redirected = True
        elif lang == "en" and os.path.exists(serena_en_audio):
            generate_kwargs["ref_audio"] = serena_en_audio
            generate_kwargs["ref_text"] = (
                "This is the research headquarters for one of the oldest companies in tech, IBM."
            )
            redirected = True
    elif voice == "Ryan":
        ryan_zh_audio = f"{base_ref_path}/ref_ryan.wav"
        if lang == "zh" and os.path.exists(ryan_zh_audio):
            generate_kwargs["ref_audio"] = ryan_zh_audio
            generate_kwargs["ref_text"] = "各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。"
            redirected = True

    if not redirected:
        generate_kwargs.pop("ref_audio", None)
        generate_kwargs.pop("ref_text", None)


class InferenceEngine:
    """Owns synthesis logic above the ModelBackend seam: kwargs, normalization,
    and read-through caching. Step 3 adds the worker loop + priority queue."""

    def __init__(
        self,
        backend,
        cache_dir: str,
        storage=None,
        reference_base: Optional[str] = None,
        max_cache_items: int = 10,
        models_path: Optional[str] = None,
    ):
        self.backend = backend
        self.cache_dir = cache_dir
        self.storage = storage
        self.reference_base = reference_base
        self.max_cache_items = max_cache_items
        self.models_path = models_path
        self.current_model: Optional[str] = None
        os.makedirs(self.cache_dir, exist_ok=True)

    # --- model lifecycle (owned by the engine, per ADR-001 decision #5) ---

    @property
    def is_loaded(self) -> bool:
        return self.backend.is_loaded

    def ensure_model(self, model_name: str) -> None:
        """Load `model_name`, switching the backend if a different model is
        currently resident."""
        if self.current_model == model_name and self.backend.is_loaded:
            return
        if self.models_path and not os.path.isabs(model_name):
            abs_path = os.path.join(self.models_path, model_name)
        else:
            abs_path = model_name
        if self.current_model != model_name:
            self.backend.unload()
            print(f"[InferenceEngine] 模型切换 -> {model_name}")
        self.backend.load(abs_path)
        self.current_model = model_name

    def idle_unload(self) -> None:
        if self.backend.is_loaded:
            print("[InferenceEngine] 空闲自动卸载模型...")
            self.backend.unload()
            self.current_model = None

    @staticmethod
    def _apply_model_hardening(config: dict) -> dict:
        """0.6B base models need a persona anchor prepended to instruct
        (ported from inference_worker)."""
        model = config.get("model", "")
        if "0.6B" in model:
            config = dict(config)
            voice = config.get("voice", "Serena")
            config["instruct"] = f"Persona Anchor: {voice}. " + config.get("instruct", "")
        return config

    def cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.npy")

    def synthesize_local(
        self, text: str, config: dict, use_cache: bool = True
    ) -> Iterator[np.ndarray]:
        """Yield normalized stereo frames for `text`. Read-through cache: a hit
        replays cached audio without touching the backend (no GPU); a miss
        synthesizes, normalizes, and stores. Podcast synth passes
        use_cache=False so it neither pollutes nor evicts the read cache."""
        text_to_generate, generate_kwargs, lang = build_generate_kwargs(
            text, config, self.reference_base
        )
        voice = generate_kwargs.get("voice", "Serena")
        model = config.get("model", "Qwen3-TTS-0.6B")
        key = cache_key(text, voice, model, lang)
        path = self.cache_path(key)

        # 1. Cache hit — replay without the backend.
        if use_cache and os.path.exists(path):
            try:
                cached = np.load(path)
                for s in range(0, len(cached), _CACHE_REPLAY_FRAME):
                    yield cached[s : s + _CACHE_REPLAY_FRAME]
                return
            except Exception as e:
                print(f"[InferenceEngine] Cache replay failed, re-synthesizing: {e}")

        # 2. Cache miss — synthesize through the backend, normalize, store.
        frames = []
        for raw_mono in self.backend.generate(text_to_generate, generate_kwargs):
            frame = normalize_frame(raw_mono)
            frames.append(frame)
            yield frame

        if use_cache and frames:
            self._store(key, path, text, voice, model, frames)

    def _store(self, key, path, text, voice, model, frames) -> None:
        try:
            concat = np.concatenate(frames)
            np.save(path, concat)
            if self.storage is not None:
                duration = len(concat) / 24000.0
                try:
                    self.storage.add_cache_metadata(
                        md5=key,
                        text=text,
                        model=model,
                        voice=voice,
                        duration=duration,
                        file_path=path,
                    )
                except Exception as db_err:
                    print(f"[InferenceEngine] Failed to save cache metadata: {db_err}")
                self.evict_cache()
        except Exception as save_err:
            print(f"[InferenceEngine] Save cache failed: {save_err}")

    def evict_cache(self) -> None:
        """Drop cache entries beyond max_cache_items, DB created_at order
        authoritative (mirrors the legacy manage_cache_limit)."""
        if self.storage is None:
            return
        try:
            rows = self.storage.get_all_cache()  # newest-first
            for row in rows[self.max_cache_items :]:
                fp = row.get("file_path")
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except OSError as e:
                        print(f"[InferenceEngine] Failed to remove {fp}: {e}")
                md5_val = row.get("md5")
                if md5_val:
                    try:
                        self.storage.delete_cache_by_md5(md5_val)
                    except Exception as e:
                        print(f"[InferenceEngine] Failed to delete row {md5_val}: {e}")
        except Exception as e:
            print(f"[InferenceEngine] evict_cache error: {e}")

    # --- the inference process loop (owns both lanes) ---

    def run_loop(self, shared_state, sentinel, profile_fn, idle_unload_sec: float = 600.0) -> None:
        """The single inference process. Drains the read lane (text_q) first so
        reads always preempt podcast work at chunk boundaries (ADR-001 #2), then
        the podcast lane (podcast_q). One model, GPU serialized for free."""
        print(f"[InferenceProcess] 启动成功, PID: {os.getpid()}")
        last_active = time.time()
        metal_warning_reported = False
        while True:
            try:
                try:
                    shared_state.vram_mb.value = self.backend.active_memory_mb()
                    metal_warning_reported = False
                except RuntimeError as error:
                    shared_state.vram_mb.value = 0.0
                    if not metal_warning_reported:
                        print(f"[InferenceProcess] Metal memory query unavailable: {error}")
                        metal_warning_reported = True

                task, is_podcast = self._next_task(shared_state, idle_unload_sec, last_active)
                if task is _IDLE:
                    continue
                last_active = time.time()

                if is_podcast:
                    self._handle_podcast_task(shared_state, task)
                    continue

                # --- read lane: existing text_q/audio_q protocol, preserved ---
                if task is None:
                    break
                if isinstance(task, str) and task == sentinel:
                    shared_state.audio_q.put(sentinel)
                    continue
                if not isinstance(task, dict):
                    continue
                task_id = task.get("task_id", -1)
                chunk_index = task.get("chunk_index", -1)
                if task_id != shared_state.current_task_id.value:
                    continue

                config = self._apply_model_hardening(task["config"])
                self.ensure_model(config.get("model", "Qwen3-TTS-0.6B"))
                profile = profile_fn(config.get("performance_profile"))
                throttle = profile.get("chunk_sleep", 0.0) if isinstance(profile, dict) else 0.0

                # E3: collect the whole sentence-chunk, trim its head/tail silence
                # (same fix as podcasts), then emit in player-sized frames — so
                # live reads aren't choppy at every sentence boundary. Cost: this
                # sentence's first audio waits for its full synthesis.
                def _still_current() -> bool:
                    return not shared_state.stop_event.is_set() and task_id == shared_state.current_task_id.value

                chunk_frames = []
                for frame in self.synthesize_local(task["text"], config):
                    if not _still_current():
                        break
                    chunk_frames.append(frame)
                if chunk_frames and _still_current():
                    trimmed = trim_silence(np.concatenate(chunk_frames))
                    for s in range(0, len(trimmed), _CACHE_REPLAY_FRAME):
                        if not _still_current():
                            break
                        shared_state.audio_q.put((task_id, chunk_index, trimmed[s : s + _CACHE_REPLAY_FRAME]))
                        shared_state.note_audio_frame()
                        if throttle:
                            time.sleep(throttle)
                shared_state.audio_q.put("CHUNK_DONE")
            except Exception as e:
                import traceback

                print(f"[InferenceProcess] 异常: {e}")
                traceback.print_exc()
                try:
                    shared_state.set_error(str(e))
                except Exception:
                    pass
                time.sleep(1.0)

    def _next_task(self, shared_state, idle_unload_sec, last_active):
        """Read-priority task pickup: text_q (reads) before podcast_q, else a
        short blocking wait on reads that doubles as the idle-unload tick.
        Returns (task, is_podcast); task is _IDLE when nothing was available."""
        try:
            return shared_state.text_q.get_nowait(), False
        except _queue.Empty:
            pass
        pq = getattr(shared_state, "podcast_q", None)
        if pq is not None:
            try:
                return pq.get_nowait(), True
            except _queue.Empty:
                pass
        try:
            return shared_state.text_q.get(timeout=2), False
        except _queue.Empty:
            if self.is_loaded and (time.time() - last_active > idle_unload_sec):
                self.idle_unload()
            return _IDLE, False

    def _handle_podcast_task(self, shared_state, task) -> None:
        """Synthesize one podcast chunk fully and write chunk_{idx}.npy (the
        format write_podcast_wav_from_chunks already expects). On failure write
        a sibling `.err` marker so the polling subprocess fails fast instead of
        hanging. use_cache=False keeps the read cache untouched."""
        chunk_file = task.get("chunk_file")
        try:
            config = self._apply_model_hardening(task["config"])
            self.ensure_model(config.get("model", "Qwen3-TTS-0.6B"))
            frames = list(self.synthesize_local(task["text"], config, use_cache=False))
            if chunk_file:
                if not frames:
                    raise RuntimeError("no audio frames produced for podcast chunk")
                # Write to a sibling then atomically rename, so the polling
                # subprocess never observes a half-written chunk file.
                building = chunk_file + ".building.npy"  # ends in .npy → np.save keeps it
                np.save(building, np.concatenate(frames))
                os.replace(building, chunk_file)
        except Exception as e:
            if chunk_file:
                try:
                    with open(chunk_file + ".err", "w", encoding="utf-8") as f:
                        f.write(str(e))
                except Exception:
                    pass


# Sentinel distinguishing "no task this tick" from a real None (shutdown) task.
_IDLE = object()
