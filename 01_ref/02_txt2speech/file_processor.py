import os
import soundfile as sf
from qwen_tts_engine import QwenTTSEngine

class FileToSpeech:
    """
    文件转语音工具类
    支持 .txt, .md 格式 (EPUB 待扩展)
    """
    def __init__(self, engine=None):
        self.engine = engine if engine else QwenTTSEngine()
        self.output_dir = "book_outputs"
        os.makedirs(self.output_dir, exist_ok=True)

    def process_txt(self, file_path, mode="story", chunk_size=1000):
        """
        处理 TXT 文件
        :param file_path: 文件路径
        :param mode: "story" (Serena) 或 "news" (Vivian)
        :param chunk_size: 每多少字切分一个音频文件 (防止单个 wav 太大)
        """
        if not os.path.exists(file_path):
            print(f"[Error] 文件不存在: {file_path}")
            return

        print(f"[Processor] 正在读取文件: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 简单的预处理：去除多余空行
        content = re.sub(r'\n+', '\n', content)
        
        # 按字数进行大块切分，用于保存不同的文件
        # 注意：engine 内部还有一层针对模型稳定性的智能段落切分
        book_chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        book_folder = os.path.join(self.output_dir, base_name)
        os.makedirs(book_folder, exist_ok=True)

        print(f"[Processor] 全书共 {len(content)} 字，将分为 {len(book_chunks)} 个部分生成。")

        for i, chunk_text in enumerate(book_chunks):
            file_name = f"{base_name}_part_{i+1:03d}.wav"
            target_path = os.path.join(book_folder, file_name)
            
            if os.path.exists(target_path):
                print(f"  -> 跳过已存在的部分: {file_name}")
                continue

            print(f"  -> 正在生成第 {i+1}/{len(book_chunks)} 部分...")
            try:
                wav, sr = self.engine.generate(chunk_text, mode=mode)
                sf.write(target_path, wav, sr)
                print(f"     已保存: {target_path}")
            except Exception as e:
                print(f"     [失败] 第 {i+1} 部分出错: {e}")

import re

# ================= 运行示例 =================
if __name__ == "__main__":
    processor = FileToSpeech()
    
    # 测试处理你上传的 txt 文件
    book_path = "/Users/funanhe/Documents/0.MyCode/TTS/02_txt2speech/玉中遇-祈破.txt"
    
    # 既然是小说，我们用 Serena (story 模式)
    processor.process_txt(book_path, mode="story", chunk_size=500)
