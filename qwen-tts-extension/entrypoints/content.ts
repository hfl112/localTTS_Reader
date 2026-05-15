export default defineContentScript({
  matches: ["<all_urls>"],
  runAt: "document_end",
  main() {
    console.log("[Qwen TTS] Content script loaded on:", window.location.href);

    const callApi = async (endpoint: string, data?: any) => {
      try {
        return await browser.runtime.sendMessage({
          type: "QWEN_COMMAND",
          endpoint,
          data
        });
      } catch (err) {
        console.error("Failed to send message to background:", err);
        return { error: "Message failed" };
      }
    };

    const initUI = () => {
      if (document.getElementById('qwen-tts-floating-root')) return;
      
      const container = document.createElement('div');
      container.id = 'qwen-tts-floating-root';
      container.style.position = 'fixed';
      container.style.bottom = '30px';
      container.style.right = '30px';
      container.style.zIndex = '2147483647';

      if (!document.body) {
        setTimeout(initUI, 200);
        return;
      }

      document.body.appendChild(container);
      const shadow = container.attachShadow({ mode: 'open' });

      // Styles
      const style = document.createElement('style');
      style.textContent = `
        .fab-container {
          font-family: system-ui, -apple-system, sans-serif;
          display: flex;
          align-items: center;
          background: #1e1e1e;
          border-radius: 50px;
          box-shadow: 0 8px 24px rgba(0,0,0,0.5);
          padding: 6px;
          transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
          overflow: hidden;
          border: 1px solid rgba(255,255,255,0.1);
        }
        .fab-icon {
          width: 44px;
          height: 44px;
          border-radius: 50%;
          background: linear-gradient(135deg, #4a4a4a, #2a2a2a);
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          color: white;
          font-weight: bold;
          font-size: 20px;
          flex-shrink: 0;
          user-select: none;
        }
        .fab-icon:hover { filter: brightness(1.2); }
        .controls {
          display: flex;
          align-items: center;
          width: 0;
          opacity: 0;
          transition: all 0.3s ease;
          pointer-events: none;
          white-space: nowrap;
        }
        .fab-container.expanded .controls {
          width: 260px;
          opacity: 1;
          pointer-events: auto;
          padding: 0 10px;
        }
        button {
          background: none;
          border: none;
          color: white;
          font-size: 18px;
          cursor: pointer;
          padding: 5px 8px;
          border-radius: 8px;
          transition: background 0.2s;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        button:hover { background: rgba(255,255,255,0.15); }
        button:active { transform: scale(0.9); }
        .separator {
          width: 1px;
          height: 24px;
          background: rgba(255,255,255,0.2);
          margin: 0 8px;
        }
        .status-dot {
          width: 8px;
          height: 8px;
          background: #4caf50;
          border-radius: 50%;
          position: absolute;
          bottom: 2px;
          right: 2px;
          border: 2px solid #1e1e1e;
          display: none;
        }
        .fab-container.active .status-dot { display: block; }
      `;
      shadow.appendChild(style);

      // UI Structure
      const wrapper = document.createElement('div');
      wrapper.className = 'fab-container';
      
      const iconBtn = document.createElement('div');
      iconBtn.className = 'fab-icon';
      iconBtn.textContent = 'Q';
      const dot = document.createElement('div');
      dot.className = 'status-dot';
      iconBtn.appendChild(dot);
      
      const controls = document.createElement('div');
      controls.className = 'controls';

      const btnPrev = createBtn('⏮', '上一句');
      const btnPlayPause = createBtn('⏯', '播放/暂停');
      const btnNext = createBtn('⏭', '下一句');
      const btnStop = createBtn('⏹', '停止');
      const sep = document.createElement('div');
      sep.className = 'separator';
      const btnSave = createBtn('💾', '保存到稍后听');
      const btnPodcast = createBtn('🎙️', '生成播客');

      function createBtn(icon: string, title: string) {
        const b = document.createElement('button');
        b.innerHTML = icon;
        b.title = title;
        return b;
      }

      controls.append(btnPrev, btnPlayPause, btnNext, btnStop, sep, btnSave, btnPodcast);
      wrapper.append(iconBtn, controls);
      shadow.appendChild(wrapper);

      // Logic
      let isExpanded = false;
      let isPlaying = false;

      iconBtn.onclick = () => {
        isExpanded = !isExpanded;
        wrapper.classList.toggle('expanded', isExpanded);
      };

      btnPlayPause.onclick = async () => {
        await callApi(isPlaying ? "/pause" : "/resume");
      };

      btnStop.onclick = () => callApi("/stop");
      btnPrev.onclick = () => callApi("/seek", { direction: -1 });
      btnNext.onclick = () => callApi("/seek", { direction: 1 });
      
      btnSave.onclick = async () => {
        const text = window.getSelection()?.toString().trim();
        if (text) {
          const res = await callApi("/save_for_later", { text });
          if (res && !res.error) showToast("已保存到稍后听");
        } else {
          showToast("请先划选文字");
        }
      };

      btnPodcast.onclick = async () => {
        const res = await callApi("/generate_podcast");
        if (res && res.error) {
          showToast("错误: " + res.error);
        } else {
          showToast("播客生成中，请稍后查看 data/podcasts");
        }
      };

      function showToast(msg: string) {
        const toast = document.createElement('div');
        toast.textContent = msg;
        toast.style.cssText = `
          position: fixed; bottom: 90px; right: 30px;
          background: #333; color: white; padding: 8px 16px;
          border-radius: 8px; font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
          z-index: 2147483647;
        `;
        shadow.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
      }

      browser.runtime.onMessage.addListener((message: any) => {
        if (message.type === "QWEN_STATUS_UPDATE") {
          isPlaying = message.data.is_playing;
          btnPlayPause.innerHTML = isPlaying ? '⏸' : '▶️';
          wrapper.classList.toggle('active', isPlaying);
        }
        if (message.type === "TTS_ERROR") {
          showToast(message.error);
        }
      });

      browser.runtime.sendMessage({ type: "GET_LAST_STATE" }).then(state => {
        if (state) {
          isPlaying = state.is_playing;
          btnPlayPause.innerHTML = isPlaying ? '⏸' : '▶️';
          wrapper.classList.toggle('active', isPlaying);
        }
      });
    };

    // Run initialization
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initUI);
    } else {
      initUI();
    }
  },
});
