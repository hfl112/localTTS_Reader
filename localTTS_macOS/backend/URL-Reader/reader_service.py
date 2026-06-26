import hashlib
import os
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Callable

from youtube_transcript_api import YouTubeTranscriptApi


StageCallback = Callable[[str, dict], None]


@dataclass
class UrlReaderResult:
    text: str
    title: str
    source: str
    voice: str | None
    mode: str
    from_cache: bool = False


def noop_stage(stage: str, fields: dict) -> None:
    return None


def cache_key(*parts: str) -> str:
    h = hashlib.md5()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def extract_youtube_video_id(url: str) -> str | None:
    pattern = r"(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})"
    match = re.search(pattern, url)
    return match.group(1) if match else None


def get_youtube_transcript(video_id: str) -> str:
    api = YouTubeTranscriptApi()
    transcript_list = api.fetch(
        video_id,
        languages=["zh", "zh-CN", "zh-TW", "zh-Hans", "zh-Hant", "en"],
    )
    return " ".join([item.text for item in transcript_list])


def cleanup_temp_file(path: str) -> None:
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


def fetch_html_with_proxy_fallback(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
    }
    direct_err_msg = ""
    html_content = ""

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html_content = response.read().decode("utf-8")
    except Exception as e:
        direct_err_msg = str(e)

    if not html_content or len(html_content) < 3000:
        for proxy in [
            "http://127.0.0.1:7890",
            "http://127.0.0.1:1087",
            "http://127.0.0.1:10809",
            "http://127.0.0.1:1080",
        ]:
            try:
                proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                opener = urllib.request.build_opener(proxy_handler)
                req = urllib.request.Request(url, headers=headers)
                with opener.open(req, timeout=8) as response:
                    temp_html = response.read().decode("utf-8")
                    if len(temp_html) >= 3000:
                        return temp_html
            except Exception:
                continue

    if html_content:
        return html_content

    raise RuntimeError(f"网络及代理均无法提取网页内容。最初错误: {direct_err_msg}")


def defuddle_html(html: str) -> str:
    temp_path = os.path.join(tempfile.gettempdir(), f"defuddle_{os.getpid()}.html")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(html)
        return defuddle_file(temp_path)
    finally:
        cleanup_temp_file(temp_path)


def defuddle_file(html_file_path: str) -> str:
    try:
        result = subprocess.run(
            ["defuddle", "parse", html_file_path, "--md"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except Exception as e:
        raise RuntimeError(f"调用 defuddle 失败: {e}") from e


def extract_title(text: str) -> str:
    non_title_headings = {
        "references",
        "bibliography",
        "works cited",
        "参考文献",
        "参考资料",
        "参考书目",
        "引用文献",
    }
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            if title.lower() not in non_title_headings:
                return title
        if line.startswith("## "):
            title = line[3:].strip()
            if title.lower() not in non_title_headings:
                return title
    return ""


def clean_markdown_content(text: str) -> str:
    """Remove web extraction noise before Gemini/TTS sees the article body."""
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("&nbsp;", " ").replace("&amp;", "&")

    # Drop embedded widgets and raw HTML blocks that Defuddle may keep.
    text = re.sub(
        r"<(?:iframe|script|style|noscript)\b[\s\S]*?</(?:iframe|script|style|noscript)>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<iframe\b[\s\S]*?>", "", text, flags=re.IGNORECASE)

    # Cut bibliography/reference tails in both English and Chinese. Match exact headings only.
    reference_heading = re.compile(
        r"^\s*(?:#{1,6}\s*|\*\*\s*)?"
        r"(?:References|Bibliography|Works\s+Cited|参考文献|参考资料|参考书目|引用文献)"
        r"(?:\s*\*\*)?\s*[:：]?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = reference_heading.search(text)
    if match:
        text = text[: match.start()]

    # Remove footnote/link definitions and citation markers.
    text = re.sub(
        r"^\s*\[\^[^\]]+\]:\s+.*(?:\n(?:[ \t]+|\t).*)*",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^\s*\[[^\]]+\]:\s+https?://\S+.*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"\[\^\d+\]|\[\^[^\]]+\]", "", text)
    text = re.sub(r"\[\d+(?:\s*[-,]\s*\d+)*\]", "", text)

    # Keep visible link text, drop URL payloads and standalone URLs.
    text = re.sub(r"!\[[\s\S]*?\]\([\s\S]*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(\s*https?://[^\)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(\s*#[^\)]*\)", r"\1", text)
    text = re.sub(r"[\(（]\s*https?://[^\s\)）]+[\)）]", "", text)
    text = re.sub(r"https?://\S+", "", text)

    # Drop remaining raw HTML tags, but keep their text content.
    text = re.sub(r"</?[^>\n]+>", "", text)

    # Normalize noisy blank lines and trailing spaces without flattening paragraphs.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def title_for_mode(mode: str, title: str) -> str:
    if mode == "translate":
        try:
            from translation_engine import lang_name
            from engine_config import load_engines
            tl = (load_engines().get("translate", {}) or {}).get("target_lang", "zh")
            prefix = f"[译·{lang_name(tl)}]"
        except Exception:
            prefix = "[翻译]"
    else:
        prefix = {
            "podcast-discuss": "[双人总结]",
            "podcast-trans": "[双人翻译]",
        }.get(mode, "")
    return f"{prefix}{title}" if title else ""


def read_cache(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def write_cache(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# Podcast prompts, dispatched through the provider-agnostic LLM engine.
_PODCAST_DISCUSS_PROMPT = (
    "你是一个播客脚本创作专家。请将以下输入的文章或网页内容，改编为一个类似 NotebookLM 风格的双人对谈播客脚本。\n"
    "要求：\n"
    "1. 播客有两个主持人：[Serena] (女性，语气温和、好奇、擅长引导和总结) 和 [Ryan] (男性，语气幽默、博学、擅长解释专业概念和给出例证)。\n"
    "2. 他们需要交替对话，以通俗易懂、口语化、生动有趣的方式讨论和解释输入文章的核心内容，让听众像听故事一样理解这篇文章。\n"
    "3. 输出格式必须严格遵循以下格式（中英文冒号均可，但每行必须以 [Serena]: 或 [Ryan]: 开头，且只有这两个人，绝不能包含其他说话人）：\n"
    "   [Serena]: [说话内容]\n"
    "   [Ryan]: [说话内容]\n"
    "4. 对话内容应使用全中文进行（但技术术语和专有名词可保留英文，以便自然朗读）。\n"
    "5. 对话轮数建议在 8 到 15 轮之间，使内容充实。每一轮说话要口语化，不要过长（单次说话在 50~150 字以内为宜）。\n"
    "6. 仅输出最终的对话内容，绝对不能包含任何 ```markdown、多余的前言、后记或解释性段落。\n\n"
    "待对谈解释的原文内容如下：\n"
)
_PODCAST_TRANS_PROMPT = (
    "你是一个播客翻译和编辑专家。请将以下输入的内容，以双人对谈翻译（一问一答）的形式翻译并改编为中文播客脚本。\n"
    "要求：\n"
    "1. 播客有两个角色：[Serena] (负责用中文进行提问、引出段落主题或承上启下) 和 [Ryan] (负责对原文的具体内容进行直译、解释与解答)。\n"
    "2. 你需要梳理文章脉络，如果是访谈记录，则直接对应翻译；如果是单人文章，请将其解构为 [Serena] 提问/引导、[Ryan] 翻译/具体阐述的交替对话形式。\n"
    "3. 输出格式必须严格遵循以下格式（中英文冒号均可，但每行必须以 [Serena]: 或 [Ryan]: 开头）：\n"
    "   [Serena]: [用中文提问或引导]\n"
    "   [Ryan]: [对应的中文翻译与具体解释内容]\n"
    "4. 所有的对话和回答均使用中文进行。\n"
    "5. 仅输出最终的对话内容，绝对不能包含任何 ```markdown、多余的前言、后记或解释性段落。\n\n"
    "待翻译并解构的原文内容如下：\n"
)


def process_with_llm(text: str, mode: str) -> str:
    """Dispatch a reader mode through the provider-agnostic engines.

    translate -> machine-translation engine (with optional LLM fallback)
    podcast-discuss / podcast-trans -> creative LLM engine
    """
    if mode == "translate":
        from translation_engine import translate_text
        # target=None → 取配置里的 target_lang（默认 zh）
        return translate_text(text)
    if mode in ("podcast-trans", "podcast-discuss"):
        from llm_engine import call_llm
        if mode == "podcast-trans":
            prompt = _PODCAST_TRANS_PROMPT + text
            step = "PodcastTranslation"
        else:
            prompt = _PODCAST_DISCUSS_PROMPT + text
            step = "PodcastDiscussion"
        return call_llm(prompt, tier="standard", step_name=step)
    return text


def process_url_job(
    *,
    url: str,
    html: str = "",
    mode: str = "podcast-discuss",
    base_dir: str | None = None,
    cache_dir: str | None = None,
    stage_callback: StageCallback | None = None,
) -> UrlReaderResult:
    callback = stage_callback or noop_stage
    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
    cache_dir = cache_dir or os.path.join(base_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    video_id = extract_youtube_video_id(url)
    is_youtube = video_id is not None
    source_type = "video" if is_youtube else "web"

    source_key = cache_key(url, html[:2000])
    source_cache_path = os.path.join(cache_dir, f"source_{source_key}.md")
    markdown_content = read_cache(source_cache_path)
    from_cache = markdown_content is not None

    if markdown_content is None:
        callback("fetching", {"source": source_type, "has_html": bool(html)})
        if html.strip():
            callback("parsing", {"method": "uploaded_html"})
            markdown_content = defuddle_html(html)
        elif is_youtube and video_id:
            callback("fetching", {"method": "youtube_transcript"})
            markdown_content = get_youtube_transcript(video_id)
        else:
            callback("fetching", {"method": "network"})
            fetched_html = fetch_html_with_proxy_fallback(url)
            callback("parsing", {"method": "network_html"})
            markdown_content = defuddle_html(fetched_html)
        markdown_content = clean_markdown_content(markdown_content)
        write_cache(source_cache_path, markdown_content)
    else:
        markdown_content = clean_markdown_content(markdown_content)

    if not markdown_content.strip():
        raise RuntimeError("抓取到的内容为空")

    temp_source_path = os.path.join(base_dir, "temp_source.md")
    write_cache(temp_source_path, markdown_content)

    processed_content = markdown_content
    if mode != "original":
        processed_key = cache_key(mode, markdown_content)
        processed_cache_path = os.path.join(cache_dir, f"{mode}_{processed_key}.md")
        cached_processed = read_cache(processed_cache_path)
        if cached_processed is not None:
            processed_content = clean_markdown_content(cached_processed)
            from_cache = True
        else:
            callback("gemini", {"mode": mode})
            processed_content = process_with_llm(markdown_content, mode)
            processed_content = clean_markdown_content(processed_content)
            write_cache(processed_cache_path, processed_content)

        temp_translated_path = os.path.join(base_dir, "temp_translated.md")
        write_cache(temp_translated_path, processed_content)

    raw_title = extract_title(processed_content)
    full_title = title_for_mode(mode, raw_title)
    voice = None
    if is_youtube and mode not in ("podcast-trans", "podcast-discuss"):
        voice = "Ryan"

    callback(
        "processed",
        {
            "source": source_type,
            "title": full_title,
            "text_chars": len(processed_content),
            "from_cache": from_cache,
        },
    )
    return UrlReaderResult(
        text=processed_content,
        title=full_title,
        source=source_type,
        voice=voice,
        mode=mode,
        from_cache=from_cache,
    )
