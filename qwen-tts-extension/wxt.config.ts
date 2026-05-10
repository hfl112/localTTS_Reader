import { defineConfig } from 'wxt';

// See https://wxt.dev/api/config.html
export default defineConfig({
  manifest: {
    name: "Qwen TTS Reader",
    permissions: ["contextMenus", "storage"],
    host_permissions: ["http://127.0.0.1:8001/*"],
  },
});
