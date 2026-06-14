import os
import time
import json
import re
import atexit
import sys
import concurrent.futures
import requests
from datetime import datetime
from google import genai
from google.genai import types
from dotenv import load_dotenv
from typing import List, Tuple, Optional, Any

# --- Exceptions ---
class TokenLimitError(Exception): pass

# --- Paths & Key Loading ---
SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))

# 优先从 Obsidian Vault .env 以及同级目录的 .env 加载
vault_dotenv: str = "/Users/funanhe/Obsidian/DailyInsight/.env"
if os.path.exists(vault_dotenv):
    load_dotenv(vault_dotenv)
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

# 清理干扰以防 Key 混淆
os.environ.pop('GOOGLE_API_KEY', None)
os.environ.pop('GEMINI_API_KEY', None)

def get_api_keys() -> List[str]:
    keys: List[str] = []
    # 优先使用 .env 中读取到的多 Key 支持
    for i in range(1, 20):
        key_name: str = "GEMINI_API_KEY" if i == 1 else f"GEMINI_API_KEY_{i}"
        val: Optional[str] = os.getenv(key_name)
        if val and val not in keys: 
            keys.append(val)
    return keys

def network_audit() -> None:
    try:
        t1: float = time.time()
        requests.get("https://generativelanguage.googleapis.com", timeout=10)
        print(f"[Gemini] Probe Latency: {(time.time()-t1)*1000:.0f}ms")
    except:
        print("[Gemini] Probe Failed.")

API_KEYS: List[str] = get_api_keys()
if API_KEYS:
    network_audit()
else:
    print("[Gemini] Warning: No GEMINI_API_KEY found in env!")

MODEL_ROUTING: dict[str, List[str]] = {
    "lite": ["gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview"], 
    "standard": ["gemini-2.5-flash", "gemini-3-flash-preview", "gemini-flash-latest"],
    "pro": ["gemini-3.1-pro-preview"]
}
LEVEL_ORDER: List[str] = ["pro", "standard", "lite"]

GLOBAL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=50)

class UsageSupervisor:
    def __init__(self, log_path: str) -> None:
        self.log_path: str = log_path
        self.session_data: List[dict] = []
        
    def record(self, step_name: str, model: str, p: int, c: int) -> None:
        entry: dict = {
            "ts": time.time(), 
            "dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
            "step": step_name, 
            "m": model, 
            "p": p, 
            "c": c, 
            "t": p + c
        }
        self.session_data.append(entry)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f: 
                f.write(json.dumps(entry) + "\n")
        except: 
            pass
            
    def print_receipt(self) -> None:
        if not self.session_data: 
            return
        print(f"\n📊 [Gemini Done] Total: {sum(e['t'] for e in self.session_data)} tokens.")

DATA_DIR: str = os.path.join(SCRIPT_DIR, "data")
if not os.path.exists(DATA_DIR): 
    os.makedirs(DATA_DIR)
    
PROMPT_LOG: str = os.path.join(DATA_DIR, "gemini_prompt.log")
supervisor = UsageSupervisor(os.path.join(DATA_DIR, "usage_stats.jsonl"))
atexit.register(supervisor.print_receipt)

class GeminiPool:
    def __init__(self, keys: List[str]) -> None:
        self.keys: List[str] = keys
        self.current_key_index: int = 0
        
    def get_key(self) -> Tuple[Optional[str], int]:
        if not self.keys: 
            return None, -1
        idx: int = self.current_key_index
        key: str = self.keys[idx]
        self.current_key_index = (self.current_key_index + 1) % len(self.keys)
        return key, idx

pool = GeminiPool(API_KEYS)

def _api_call_worker(key: str, model_name: str, prompt: str, response_mime_type: Optional[str] = None) -> Any:
    """原子调用：解决 400 错误"""
    try:
        client = genai.Client(api_key=key)
        config_args: dict = {}
        if response_mime_type:
            config_args["response_mime_type"] = response_mime_type
            
        res = client.models.generate_content(
            model=model_name, 
            contents=prompt,
            config=types.GenerateContentConfig(**config_args) if config_args else None
        )
        return res
    except Exception as e:
        return e

def call_gemini(prompt: str, task_level: str = "standard", step_name: str = "Default", response_mime_type: Optional[str] = None) -> str:
    # 1. 确定降级序列
    if task_level in LEVEL_ORDER:
        idx: int = LEVEL_ORDER.index(task_level)
        levels: List[str] = LEVEL_ORDER[idx:]
    elif task_level in MODEL_ROUTING:
        levels = [task_level]
    else:
        levels = LEVEL_ORDER[1:] # 默认从 standard 开始

    # 2. 按 Level 降级循环
    for current_level in levels:
        models: List[str] = MODEL_ROUTING.get(current_level, [])
        if not models: 
            continue
        
        print(f"📡 [Gemini Tier] Switching to {current_level}...")
        
        # 3. 轮询所有 Key (外层)
        for _ in range(len(API_KEYS)):
            key, key_idx = pool.get_key()
            if not key:
                raise Exception("No available API Keys.")
            
            # 4. 在当前 Key 上尝试该 Level 的所有模型 (内层)
            for model_name in models:
                print(f"  [Try] {model_name} | Slot #{key_idx}", end=" ", flush=True)
                
                future = GLOBAL_EXECUTOR.submit(_api_call_worker, key, model_name, prompt, response_mime_type)
                try:
                    res = future.result(timeout=100)
                    
                    if isinstance(res, Exception):
                        err_detail: str = str(res)
                        print(f"-> Error: {type(res).__name__} ({err_detail[:50]}...)")
                        continue
                    
                    if hasattr(res, "text"):
                        print("-> ✔ Success!")
                        try:
                            with open(PROMPT_LOG, "a", encoding="utf-8") as f:
                                f.write(f"TIME: {datetime.now()}\nMODEL: {model_name}\nPROMPT:\n{prompt}\n\n" + "="*40 + "\n")
                        except: 
                            pass
                        supervisor.record(step_name, model_name, res.usage_metadata.prompt_token_count, res.usage_metadata.candidates_token_count)
                        return res.text
                except concurrent.futures.TimeoutError:
                    print("-> Timeout (100s)")
                    continue
                except Exception as e:
                    print(f"-> Error: {type(e).__name__}")
                    continue
                    
    raise Exception("❌ All attempts exhausted across all keys and levels.")

def translate_to_chinese(text: str) -> str:
    """
    使用 Gemini 模型将输入的 Markdown 或文本内容翻译成通俗易懂、流畅自然的中文，
    保留 Markdown 原有格式，并优化口语化表达。
    """
    prompt: str = (
        "你是一个专业的技术和学术翻译专家。请将以下 Markdown/文本内容翻译成通俗易懂、流畅自然的中文。\n"
        "要求：\n"
        "1. 保留原本的 Markdown 格式，例如标题（#）、链接、图片、加粗、代码块等。\n"
        "2. 翻译要符合中文表达习惯，术语要准确，通俗易懂。\n"
        "3. 如果是 YouTube 视频字幕，请将口语化的表达转化为书面、连贯的中文段落。\n"
        "4. 仅返回翻译后的中文内容，不要包含任何多余的解释、前言或 Markdown 标记符（如 ```markdown）。\n\n"
        f"待翻译内容：\n{text}"
    )
    return call_gemini(prompt, task_level="standard", step_name="Translate")

if __name__ == "__main__":
    if not API_KEYS:
        print("Test Aborted: No API keys configured.")
        sys.exit(1)
    try:
        print(call_gemini("Hello Gemini, reply in one word 'OK' if you hear me.", task_level="standard"))
    except Exception as e: 
        print(f"\nFATAL: {e}")
