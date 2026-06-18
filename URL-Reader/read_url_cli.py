import os
import sys
from typing import Any

import requests

from reader_service import process_url_job


def send_to_qwentts(text: str, voice: str | None = None, source: str = "web") -> None:
    url = "http://127.0.0.1:8001/read"
    print("[CLI] 正在将净化后的文本投喂给 QwenTTS-App 进行播放...")

    payload: dict[str, Any] = {"text": text, "source": source}
    if voice:
        payload["voice"] = voice

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[Success] 投喂成功！QwenTTS-App 已经开始朗读。")
        else:
            print(f"[Error] 接口返回错误: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[Error] 无法连接到 QwenTTS-App 服务: {e}")


def save_to_qwentts(
    text: str,
    source: str = "web",
    do_save: bool = True,
    do_podcast: bool = False,
    voice: str | None = None,
    title: str | None = None,
) -> None:
    if do_save:
        url = "http://127.0.0.1:8001/save_for_later"
        print("[CLI] 正在将净化后的文本保存到 QwenTTS-App 稍后朗读列表中...")
        payload: dict[str, Any] = {"text": text, "source": source}
        if voice:
            payload["voice"] = voice
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
        podcast_url = "http://127.0.0.1:8001/generate_single_podcast"
        payload: dict[str, Any] = {"text": text, "source": source}
        if voice:
            payload["voice"] = voice
        if title:
            payload["title"] = title
        try:
            res = requests.post(podcast_url, json=payload, timeout=10)
            if res.status_code == 200:
                print("[Success] 后台播客生成任务已启动。")
            else:
                print(f"[Error] 接口返回错误: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"[Error] 无法连接播客生成接口: {e}")


def parse_args(args: list[str]) -> tuple[str, str, bool, bool, str | None]:
    mode = "podcast-discuss"
    save_flag = False
    podcast_flag = False
    url_args: list[str] = []
    html_file_path: str | None = None

    skip = False
    for i, arg in enumerate(args):
        if skip:
            skip = False
            continue
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

    if not url_args:
        print("用法: python read_url_cli.py [URL] [模式参数] [--save / -s] [--podcast / -p] [--html-file PATH]")
        print("模式参数:")
        print("  --original / -o           - 原始正文")
        print("  --translate / -t          - 翻译正文")
        print("  --podcast-trans / -pt     - 双人-翻译")
        print("  --podcast-discuss / -pd   - 双人-总结 (默认)")
        sys.exit(1)

    return url_args[0], mode, save_flag, podcast_flag, html_file_path


def main() -> None:
    target_url, mode, save_flag, podcast_flag, html_file_path = parse_args(sys.argv[1:])

    html = ""
    if html_file_path and os.path.exists(html_file_path):
        print(f"[CLI] 正在从上传的浏览器 HTML 页面提取正文... ({html_file_path})")
        with open(html_file_path, "r", encoding="utf-8") as f:
            html = f.read()

    def print_stage(stage: str, fields: dict[str, Any]) -> None:
        print(f"[CLI] {stage}: {fields}")

    try:
        result = process_url_job(
            url=target_url,
            html=html,
            mode=mode,
            stage_callback=print_stage,
        )
    except Exception as e:
        print(f"[Error] URL 处理失败: {e}")
        sys.exit(1)

    print("\n--- [ 投喂的正文预览 ] ---")
    print(result.text[:400] + "\n...")
    print("----------------------------\n")

    if save_flag or podcast_flag:
        save_to_qwentts(
            result.text,
            source=result.source,
            do_save=save_flag,
            do_podcast=podcast_flag,
            voice=result.voice,
            title=result.title,
        )
    else:
        send_to_qwentts(result.text, voice=result.voice, source=result.source)


if __name__ == "__main__":
    main()
