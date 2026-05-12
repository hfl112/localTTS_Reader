import re

class TextProcessor:
    def __init__(self, max_length=160):
        self.max_length = max_length

    def strip_markdown(self, text):
        text = re.sub(r'^---[\s\S]*?---\n', '', text)
        text = re.sub(r'!\[\[.*?\]\]', '', text)
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
        text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
        text = re.sub(r'^\s*#+\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*|__|\*|_', '', text)
        text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[\*\-\+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'https?://[^\s]+', '', text)
        return text

    def clean_text(self, text):
        text = self.strip_markdown(text)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        def protect_match(m):
            return m.group(0).replace(' ', '____SPACE____')
        
        text = re.sub(r'[a-zA-Z0-9,.\'\-!]+(?:\s+[a-zA-Z0-9,.\'\-!]+)+', protect_match, text)
        text = re.sub(r'[ \t]+', '', text)
        text = text.replace('____SPACE____', ' ')
        text = text.replace('·', '，')
        
        return text.strip()

    def smart_split(self, text):
        cleaned_text = self.clean_text(text)
        paragraphs = [p.strip() for p in re.split(r'\n+', cleaned_text) if p.strip()]
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
                    
                    break_point = -1
                    for i in range(end_pos, current_pos, -1):
                        if i+1 < len(p) and p[i] in ["。", "！", "？", "!"] and p[i+1] in ["”", "』", "'", "\""]:
                            break_point = i + 2
                            break
                    if break_point == -1:
                        for i in range(end_pos, current_pos, -1):
                            if p[i] in ["。", "！", "？", "!", "?", "；", ";"]:
                                break_point = i + 1
                                break
                    if break_point == -1:
                        for i in range(end_pos, current_pos, -1):
                            if p[i] in ["，", ",", " "]:
                                break_point = i + 1
                                break
                    
                    if break_point == -1:
                        break_point = end_pos
                    
                    chunks.append(p[current_pos:break_point])
                    current_pos = break_point
        
        return [c for c in chunks if c.strip()]
