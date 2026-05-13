import { defineConfig } from 'wxt';

// See https://wxt.dev/api/config.html
export default defineConfig({
  manifest: {
    name: "Qwen TTS Reader",
    permissions: ["contextMenus", "storage", "activeTab", "scripting"],
    host_permissions: ["http://127.0.0.1:8001/*"],
    commands: {
      "qwen-tts-read": {
        "suggested_key": {
          "default": "Alt+S",
          "mac": "MacCtrl+S"
        },
        "description": "Read selected text with Qwen TTS"
      }
    }
  },
});
