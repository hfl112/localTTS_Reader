export default defineBackground(() => {
  const APP_API_URL = "http://127.0.0.1:8001/read";

  const sendToApp = async (text: string, tabId?: number) => {
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
      if (tabId) {
        browser.tabs.sendMessage(tabId, { 
          type: "TTS_ERROR", 
          error: "无法连接到 Qwen App，请确保它正在菜单栏运行 (Port 8001)" 
        });
      }
    }
  };

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

  // Handle Context Menu clicks
  browser.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === "qwen-tts-read-v2" && info.selectionText) {
      sendToApp(info.selectionText, tab?.id);
    }
  });

  // Handle Keyboard Shortcuts
  browser.commands.onCommand.addListener(async (command) => {
    if (command === "qwen-tts-read") {
      try {
        const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
        if (!tab || !tab.id) return;
        
        // Inject script to get the selected text from the active tab
        const results = await browser.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => window.getSelection()?.toString() || ""
        });
        
        const selectionText = results[0]?.result;
        if (selectionText) {
          sendToApp(selectionText, tab.id);
        }
      } catch (err) {
        console.error("Error handling command:", err);
      }
    }
  });
});
