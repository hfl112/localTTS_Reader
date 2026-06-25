export default defineBackground(() => {
  // ==========================================
  // 动态后端发现 (Dynamic backend discovery)
  // 后端启动时在 8002~8100 间选择一个空闲端口，扩展无法读取本地文件，
  // 因此通过扫描 /health 端点来发现就绪的 QwenTTS 后端。
  // ==========================================
  const PORT_START = 8002;          // 后端绝不会用 8001（用户自占用）
  const PORT_END = 8020;            // 探测范围，足够覆盖常见情况
  const HEALTH_TIMEOUT_MS = 800;    // 单端口探测超时
  const STORAGE_KEY = "qwen_backend";
  const TOKEN_KEY = "qwen_pairing_token";   // 扩展配对令牌（用户从 App 复制）

  // 配对令牌：写操作端点需通过 x-extension-token 头认证
  let pairingToken: string | null = null;
  const loadPairingToken = async () => {
    try {
      const stored = await browser.storage.local.get(TOKEN_KEY);
      const t = stored?.[TOKEN_KEY];
      pairingToken = typeof t === "string" && t.length > 0 ? t : null;
    } catch {
      pairingToken = null;
    }
  };
  loadPairingToken();

  // 缓存：发现到的 base URL 及其 instance_id（内存 + chrome.storage.local）
  let baseUrl: string | null = null;
  let instanceId: string | null = null;
  let discovering: Promise<string | null> | null = null;

  // 探测单个端口的 /health，返回 base URL（就绪）或 null
  const probePort = async (port: number): Promise<{ url: string; instanceId: string } | null> => {
    const url = `http://127.0.0.1:${port}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
    try {
      const resp = await fetch(`${url}/health`, { signal: controller.signal });
      if (!resp.ok) return null;
      const data = await resp.json();
      if (data && data.status === "ready" && typeof data.instance_id === "string") {
        return { url, instanceId: data.instance_id };
      }
      return null;
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  };

  // 发现后端：优先尝试上次成功端口，然后并发扫描整个范围取最快就绪的那个
  const discoverBackend = async (): Promise<string | null> => {
    if (discovering) return discovering;

    discovering = (async () => {
      const candidates: number[] = [];

      // 优先尝试上次成功的端口
      try {
        const stored = await browser.storage.local.get(STORAGE_KEY);
        const lastPort = (stored?.[STORAGE_KEY] as { port?: number } | undefined)?.port;
        if (typeof lastPort === "number") candidates.push(lastPort);
      } catch {}

      for (let p = PORT_START; p <= PORT_END; p++) {
        if (!candidates.includes(p)) candidates.push(p);
      }

      // 并发探测，取第一个就绪的（最快返回的）
      const results = await Promise.allSettled(candidates.map(probePort));
      let found: { url: string; instanceId: string } | null = null;
      for (const r of results) {
        if (r.status === "fulfilled" && r.value) {
          found = r.value;
          break;
        }
      }

      if (found) {
        baseUrl = found.url;
        instanceId = found.instanceId;
        const port = parseInt(found.url.split(":").pop() || "0", 10);
        try {
          await browser.storage.local.set({ [STORAGE_KEY]: { port, instanceId: found.instanceId } });
        } catch {}
        console.log(`[Qwen TTS] 已发现后端: ${found.url} (instance ${found.instanceId})`);
        return found.url;
      }

      console.warn("[Qwen TTS] 未能发现就绪的 QwenTTS 后端");
      return null;
    })();

    try {
      return await discovering;
    } finally {
      discovering = null;
    }
  };

  // 清空缓存（后端重启 / 换端口 / 连接失败时调用）
  const invalidateBackend = () => {
    baseUrl = null;
    instanceId = null;
  };

  // 确保已发现 base URL
  const getBaseUrl = async (): Promise<string | null> => {
    if (baseUrl) return baseUrl;
    return discoverBackend();
  };

  // Shared state for all tabs
  let lastState = { is_playing: false };

  // 实际执行一次 fetch；返回结果及是否需要重发现的信号
  const doFetch = async (
    url: string,
    endpoint: string,
    method: string,
    data?: any
  ): Promise<{ result: any; networkError: boolean; staleInstance: boolean }> => {
    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (pairingToken) headers['x-extension-token'] = pairingToken;
      const response = await fetch(`${url}${endpoint}`, {
        method,
        headers,
        body: data ? JSON.stringify(data) : undefined
      });
      if (!response.ok) {
        return { result: { error: `Server error: ${response.status}` }, networkError: false, staleInstance: false };
      }
      const json = await response.json();
      // 若响应携带 instance_id 且与缓存不一致，说明后端重启换了实例/端口
      const staleInstance =
        instanceId != null && json && typeof json.instance_id === "string" && json.instance_id !== instanceId;
      return { result: json, networkError: false, staleInstance };
    } catch (err) {
      return { result: { error: "Connection failed" }, networkError: true, staleInstance: false };
    }
  };

  const callBackend = async (endpoint: string, method: string = 'POST', data?: any) => {
    let url = await getBaseUrl();
    if (!url) return { error: "Connection failed" };

    let { result, networkError, staleInstance } = await doFetch(url, endpoint, method, data);

    // 失败重发现：连接错误或实例不一致时清缓存重新发现一次再重试
    if (networkError || staleInstance) {
      console.warn("[Qwen TTS] 后端连接失败或实例变更，重新发现中...");
      invalidateBackend();
      url = await discoverBackend();
      if (!url) return { error: "Connection failed" };
      ({ result } = await doFetch(url, endpoint, method, data));
    }

    return result;
  };

  let eventSource: EventSource | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  const broadcastState = (data: any) => {
    lastState = data;
    // 仅广播给活跃网页标签页，保护后台标签页性能
    browser.tabs.query({ active: true }).then(tabs => {
      tabs.forEach(tab => {
        if (tab.id) {
          browser.tabs.sendMessage(tab.id, { type: "QWEN_STATUS_UPDATE", data }).catch(() => {});
        }
      });
    });
  };

  // 状态获取兜底：新后端没有 SSE /stream/status 端点（仅有 /snapshot 轮询端点），
  // 因此降级为每 1.5s 轮询 /snapshot。通过 callBackend 复用发现 / 失败重发现逻辑。
  const stopStatusPolling = () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const startStatusPolling = () => {
    stopStatusPolling();
    pollTimer = setInterval(async () => {
      const data = await callBackend("/snapshot", "GET");
      if (data && !data.error) {
        broadcastState(data);
      }
    }, 1500);
  };

  // 启动状态监听：先发现后端再开始轮询兜底
  const startStatusUpdates = async () => {
    await getBaseUrl();
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    startStatusPolling();
  };

  startStatusUpdates();

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

    if (message.type === "SET_PAIRING_TOKEN") {
      const t = typeof message.token === "string" ? message.token.trim() : "";
      pairingToken = t.length > 0 ? t : null;
      browser.storage.local.set({ [TOKEN_KEY]: t })
        .then(() => sendResponse({ ok: true }))
        .catch(() => sendResponse({ ok: false }));
      return true;
    }

    if (message.type === "GET_PAIRING_TOKEN") {
      browser.storage.local.get(TOKEN_KEY)
        .then(s => sendResponse({ token: (s?.[TOKEN_KEY] as string) || "" }))
        .catch(() => sendResponse({ token: "" }));
      return true;
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
