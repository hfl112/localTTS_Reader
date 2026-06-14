import os
import soundfile as sf
from file_processor import FileToSpeech

def run_short_test():
    # 1. 初始化处理器
    processor = FileToSpeech()
    
    # 2. 设置测试文件路径
    test_file = "novel_test_50.txt"
    
    print(f"\n--- 开始 50 行文本稳定性测试 (音色: Serena) ---")
    
    # 我们将 chunk_size 设置大一些 (10000)，这样 50 行文本会生成在一个 wav 文件里
    # 方便你完整听完 50 行的连贯性
    processor.process_txt(test_file, mode="story", chunk_size=10000)

if __name__ == "__main__":
    run_short_test()
