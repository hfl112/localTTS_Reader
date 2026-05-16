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

  // Consolidated message listener
  browser.runtime.onMessage.addListener((message: any, sender, sendResponse) => {
    console.log("[Qwen TTS] Message received:", message.type);

    if (message.type === "QWEN_COMMAND") {
      const method = message.method || 'POST';
      callBackend(message.endpoint, method, message.data)
        .then(result => sendResponse(result))
        .catch(() => sendResponse({ error: "Command failed" }));
      return true;
    }

    if (message.type === "READ_CLIPBOARD") {
      browser.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
        if (tab?.id) {
          browser.scripting.executeScript({
            target: { tabId: tab.id },
            func: () => navigator.clipboard.readText()
          }).then(results => {
            const clipText = results[0]?.result;
            if (clipText) {
              callBackend("/read", "POST", { text: clipText, index: 0 })
                .then(res => sendResponse(res));
            } else {
              sendResponse({ error: "Clipboard empty" });
            }
          }).catch(() => sendResponse({ error: "Scripting failed" }));
        } else {
          sendResponse({ error: "No active tab" });
        }
      });
      return true;
    }

    if (message.type === "GET_LAST_STATE") {
      sendResponse(lastState);
      return false;
    }
  });

  // Setup context menu
  browser.runtime.onInstalled.addListener(() => {
    browser.contextMenus.create({
      id: "qwen-read-selection",
      title: "使用 Qwen 朗读选中内容",
      contexts: ["selection"],
    });
  });

  browser.contextMenus.onClicked.addListener(async (info, tab) => {
    if (info.menuItemId === "qwen-read-selection" && info.selectionText) {
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
