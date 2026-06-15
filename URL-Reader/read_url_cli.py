import subprocess
import sys
import os
import tempfile
import urllib.request
import urllib.error
import re
import requests
from youtube_transcript_api import YouTubeTranscriptApi
from typing import Optional, List, Dict, Any

def cleanup_temp_file(path: str) -> None:
    if os.path.exists(path):
        try:
            os.remove(path)
        except:
            pass

def extract_youtube_video_id(url: str) -> Optional[str]:
    """
    使用正则表达式提取 YouTube 视频中的 11 位唯一视频 ID。
    支持格式包括: watch?v=, youtu.be/, /embed/, /v/, /shorts/ 等。
    """
    pattern: str = r'(?:v=|\/embed\/|\/v\/|youtu\.be\/|\/shorts\/)([a-zA-Z0-9_-]{11})'
    match = re.search(pattern, url)
    return match.group(1) if match else None

def get_youtube_transcript(video_id: str) -> str:
    """
    通过 YouTube 官方 API 接口提取视频字幕。
    优先寻找中文/中文简体/中文繁体，如果不存在则退避加载英文。
    """
    print(f"[CLI] 正在请求 YouTube 视频字幕 (Video ID: {video_id}) ...")
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.fetch(
            video_id, 
            languages=['zh', 'zh-CN', 'zh-TW', 'zh-Hans', 'zh-Hant', 'en']
        )
        full_text: str = " ".join([item.text for item in transcript_list])
        return full_text
    except Exception as e:
        raise Exception(f"未能获取该视频的有效字幕 (支持 zh/en): {e}")

def fetch_html_with_proxy_fallback(url: str, headers: Dict[str, str]) -> str:
    """
    尝试直连抓取网页。如果因 WAF 阻拦或 429 报错，
    则自动检测本地 Clash 7890，V2Ray 1087 代理进行请求重试。
    若全部失败，或获取到了 SPA 动态空壳，则从 Google Chrome 当前 activity 标签页直接拉取已渲染的 DOM。
    """
    direct_err_msg: str = ""
    html_content: str = ""
    
    # 1. 尝试直连
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html_content = response.read().decode("utf-8")
    except Exception as e:
        direct_err_msg = str(e)
        print(f"[CLI] 直连抓取受阻 ({e})，正在尝试探测本地代理服务重试...")
        
    # 2. 本地代理检测重试
    if not html_content or len(html_content) < 3000:
        proxies: List[str] = [
            "http://127.0.0.1:7890",
            "http://127.0.0.1:1087",
            "http://127.0.0.1:10809",
            "http://127.0.0.1:1080",
        ]
        
        for proxy in proxies:
            try:
                proxy_handler = urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
                opener = urllib.request.build_opener(proxy_handler)
                req = urllib.request.Request(url, headers=headers)
                with opener.open(req, timeout=8) as response:
                    temp_html = response.read().decode("utf-8")
                    if len(temp_html) >= 3000:
                        html_content = temp_html
                        print(f"[CLI] 成功通过本地代理 ({proxy}) 抓取网页！")
                        break
            except Exception:
                continue

    # 3. 终极 Fallback：Chrome 浏览器已渲染缓存抓取
    is_empty_shell: bool = html_content is not None and len(html_content) < 3000
    if not html_content or is_empty_shell:
        if is_empty_shell:
            print("[CLI] 直连/代理抓取的源码内容过短 (可能是 JS 动态渲染的空壳)，正在尝试从 Chrome 浏览器缓存直接提取...")
        else:
            print("[CLI] 网络抓取彻底失败，正在尝试从 Chrome 浏览器缓存直接提取...")
            
        try:
            chrome_url_cmd: str = "osascript -e 'tell application \"Google Chrome\" to return URL of active tab of front window'"
            chrome_url: str = subprocess.check_output(chrome_url_cmd, shell=True, text=True).strip()
            
            def clean_url(u: str) -> str:
                return u.lower().replace("https://", "").replace("http://", "").rstrip("/")
                
            if clean_url(chrome_url) == clean_url(url):
                print(f"[CLI] 检测到当前 Chrome 浏览器已打开此网页，正在提取浏览器已渲染的 DOM...")
                chrome_html_cmd: str = "osascript -e 'tell application \"Google Chrome\" to return execute active tab of front window javascript \"document.documentElement.outerHTML\"'"
                chrome_html: str = subprocess.check_output(chrome_html_cmd, shell=True, text=True).strip()
                if chrome_html and "<html>" in chrome_html.lower():
                    print("[CLI] 成功提取当前浏览器页面已渲染源码！")
                    return chrome_html
            else:
                print(f"[Warning] 浏览器当前活动标签页与目标 URL 不匹配，跳过浏览器缓存抓取。")
        except Exception:
            print(f"[Warning] 浏览器缓存提取受阻。")
            print("\n💡 [提示] 检测到目标网页为 JS 动态渲染或处于学术登录贴/墙内。")
            print("💡 如果您已在 Google Chrome 浏览器中打开了该网页，请在 Chrome 菜单中勾选：")
            print("👉 [View] (视图) -> [Developer] (开发者) -> [Allow JavaScript from Apple Events] (允许来自 Apple 事件的 JavaScript)")
            print("💡 勾选后，重新运行本命令，脚本将可以直接从您的浏览器里“零网络延迟”提取已登录的论文全文！\n")
            
    if html_content:
        return html_content
        
    raise Exception(f"网络及浏览器缓存均无法提取网页内容。最初错误: {direct_err_msg}")

def run_defuddle(url: str) -> str:
    """
    使用 urllib 配合详细 Headers 抓取 HTML 源码，并喂给 defuddle 进行 Markdown 正文净化。
    """
    headers: Dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1"
    }
    
    html_content: str = fetch_html_with_proxy_fallback(url, headers)
        
    temp_dir: str = tempfile.gettempdir()
    temp_file_path: str = os.path.join(temp_dir, f"defuddle_{os.getpid()}.html")
    
    try:
        with open(temp_file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception as e:
        print(f"[Error] 写入临时文件失败: {e}")
        sys.exit(1)
        
    markdown_result: str = ""
    try:
        result = subprocess.run(
            ["defuddle", "parse", temp_file_path, "--md"],
            capture_output=True,
            text=True,
            check=True
        )
        markdown_result = result.stdout
    except Exception as e:
        print(f"[Error] 调用 defuddle 失败: {e}")
    finally:
        cleanup_temp_file(temp_file_path)
        
    return markdown_result

def run_defuddle_with_local_file(html_file_path: str) -> str:
    markdown_result: str = ""
    try:
        result = subprocess.run(
            ["defuddle", "parse", html_file_path, "--md"],
            capture_output=True,
            text=True,
            check=True
        )
        markdown_result = result.stdout
    except Exception as e:
        print(f"[Error] 调用 defuddle 失败: {e}")
    return markdown_result

def send_to_qwentts(text: str, voice: Optional[str] = None, source: str = "web") -> None:
    """
    将文本投喂给本地正在运行的 QwenTTS-App 接口进行朗读。
    """
    url: str = "http://127.0.0.1:8001/read"
    print("[CLI] 正在将净化后的文本投喂给 QwenTTS-App 进行播放...")
    
    payload: Dict[str, Any] = {"text": text, "source": source}
    if voice:
        payload["voice"] = voice
        
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[Success] 投喂成功！QwenTTS-App 已经开始朗读。")
        else:
            print(f"[Error] 接口返回错误: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[Error] 无法连接到 QwenTTS-App 服务 (请确认 python app.py 是否在运行): {e}")

def save_to_qwentts(text: str, source: str = "web", do_save: bool = True, do_podcast: bool = False, voice: Optional[str] = None, title: Optional[str] = None) -> None:
    if do_save:
        url: str = "http://127.0.0.1:8001/save_for_later"
        print(f"[CLI] 正在将净化后的文本保存到 QwenTTS-App 稍后朗读列表中...")
        payload: Dict[str, Any] = {"text": text, "source": source}
        if title:
            payload["title"] = title
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print("[Success] 保存成功！已加入“稍后朗读”列表。")
            else:
                print(f"[Error] 接口返回错误: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"[Error] 无法连接到 QwenTTS-App 服务: {e}")
        
    if do_podcast:
        podcast_url: str = "http://127.0.0.1:8001/generate_single_podcast"
        payload = {"text": text, "source": source}
        if voice: payload["voice"] = voice
        if title:
            payload["title"] = title
        try:
            res = requests.post(podcast_url, json=payload, timeout=10)
            if res.status_code == 200:
                print("[Success] 后台播客生成任务已启动。")
        except Exception as e:
            print(f"[Error] 无法连接播客生成接口: {e}")

def main() -> None:
    # 支持命令行参数中夹带模式及动作：--translate/-t, --podcast-trans/-pt, --podcast-discuss/-pd, --save/-s, --podcast/-p
    mode: str = "podcast-discuss"
    save_flag: bool = False
    podcast_flag: bool = False
    url_args: List[str] = []
    html_file_path: Optional[str] = None
    
    args = sys.argv[1:]
    skip = False
    for i in range(len(args)):
        if skip:
            skip = False
            continue
        arg = args[i]
        if arg in ("--translate", "-t"):
            mode = "translate"
        elif arg in ("--podcast-trans", "-pt"):
            mode = "podcast-trans"
        elif arg in ("--podcast-discuss", "-pd"):
            mode = "podcast-discuss"
        elif arg in ("--original", "-o"):
            mode = "original"
        elif arg in ("--save", "-s"):
            save_flag = True
        elif arg in ("--podcast", "-p"):
            podcast_flag = True
        elif arg == "--html-file" and i + 1 < len(args):
            html_file_path = args[i + 1]
            skip = True
        else:
            url_args.append(arg)
            
    if len(url_args) < 1:
        print("用法: python read_url_cli.py [URL] [模式参数] [--save / -s] [--podcast / -p] [--html-file PATH]")
        print("模式参数:")
        print("  --original / -o           - 原始正文 (不作Gemini转换)")
        print("  --translate / -t          - 翻译正文")
        print("  --podcast-trans / -pt     - 双人-翻译")
        print("  --podcast-discuss / -pd   - 双人-总结 (默认)")
        sys.exit(1)
        
    target_url: str = url_args[0]
    
    # 自动识别并拦截 YouTube 视频链接提取字幕
    video_id: Optional[str] = extract_youtube_video_id(target_url)
    is_youtube: bool = video_id is not None
    
    markdown_content = ""
    if html_file_path and os.path.exists(html_file_path):
        print(f"[CLI] 正在从上传的浏览器 HTML 页面提取正文... ({html_file_path})")
        try:
            markdown_content = run_defuddle_with_local_file(html_file_path)
        except Exception as e:
            print(f"[Warning] 本地 HTML 提取失败: {e}")
            
    if not markdown_content:
        if is_youtube and video_id:
            try:
                markdown_content = get_youtube_transcript(video_id)
            except Exception as e:
                print(f"[Error] {e}")
                sys.exit(1)
        else:
            # 普通网页正文提取
            markdown_content = run_defuddle(target_url)
    
    if not markdown_content.strip():
        print("[Warning] 抓取到的内容为空！")
        sys.exit(0)
        
    # 保存提取的原始正文临时文件
    base_dir: str = os.path.dirname(os.path.abspath(__file__))
    temp_source_path: str = os.path.join(base_dir, "temp_source.md")
    try:
        with open(temp_source_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        print(f"[CLI] 原始网页内容已保存至临时文件: {temp_source_path}")
    except Exception as e:
        print(f"[Warning] 写入临时源文件失败: {e}")
 
    # 处理各种生成/翻译/对谈模式
    if mode != "original":
        print(f"[CLI] 检测到生成模式 [{mode}]，正在调用 Gemini 处理...")
        try:
            sys.path.append(base_dir)
            from gemini_engine import translate_to_chinese, generate_podcast_discussion, generate_podcast_translation
            
            processed_content: str = ""
            if mode == "translate":
                processed_content = translate_to_chinese(markdown_content)
            elif mode == "podcast-trans":
                processed_content = generate_podcast_translation(markdown_content)
            elif mode == "podcast-discuss":
                processed_content = generate_podcast_discussion(markdown_content)
            else:
                processed_content = markdown_content
                
            # 保存处理后的译文临时文件
            temp_translated_path: str = os.path.join(base_dir, "temp_translated.md")
            with open(temp_translated_path, "w", encoding="utf-8") as f:
                f.write(processed_content)
            print(f"[CLI] 处理后内容已保存至临时文件: {temp_translated_path}")
            
            # 将要朗读的文本替换为处理后的内容
            markdown_content = processed_content
        except Exception as e:
            print(f"[Error] Gemini 处理失败，退避为原始文本朗读: {e}")
    
    print("\n--- [ 投喂的正文预览 ] ---")
    print(markdown_content[:400] + "\n...")
    print("----------------------------\n")
    
    # 提取网页中的原始标题
    extracted_title = ""
    for line in markdown_content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            extracted_title = line[2:].strip()
            break
        elif line.startswith("## "):
            extracted_title = line[3:].strip()
            break
            
    # 根据模式添加标题前缀
    mode_label = ""
    if mode == "podcast-discuss":
        mode_label = "[双人总结]"
    elif mode == "podcast-trans":
        mode_label = "[双人翻译]"
    elif mode == "translate":
        mode_label = "[中文翻译]"
        
    full_title = f"{mode_label}{extracted_title}" if extracted_title else ""
    
    # 确定朗读的 voice
    voice: Optional[str] = None
    # 只有在非播客模式且为 YouTube 时，才默认为单人男声 Ryan
    if is_youtube and mode not in ("podcast-trans", "podcast-discuss"):
        voice = "Ryan"
        
    source_type: str = "video" if is_youtube else "web"
    if save_flag or podcast_flag:
        save_to_qwentts(markdown_content, source=source_type, do_save=save_flag, do_podcast=podcast_flag, voice=voice, title=full_title)
    else:
        # 同时通过命令行朗读时也可以带上标题
        send_to_qwentts(markdown_content, voice=voice, source=source_type)

if __name__ == "__main__":
    main()
