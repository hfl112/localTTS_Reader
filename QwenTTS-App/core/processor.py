import os
import re
from core.paths import runtime_paths

class TextProcessor:
    def __init__(self, max_length: int = 80) -> None:
        self.max_length: int = max_length


    def filter_references(self, text: str) -> str:
        """
        匹配并切除论文最后的 References / 参考文献部分。
        匹配整行的 Markdown 标题或加粗开头的 'References'/'Bibliography'/'参考文献'
        """
        pattern = re.compile(
            r'^(?:#+\s*|\*\*\s*)*(?:References|Bibliography|参考文献|参考书目)(?:\s*[:：\s]*)?$', 
            re.MULTILINE | re.IGNORECASE
        )
        match = pattern.search(text)
        if match:
            return text[:match.start()]
        return text

    def strip_markdown(self, text: str) -> str:
        # 0. 过滤参考文献部分，避免朗读冗长无意义的文献列表
        text = self.filter_references(text)
        
        text = re.sub(r'^---[\s\S]*?---\n', '', text)
        # 1. 清洗学术文献标记，例如 [1], [2, 3], [4-6] 等
        text = re.sub(r'\[\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+)*\]', '', text)
        # 2. 清洗 Unicode 上标角标数字 ¹ ² ³ ⁴ 等
        text = re.sub(r'[\u00b2\u00b3\u00b9\u2070-\u207f]', '', text)
        # 3. 清洗带别名的 WikiLinks: [[Note_Title|display_text]] -> display_text
        text = re.sub(r'\[\[[^\]|]*\|([^\]]+)\]\]', r'\1', text)
        # 4. 清洗普通 WikiLinks: [[Note_Title]] -> Note_Title
        text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
        text = re.sub(r'!\[\[[\s\S]*?\]\]', '', text)
        text = re.sub(r'!\[[\s\S]*?\]\([\s\S]*?\)', '', text)
        
        # 5. 剥离所有中文/英文括号包裹的 URL 链接（多用于防翻译后错乱的 URL 残留）
        text = re.sub(r'[\(\（]\s*https?://[^\)\）\s]+\s*[\)\）]', '', text)
        # 6. 清洗标准及带空格的 Markdown 链接: [text](url) -> text
        text = re.sub(r'\[([\s\S]*?)\]\s*\([\s\S]*?\)', r'\1', text)
        
        # 7. 清除字幕中的说话人切换标识 (如 >>)
        text = re.sub(r'>>', '', text)
        # 8. 清除字幕和正文里多余的声音事件/旁白标记 (如 [掌声], [欢呼], [Laughter], 【注】 等 12 字以内的短括号)
        text = re.sub(r'\[[^\]]{1,12}\]', '', text)
        text = re.sub(r'【[^】]{1,12}】', '', text)
        
        text = re.sub(r'^\s*#+\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*|__|\*|_', '', text)
        text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*[\*\-\+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'https?://[^\s]+', '', text)
        return text

    def clean_text(self, text: str) -> str:
        # 首先进行统一的异形空格标准化与格式符号清理
        text = text.replace('\xa0', ' ').replace('&nbsp;', ' ').replace('&amp;', ' and ')
        text = text.replace('|', ' ')  # 过滤表格线以防止 TTS 爆音
        
        text = self.strip_markdown(text)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # 保护缩进和段落，但压缩连续的空白
        text = re.sub(r'[ \t]+', ' ', text)
        text = text.replace('·', '，')
        
        return text.strip()

    def is_chinese(self, text: str) -> bool:
        # 统计中文字符比例
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        return chinese_chars > (len(text) * 0.3)

    def smart_split(self, text: str, performance_profile: str | None = None) -> list[str]:
        cleaned_text = self.clean_text(text)
        
        # 1. 识别主语言并设置参数
        is_zh = self.is_chinese(cleaned_text)
        
        # 2. 缩写词句点保护逻辑 (主要针对英文，避免假性断句)
        abbreviations = {
            "e.g.": "e_g_",
            "i.e.": "i_e_",
            "et al.": "et_al_",
            "etc.": "etc_",
            "vs.": "vs_",
            "Dr.": "Dr_",
            "Mr.": "Mr_",
            "Mrs.": "Mrs_",
            "Prof.": "Prof_",
            "St.": "St_",
            "Jan.": "Jan_",
            "Feb.": "Feb_",
            "Mar.": "Mar_",
            "Apr.": "Apr_",
            "Jun.": "Jun_",
            "Jul.": "Jul_",
            "Aug.": "Aug_",
            "Sept.": "Sept_",
            "Oct.": "Oct_",
            "Nov.": "Nov_",
            "Dec.": "Dec_"
        }
        
        processed_text = cleaned_text
        if not is_zh:
            # 保护预设的常见缩写词
            for abbr, placeholder in abbreviations.items():
                processed_text = re.sub(r'\b' + re.escape(abbr), placeholder, processed_text, flags=re.IGNORECASE)
            
            # 保护姓名缩写后面的点，例如 "N. H. Selander" -> "N_DOT_ H_DOT_ Selander"
            processed_text = re.sub(r'\b([A-Z])\.(?=\s|$)', r'\1_DOT_', processed_text)
        
        # 3. 决定断句参数与正则
        profile = performance_profile if performance_profile in {"fast", "balanced", "quiet"} else "balanced"
        split_profiles = {
            "fast": {"zh_max": 250, "zh_target": 180, "en_max": 600, "en_target": 450},
            "balanced": {"zh_max": 220, "zh_target": 160, "en_max": 500, "en_target": 350},
            "quiet": {"zh_max": 180, "zh_target": 130, "en_max": 400, "en_target": 280},
        }
        split_profile = split_profiles[profile]

        if is_zh:
            max_chunk_chars = split_profile["zh_max"]
            target_chunk_chars = split_profile["zh_target"]
            sentence_endings = re.compile(r'([。！？!?;；\n]|(?:\.|\?|\!)(?=\s|$))')
        else:
            max_chunk_chars = split_profile["en_max"]
            target_chunk_chars = split_profile["en_target"]
            sentence_endings = re.compile(r'([.?!;；\n](?=\s|$))')
        
        raw_parts = sentence_endings.split(processed_text)
        sentences = []
        for i in range(0, len(raw_parts) - 1, 2):
            sent = raw_parts[i] + raw_parts[i+1]
            if sent.strip():
                sentences.append(sent.strip())
        if len(raw_parts) % 2 == 1 and raw_parts[-1].strip():
            sentences.append(raw_parts[-1].strip())
            
        # 4. 还原被保护的缩写词与姓名缩写点
        restored_sentences = []
        for sent in sentences:
            restored_sent = sent
            if not is_zh:
                # 还原预设的常见缩写
                for abbr, placeholder in abbreviations.items():
                    restored_sent = re.sub(re.escape(placeholder), abbr, restored_sent, flags=re.IGNORECASE)
                # 还原姓名缩写点
                restored_sent = re.sub(r'\b([A-Z])_DOT_', r'\1.', restored_sent)
            restored_sentences.append(restored_sent)
            
        # 5. 按照水位参数组装分块
        chunks = []
        current_chunk = ""
        
        for sent in restored_sentences:
            if len(sent) > max_chunk_chars:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                
                sub_parts = re.split(r'([，,：: ]+)', sent)
                sub_current = ""
                for j in range(0, len(sub_parts) - 1, 2):
                    part = sub_parts[j] + sub_parts[j+1]
                    if len(sub_current) + len(part) < target_chunk_chars:
                        sub_current += part
                    else:
                        if sub_current: chunks.append(sub_current.strip())
                        sub_current = part
                if sub_current: chunks.append(sub_current.strip())
            
            elif len(current_chunk) + len(sent) < max_chunk_chars:
                sep = " " if not is_zh and current_chunk and not current_chunk.endswith(" ") else ""
                current_chunk += sep + sent
                if len(current_chunk) >= target_chunk_chars:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sent
        
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        return [c for c in chunks if c.strip()]

    def parse_dialogue_or_text(self, text: str, performance_profile: str | None = None) -> list:
        base_ref_path = runtime_paths.reference_path
        serena_ref_audio = os.path.join(base_ref_path, "bbc_news.wav")
        serena_ref_text = "This is the research headquarters for one of the oldest companies in tech, IBM."

        ryan_ref_audio = os.path.join(base_ref_path, "ref_ryan.wav")
        ryan_ref_text = "各位听众大家好，欢迎收听本期的新闻快报，我是男主持瑞恩。"

        
        # 1. 识别对话标识，例如:
        # [Serena]: Hello
        # [Ryan]: Hi
        # Serena: Hello
        # Ryan: Hi
        # 支持中英文冒号，支持括号
        lines = text.split("\n")
        dialogue_pattern = re.compile(r'^\s*(?:\[?(Serena|Ryan)\]?)\s*[:：]\s*(.*)', re.IGNORECASE)
        
        turns = []
        is_dialogue = False
        
        for line in lines:
            if not line.strip():
                continue
            match = dialogue_pattern.match(line)
            if match:
                is_dialogue = True
                speaker = match.group(1).strip().capitalize() # 规范化为 "Serena" 或 "Ryan"
                content = match.group(2).strip()
                turns.append((speaker, content))
            else:
                # 如果当前行没有匹配到，但前面已经是对话模式，我们可以追加到上一个人的说话内容里
                if is_dialogue and turns:
                    prev_speaker, prev_content = turns[-1]
                    turns[-1] = (prev_speaker, prev_content + "\n" + line.strip())
                else:
                    # 还没有开启对话模式，按普通文本存
                    turns.append((None, line.strip()))
                    
        # 如果至少有一次检测到了 Serena 或 Ryan 说话，那么就按对话模式分发
        # 否则回退为普通文本
        has_dialogue = any(sp in ["Serena", "Ryan"] for sp, _ in turns)
        
        final_chunks = []
        if has_dialogue:
            for speaker, content in turns:
                if not content.strip():
                    continue
                # 将本段内容使用 smart_split 切分
                sub_chunks = self.smart_split(content, performance_profile=performance_profile)
                for chunk in sub_chunks:
                    if not chunk.strip():
                        continue
                    
                    if speaker == "Serena":
                        cfg = {
                            "voice": "Serena",
                            "instruct": "Professional female anchor, steady and clear.",
                            "ref_audio": serena_ref_audio,
                            "ref_text": serena_ref_text
                        }
                    elif speaker == "Ryan":
                        cfg = {
                            "voice": "Ryan",
                            "instruct": "A professional male anchor, reading news in a steady and clear voice.",
                            "ref_audio": ryan_ref_audio,
                            "ref_text": ryan_ref_text
                        }
                    else:
                        cfg = {}
                    
                    final_chunks.append({
                        "text": chunk,
                        "config": cfg
                    })
        else:
            # 普通文本直接 smart_split，返回普通的字符串列表即可
            final_chunks = self.smart_split(text, performance_profile=performance_profile)
            
        return final_chunks
