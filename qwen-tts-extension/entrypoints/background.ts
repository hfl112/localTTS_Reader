export default defineBackground(() => {
  // App 的内部 API 地址 (8001 端口)
  const APP_API_URL = "http://127.0.0.1:8001/read";

  browser.runtime.onInstalled.addListener(() => {
    browser.contextMenus.removeAll(() => {
      browser.contextMenus.create({
        id: "qwen-tts-read-v2",
        title: "使用 Qwen App 朗读",
        contexts: ["selection"],
      });
      console.log("Qwen App Remote Ready.");
    });
  });

  browser.contextMenus.onClicked.addListener(async (info, tab) => {
    if (info.menuItemId === "qwen-tts-read-v2" && info.selectionText) {
      const text = info.selectionText;
      console.log("Sending text to Qwen App...");

      try {
        const response = await fetch(APP_API_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: text, index: 0 }),
        });

        if (!response.ok) {
          throw new Error(`App is not responding (HTTP ${response.status})`);
        }
        console.log("Success! App is reading the text.");

      } catch (err: any) {
        console.error("Remote Read Error:", err);
        // 如果 App 没开，给个友好的提醒
        if (tab?.id) {
          browser.tabs.sendMessage(tab.id, { 
            type: "TTS_ERROR", 
            error: "无法连接到 Qwen App，请确保它正在菜单栏运行 (Port 8001)" 
          });
        }
      }
    }
  });
});
