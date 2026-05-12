import json
import os

class Storage:
    def __init__(self, data_dir="data"):
        self.config_path = os.path.join(data_dir, "config.json")
        self.state_path = os.path.join(data_dir, "state.json")
        
        # 默认设置
        self.default_config = {
            "voice": "Serena",
            "temperature": 0.2,
            "top_p": 0.5,
            "seed": 42,
            "repetition_penalty": 1.1,
            "lang_code": "zh"
        }
        
        # 默认运行状态（断点续传）
        self.default_state = {
            "current_article": {
                "title": "",
                "chunks": [],
                "current_index": 0
            },
            "history": []
        }

    def load_config(self):
        if not os.path.exists(self.config_path):
            return self.default_config
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_config(self, config):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def load_state(self):
        if not os.path.exists(self.state_path):
            return self.default_state
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_state(self, state):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
