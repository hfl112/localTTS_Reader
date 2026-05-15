export default defineContentScript({
  matches: ["<all_urls>"],
  runAt: "document_end",
  main() {
    console.log("[Qwen TTS] Hover Queue UI script loaded.");

    // --- Icons (Lucide-style SVGs) ---
    const ICONS = {
      prev: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/></svg>`,
      play: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>`,
      pause: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`,
      next: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg>`,
      stop: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/></svg>`,
      save: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>`,
      list: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`
    };

    const callApi = async (endpoint: string, data?: any) => {
      try {
        return await browser.runtime.sendMessage({ type: "QWEN_COMMAND", endpoint, data });
      } catch (err) {
        console.error("[Qwen TTS] API Error:", err);
        return { error: "Connection failed" };
      }
    };

    const initUI = () => {
      if (document.getElementById('qwen-tts-floating-root')) return;
      if (!document.body) { setTimeout(initUI, 200); return; }

      // --- Container ---
      const container = document.createElement('div');
      container.id = 'qwen-tts-floating-root';
      container.style.cssText = `
        position: fixed; bottom: 30px; right: 30px;
        z-index: 2147483647; user-select: none;
      `;
      document.body.appendChild(container);
      const shadow = container.attachShadow({ mode: 'open' });

      // --- CSS ---
      const style = document.createElement('style');
      style.textContent = `
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes pulseGlow {
          0% { box-shadow: 0 0 8px rgba(77, 163, 255, 0.3); }
          50% { box-shadow: 0 0 16px rgba(77, 163, 255, 0.6); }
          100% { box-shadow: 0 0 8px rgba(77, 163, 255, 0.3); }
        }

        .bar {
          display: flex; align-items: center;
          background: rgba(20, 20, 20, 0.85);
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
          border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: 22px;
          padding: 6px;
          box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
          transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
          overflow: hidden;
          max-width: 50px;
        }
        .bar:hover, .bar.active {
          max-width: 420px;
          padding: 6px 12px;
        }

        .logo {
          width: 32px; height: 32px; border-radius: 50%;
          background: rgba(255, 255, 255, 0.1);
          display: flex; align-items: center; justify-content: center;
          color: white; font-weight: 700; font-size: 16px;
          flex-shrink: 0; cursor: grab; transition: 0.2s;
        }
        .logo:hover { background: rgba(255, 255, 255, 0.2); }

        .content {
          display: flex; align-items: center; gap: 4px;
          opacity: 0; transition: opacity 0.3s;
          pointer-events: none; margin-left: 0;
        }
        .bar:hover .content, .bar.active .content {
          opacity: 1; pointer-events: auto; margin-left: 10px;
        }

        .btn {
          width: 36px; height: 36px; border-radius: 12px;
          display: flex; align-items: center; justify-content: center;
          color: rgba(255, 255, 255, 0.8);
          background: transparent; border: none; cursor: pointer;
          transition: all 0.2s;
        }
        .btn:hover { background: rgba(255, 255, 255, 0.1); color: white; transform: scale(1.1); }
        .btn svg { width: 18px; height: 18px; }
        .btn.play-active { color: #4DA3FF; background: rgba(77, 163, 255, 0.15); animation: pulseGlow 2s infinite; }

        .divider { width: 1px; height: 18px; background: rgba(255, 255, 255, 0.12); margin: 0 6px; }

        /* Queue Popup */
        .queue-popup {
          position: absolute; bottom: 60px; right: 0;
          width: 240px; background: rgba(25, 25, 25, 0.95);
          backdrop-filter: blur(20px); border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.4);
          padding: 12px; display: none; flex-direction: column; gap: 8px;
          animation: fadeIn 0.2s ease-out; z-index: 1000;
        }
        .queue-popup.show { display: flex; }
        .queue-header {
          font-size: 13px; font-weight: 600; color: rgba(255,255,255,0.5);
          padding: 0 4px 4px 4px; border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .queue-list { max-height: 200px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
        .queue-item {
          display: flex; align-items: center; gap: 8px; padding: 8px;
          border-radius: 8px; cursor: pointer; transition: 0.2s;
        }
        .queue-item:hover { background: rgba(255,255,255,0.05); }
        .queue-item input[type="checkbox"] { cursor: pointer; }
        .queue-item-text {
          flex: 1; font-size: 13px; color: rgba(255,255,255,0.9);
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .queue-item-time { font-size: 11px; color: rgba(255,255,255,0.3); }
        .queue-footer { padding-top: 8px; display: flex; justify-content: flex-end; }
        .btn-play-selected {
          background: #4DA3FF; color: white; border: none; padding: 6px 12px;
          border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer;
        }
        .btn-play-selected:hover { filter: brightness(1.1); }

        .toast {
          position: fixed; bottom: 90px; right: 30px;
          background: rgba(40, 40, 40, 0.95); color: white; padding: 10px 20px;
          border-radius: 12px; font-size: 14px; box-shadow: 0 8px 24px rgba(0,0,0,0.3);
          backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.1);
          animation: fadeIn 0.3s ease; z-index: 2147483647;
        }
      `;
      shadow.appendChild(style);

      // --- HTML Structure ---
      const bar = document.createElement('div');
      bar.className = 'bar';
      
      const logo = document.createElement('div');
      logo.className = 'logo';
      logo.textContent = 'Q';
      
      const content = document.createElement('div');
      content.className = 'content';

      const btnPrev = createBtn(ICONS.prev, '上一句');
      const btnPlayPause = createBtn(ICONS.play, '播放/暂停');
      const btnNext = createBtn(ICONS.next, '下一句');
      const btnStop = createBtn(ICONS.stop, '停止');
      const div1 = document.createElement('div'); div1.className = 'divider';
      const btnSave = createBtn(ICONS.save, '保存到稍后听');
      const btnQueue = createBtn(ICONS.list, '最近收藏');

      function createBtn(svg: string, title: string) {
        const b = document.createElement('button');
        b.className = 'btn';
        b.innerHTML = svg;
        b.title = title;
        return b;
      }

      content.append(btnPrev, btnPlayPause, btnNext, btnStop, div1, btnSave, btnQueue);
      bar.append(logo, content);
      
      const queuePopup = document.createElement('div');
      queuePopup.className = 'queue-popup';
      
      shadow.appendChild(queuePopup);
      shadow.appendChild(bar);

      // --- Logic ---
      let isPlaying = false;
      let hideTimeout: any = null;

      btnPlayPause.onclick = () => callApi(isPlaying ? "/pause" : "/resume");
      btnStop.onclick = () => callApi("/stop");
      btnPrev.onclick = () => callApi("/seek", { direction: -1 });
      btnNext.onclick = () => callApi("/seek", { direction: 1 });
      
      btnSave.onclick = async () => {
        const text = window.getSelection()?.toString().trim();
        if (text) {
          const res = await callApi("/save_for_later", { text });
          if (res && !res.error) showToast("✨ 已存入稍后听");
        } else {
          showToast("💡 请先划选文字");
        }
      };

      // Hover Queue Logic
      btnQueue.onmouseenter = async () => {
        clearTimeout(hideTimeout);
        const items = await callApi("/saved_items");
        renderQueue(items || []);
        queuePopup.classList.add('show');
      };

      bar.onmouseleave = () => {
        hideTimeout = setTimeout(() => {
          if (!queuePopup.matches(':hover')) {
            queuePopup.classList.remove('show');
          }
        }, 500);
      };

      queuePopup.onmouseleave = () => {
        queuePopup.classList.remove('show');
      };

      function renderQueue(items: any[]) {
        queuePopup.innerHTML = `
          <div class="queue-header">最近收藏 (${items.length})</div>
          <div class="queue-list">
            ${items.length === 0 ? '<div style="font-size:12px;color:#666;text-align:center;padding:10px;">暂无收藏</div>' : 
              items.map((item, i) => `
                <div class="queue-item" data-idx="${i}">
                  <input type="checkbox" checked data-idx="${i}">
                  <span class="queue-item-text" title="${item.text}">${item.title || '无标题'}</span>
                  <span class="queue-item-time">${Math.ceil(item.text.length / 200)}m</span>
                </div>
              `).join('')
            }
          </div>
          ${items.length > 0 ? `
            <div class="queue-footer">
              <button class="btn-play-selected">▶ 播放选中项</button>
            </div>
          ` : ''}
        `;

        const playBtn = queuePopup.querySelector('.btn-play-selected');
        if (playBtn) {
          (playBtn as HTMLElement).onclick = async () => {
            const selectedIndices = Array.from(queuePopup.querySelectorAll('input[type="checkbox"]:checked'))
              .map(cb => parseInt((cb as HTMLElement).getAttribute('data-idx') || '0'));
            
            if (selectedIndices.length > 0) {
              await callApi("/play_saved", { indices: selectedIndices });
              queuePopup.classList.remove('show');
              showToast("🎙️ 开始播放选中收藏...");
            }
          };
        }

        // Make whole item clickable for toggle
        queuePopup.querySelectorAll('.queue-item').forEach(item => {
          (item as HTMLElement).onclick = (e) => {
            if (e.target instanceof HTMLInputElement) return;
            const cb = item.querySelector('input') as HTMLInputElement;
            cb.checked = !cb.checked;
          };
        });
      }

      function showToast(msg: string) {
        const t = document.createElement('div');
        t.className = 'toast';
        t.textContent = msg;
        shadow.appendChild(t);
        setTimeout(() => t.remove(), 3000);
      }

      // --- Status Sync ---
      browser.runtime.onMessage.addListener((message: any) => {
        if (message.type === "QWEN_STATUS_UPDATE") {
          isPlaying = message.data.is_playing;
          btnPlayPause.innerHTML = isPlaying ? ICONS.pause : ICONS.play;
          btnPlayPause.classList.toggle('play-active', isPlaying);
          bar.classList.toggle('active', isPlaying);
        }
      });

      // --- Draggable ---
      let isDragging = false;
      let startX: number, startY: number, initialX: number, initialY: number;

      logo.onmousedown = (e) => {
        isDragging = true;
        startX = e.clientX; startY = e.clientY;
        const rect = container.getBoundingClientRect();
        initialX = rect.left; initialY = rect.top;
        container.style.cursor = 'grabbing';
      };

      window.onmousemove = (e) => {
        if (!isDragging) return;
        container.style.left = (initialX + e.clientX - startX) + 'px';
        container.style.top = (initialY + e.clientY - startY) + 'px';
        container.style.bottom = 'auto'; container.style.right = 'auto';
      };

      window.onmouseup = () => { isDragging = false; container.style.cursor = 'default'; };
    };

    initUI();
  },
});
