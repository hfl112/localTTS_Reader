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

  let eventSource: EventSource | null = null;

  // 使用 Server-Sent Events (SSE) 推送状态，彻底消除 1Hz HTTP 轮询的 CPU 和进程唤醒开销
  const startSSEConnection = () => {
    if (eventSource) {
      eventSource.close();
    }

    eventSource = new EventSource(`${API_URL}/stream/status`);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        lastState = data;

        // 仅广播给活跃网页标签页，保护后台标签页性能
        browser.tabs.query({ active: true }).then(tabs => {
          tabs.forEach(tab => {
            if (tab.id) {
              browser.tabs.sendMessage(tab.id, { type: "QWEN_STATUS_UPDATE", data }).catch(() => {});
            }
          });
        });
      } catch (err) {
        console.error("[Qwen TTS] 无法解析推送的状态数据", err);
      }
    };

    eventSource.onerror = () => {
      console.warn("[Qwen TTS] 与后台 SSE 推送流断开，将在 5 秒后尝试自动重连...");
      eventSource?.close();
      setTimeout(startSSEConnection, 5000);
    };
  };

  startSSEConnection();

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
