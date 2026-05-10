import re

class TextProcessor:
    def __init__(self, max_length=100):
        self.max_length = max_length
        self.punctuations = ["。", "！", "？", "；", "!", "?", ";", "."]

    def clean_text(self, text):
        """
        激进清洗：删除段落内部所有空格和制表符
        """
        # 1. 统一换行符
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # 2. 按行切分，对每一行进行空格剔除
        lines = text.split('\n')
        cleaned_lines = [re.sub(r'[ \t]+', '', line).strip() for line in lines]
        return "\n".join([l for l in cleaned_lines if l])

    def smart_split(self, text):
        """
        智能切片：按换行和标点符号切分（约100字）
        """
        cleaned_text = self.clean_text(text)
        paragraphs = cleaned_text.split('\n')
        chunks = []

        for p in paragraphs:
            if len(p) <= self.max_length:
                chunks.append(p)
            else:
                current_pos = 0
                while current_pos < len(p):
                    end_pos = current_pos + self.max_length
                    if end_pos >= len(p):
                        chunks.append(p[current_pos:])
                        break
                    
                    # 回溯寻找断句标点
                    break_point = -1
                    for i in range(end_pos, current_pos, -1):
                        if p[i] in self.punctuations:
                            break_point = i + 1
                            break
                    
                    if break_point == -1:
                        break_point = end_pos # 硬切
                    
                    chunks.append(p[current_pos:break_point])
                    current_pos = break_point
        
        return [c for c in chunks if c.strip()]
