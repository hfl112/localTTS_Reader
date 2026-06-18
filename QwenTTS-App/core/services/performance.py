import re


PERFORMANCE_PROFILES = {
    "fast": {
        "chunk_sleep": 0.02,
        "sentence_sleep": 0.5,
        "buffer_high_sec": 30.0,
        "buffer_low_sec": 12.0,
        "podcast_pause_poll_sec": 1.0,
        "model": None,
    },
    "balanced": {
        "chunk_sleep": 0.08,
        "sentence_sleep": 1.5,
        "buffer_high_sec": 20.0,
        "buffer_low_sec": 8.0,
        "podcast_pause_poll_sec": 2.0,
        "model": None,
    },
    "quiet": {
        "chunk_sleep": 0.25,
        "sentence_sleep": 3.0,
        "buffer_high_sec": 10.0,
        "buffer_low_sec": 4.0,
        "podcast_pause_poll_sec": 3.0,
        "model": "Qwen3-TTS-0.6B",
    },
}


def get_performance_profile(name: str | None) -> dict:
    profile_name = name if name in PERFORMANCE_PROFILES else "balanced"
    profile = PERFORMANCE_PROFILES[profile_name].copy()
    profile["name"] = profile_name
    return profile


def estimate_reading_minutes(text: str) -> float:
    zh_chars = len([ch for ch in text if "\u4e00" <= ch <= "\u9fff"])
    en_words = len([w for w in re.split(r"\s+", text) if w.strip()])
    return (zh_chars / 250.0) + (en_words / 150.0)
