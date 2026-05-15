export default defineBackground(() => {
  const API_URL = "http://127.0.0.1:8001";

  // Shared state for all tabs
  let lastState = { is_playing: false };

  const callBackend = async (endpoint: string, method: string = 'POST', data?: any) => {
    try {
      const response = await fetch(`${API_URL}${endpoint}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: data ? JSON.stringify(data) : undefined
      });
      if (!response.ok) return { error: `Server error: ${response.status}` };
      return await response.json();
    } catch (err) {
      console.error("Backend communication failed:", err);
      return { error: "Connection failed" };
    }
  };

  // Poll backend status and broadcast to all tabs
  setInterval(async () => {
    try {
      const data = await callBackend("/status", "GET");
      if (data && !data.error) {
        lastState = data;
        const tabs = await browser.tabs.query({});
        tabs.forEach(tab => {
          if (tab.id) {
            browser.tabs.sendMessage(tab.id, { type: "QWEN_STATUS_UPDATE", data }).catch(() => {});
          }
        });
      }
    } catch (e) {}
  }, 1000);

  // Listen for messages from content script
  browser.runtime.onMessage.addListener(async (message: any, sender) => {
    if (message.type === "QWEN_COMMAND") {
      const result = await callBackend(message.endpoint, 'POST', message.data);
      return result;
    }
    if (message.type === "GET_LAST_STATE") {
      return lastState;
    }
  });

  // Original context menu and shortcut logic
  browser.runtime.onInstalled.addListener(() => {
    browser.contextMenus.removeAll(() => {
      browser.contextMenus.create({
        id: "qwen-tts-read-v2",
        title: "使用 Qwen App 朗读",
        contexts: ["selection"],
      });
    });
  });

  browser.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === "qwen-tts-read-v2" && info.selectionText) {
      callBackend("/read", "POST", { text: info.selectionText, index: 0 });
    }
  });

  browser.commands.onCommand.addListener(async (command) => {
    if (command === "qwen-tts-read") {
      const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
      if (tab?.id) {
        const results = await browser.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => window.getSelection()?.toString() || ""
        });
        const selectionText = results[0]?.result;
        if (selectionText) {
          callBackend("/read", "POST", { text: selectionText, index: 0 });
        }
      }
    }
  });
});
