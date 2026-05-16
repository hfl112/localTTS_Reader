export default defineContentScript({
  matches: ["<all_urls>"],
  runAt: "document_end",
  main() {
    console.log("[Qwen TTS] Polishing UI Structure...");

    const ICONS = {
      prev: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/></svg>`,
      play: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>`,
      pause: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`,
      next: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg>`,
      stop: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/></svg>`,
      clipboard: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>`,
      bookmark: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>`,
      list: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`,
      trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`
    };

    const callApi = async (endpoint: string, data?: any, method: string = 'POST') => {
      return await browser.runtime.sendMessage({ type: "QWEN_COMMAND", endpoint, data, method });
    };

    const initUI = () => {
      if (document.getElementById('qwen-tts-floating-root')) return;
      if (!document.body) { setTimeout(initUI, 200); return; }

      const container = document.createElement('div');
      container.id = 'qwen-tts-floating-root';
      container.style.cssText = `position: fixed; bottom: 30px; right: 30px; z-index: 2147483647; user-select: none;`;
      document.body.appendChild(container);
      const shadow = container.attachShadow({ mode: 'open' });

      const style = document.createElement('style');
      style.textContent = `
        @keyframes fadeIn { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
        .bar {
          display: flex; align-items: center; background: rgba(24, 24, 27, 0.82);
          backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
          border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 26px;
          padding: 4px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.3);
          transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1); overflow: hidden;
          max-width: 38px; height: 38px; 
        }
        .bar:hover, .bar.active { max-width: 440px; padding: 4px 6px; }
        .logo {
          width: 30px; height: 30px; border-radius: 50%; background: transparent;
          display: flex; align-items: center; justify-content: center; color: rgba(255, 255, 255, 0.7);
          font-weight: 700; font-size: 14px; flex-shrink: 0; cursor: grab; transition: 0.2s;
        }
        .logo:hover { background: rgba(255, 255, 255, 0.06); color: white; }
        .content {
          display: flex; align-items: center; gap: 2px; opacity: 0;
          transition: opacity 0.3s; pointer-events: none; margin-left: 0;
        }
        .bar:hover .content, .bar.active .content { opacity: 1; pointer-events: auto; margin-left: 6px; }
        .btn {
          width: 32px; height: 32px; border-radius: 10px; display: flex; align-items: center;
          justify-content: center; color: rgba(255, 255, 255, 0.75); background: transparent;
          border: none; cursor: pointer; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .btn:hover { background: rgba(255, 255, 255, 0.08); color: white; transform: scale(1.08); }
        .btn:active { transform: scale(0.94); }
        .btn svg { width: 17px; height: 17px; }
        .btn.active { color: #4DA3FF; background: rgba(77, 163, 255, 0.12); }
        
        .btn-play-main {
          color: white; background: rgba(255, 255, 255, 0.16); border: 1px solid rgba(255, 255, 255, 0.12);
          transform: scale(1.06); margin: 0 4px;
        }
        .btn-play-main:hover { transform: scale(1.12); background: rgba(255, 255, 255, 0.22); }
        .btn-play-main.active { color: #4DA3FF; background: rgba(77, 163, 255, 0.15); box-shadow: 0 0 12px rgba(77, 163, 255, 0.3); border-color: rgba(77, 163, 255, 0.4); }

        .divider { width: 1px; height: 16px; background: rgba(255, 255, 255, 0.08); margin: 0 4px; }
        
        /* Queue Popup - Narrow Card Anchored to Right */
        .queue-popup {
          position: absolute; bottom: 52px; right: 8px; width: 230px;
          background: rgba(24, 24, 27, 0.88); backdrop-filter: blur(28px);
          border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px;
          box-shadow: 0 15px 45px rgba(0, 0, 0, 0.35); padding: 8px;
          display: none; flex-direction: column; gap: 2px; animation: fadeIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
          z-index: 1000;
        }
        .queue-popup.show { display: flex; }
        .queue-header { font-size: 13px; font-weight: 500; color: rgba(255, 255, 255, 0.75); padding: 2px 4px 4px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); }
        .queue-list { max-height: 180px; overflow-y: auto; display: flex; flex-direction: column; padding-top: 2px; }
        .queue-item {
          display: flex; align-items: center; gap: 8px; height: 38px; padding: 0 6px; border-radius: 8px;
          cursor: pointer; transition: 0.2s;
        }
        .queue-item:hover { background: rgba(255, 255, 255, 0.06); }
        .queue-item input[type="checkbox"] { 
          appearance: none; width: 14px; height: 14px; border: 1px solid rgba(255, 255, 255, 0.25); 
          border-radius: 4px; cursor: pointer; transition: 0.2s; position: relative;
        }
        .queue-item input[type="checkbox"]:checked { background: rgba(77, 163, 255, 0.85); border-color: transparent; }
        .queue-item input[type="checkbox"]:checked::after {
          content: '✓'; color: white; font-size: 10px; position: absolute; left: 1.5px; top: -1px;
        }
        .queue-item-text { flex: 1; font-size: 13px; color: rgba(255,255,255,0.9); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .queue-item-time { font-size: 11px; color: rgba(255, 255, 255, 0.3); }
        .btn-del {
          opacity: 0; width: 22px; height: 22px; border-radius: 6px;
          display: flex; align-items: center; justify-content: center;
          color: #FF4D4D; transition: 0.2s;
        }
        .queue-item:hover .btn-del { opacity: 0.7; }
        .btn-del:hover { background: rgba(255, 77, 77, 0.12); opacity: 1; }
        
        .queue-footer { padding-top: 6px; display: flex; justify-content: flex-end; }
        .btn-play-selected {
          background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.05); color: rgba(255, 255, 255, 0.85); 
          height: 28px; padding: 0 12px; border-radius: 8px; font-size: 12px; font-weight: 500; cursor: pointer; transition: 0.2s;
        }
        .btn-play-selected:hover { background: rgba(255, 255, 255, 0.15); color: white; }

        .empty-state { padding: 8px 4px 4px; text-align: left; }
        .empty-main { font-size: 13px; color: rgba(255,255,255,0.8); margin-bottom: 2px; }
        .empty-sub { font-size: 11px; color: rgba(255,255,255,0.4); }

        .toast {
          position: fixed; bottom: 100px; right: 30px; background: rgba(30, 30, 30, 0.95);
          color: white; padding: 8px 18px; border-radius: 12px; font-size: 12px;
          box-shadow: 0 10px 25px rgba(0, 0, 0, 0.25); backdrop-filter: blur(10px);
          border: 1px solid rgba(255, 255, 255, 0.08); animation: fadeIn 0.3s ease; z-index: 2147483647;
        }
      `;
      shadow.appendChild(style);

      const bar = document.createElement('div');
      bar.className = 'bar';
      const logo = document.createElement('div');
      logo.className = 'logo'; logo.textContent = 'Q';
      const content = document.createElement('div');
      content.className = 'content';

      const btnPrev = createBtn(ICONS.prev, '上一句');
      const btnPlayPause = createBtn(ICONS.play, '播放/暂停');
      btnPlayPause.classList.add('btn-play-main');
      const btnNext = createBtn(ICONS.next, '下一句');
      const btnStop = createBtn(ICONS.stop, '停止');
      const d1 = document.createElement('div'); d1.className = 'divider';
      const btnClip = createBtn(ICONS.clipboard, '朗读剪切板');
      const btnSave = createBtn(ICONS.bookmark, '保存当前/选中');
      const btnQueue = createBtn(ICONS.list, '最近收藏');

      function createBtn(svg: string, title: string) {
        const b = document.createElement('button');
        b.className = 'btn'; b.innerHTML = svg; b.title = title;
        return b;
      }

      content.append(btnPrev, btnPlayPause, btnNext, btnStop, d1, btnClip, btnSave, btnQueue);
      bar.append(logo, content);
      const queuePopup = document.createElement('div');
      queuePopup.className = 'queue-popup';
      shadow.appendChild(queuePopup);
      shadow.appendChild(bar);

      let isPlaying = false, hideTimeout: any = null;

      btnPlayPause.onclick = () => callApi(isPlaying ? "/pause" : "/resume");
      btnStop.onclick = () => callApi("/stop");
      btnPrev.onclick = () => { callApi("/seek", { direction: -1 }); isPlaying = true; btnPlayPause.innerHTML = ICONS.pause; };
      btnNext.onclick = () => { callApi("/seek", { direction: 1 }); isPlaying = true; btnPlayPause.innerHTML = ICONS.pause; };
      
      btnClip.onclick = async () => {
        const res = await browser.runtime.sendMessage({ type: "READ_CLIPBOARD" });
        if (res?.error) showToast("❌ 剪切板为空"); else showToast("📋 正在朗读剪切板...");
      };

      btnSave.onclick = async () => {
        const sel = window.getSelection()?.toString().trim();
        if (sel) {
          const res = await callApi("/save_for_later", { text: sel });
          if (!res.error) showToast("✨ 已存入选中内容");
        } else {
          const res = await callApi("/save_current");
          if (!res.error) showToast("💾 已保存当前文章");
          else showToast("💡 请划选或开始朗读");
        }
      };

      btnQueue.onmouseenter = async () => {
        clearTimeout(hideTimeout);
        const items = await callApi("/saved_items", null, "GET");
        renderQueue(items || []);
        queuePopup.classList.add('show');
      };

      bar.onmouseleave = () => {
        hideTimeout = setTimeout(() => { if (!queuePopup.matches(':hover')) queuePopup.classList.remove('show'); }, 600);
      };
      queuePopup.onmouseleave = () => queuePopup.classList.remove('show');

      function renderQueue(items: any[]) {
        queuePopup.innerHTML = `<div class="queue-header">Saved · ${items.length}</div><div class="queue-list"></div>`;
        const list = queuePopup.querySelector('.queue-list')!;
        if (items.length === 0) {
          list.innerHTML = `<div class="empty-state"><div class="empty-main">还没有收藏内容</div><div class="empty-sub">划词后点击 🔖 即可保存</div></div>`;
          return;
        }

        items.forEach((item, i) => {
          const row = document.createElement('div');
          row.className = 'queue-item';
          row.innerHTML = `<input type="checkbox" checked data-idx="${i}"><span class="queue-item-text" title="${item.text}">${item.title}</span><span class="queue-item-time">${Math.ceil(item.text.length / 200)}m</span><div class="btn-del" title="删除">${ICONS.trash}</div>`;
          row.onclick = (e) => {
            if ((e.target as HTMLElement).closest('.btn-del')) { e.stopPropagation(); callApi("/delete_saved", { index: i }).then(() => btnQueue.onmouseenter?.(null as any)); return; }
            if (e.target instanceof HTMLInputElement) return;
            const cb = row.querySelector('input') as HTMLInputElement; cb.checked = !cb.checked;
          };
          list.appendChild(row);
        });

        const footer = document.createElement('div'); footer.className = 'queue-footer';
        const pBtn = document.createElement('button'); pBtn.className = 'btn-play-selected';
        const selectedCount = items.length;
        pBtn.textContent = `▶ Play ${selectedCount} Selected`;
        pBtn.onclick = async () => {
          const idxs = Array.from(queuePopup.querySelectorAll('input:checked')).map(cb => parseInt((cb as HTMLElement).getAttribute('data-idx')!));
          if (idxs.length) { await callApi("/play_saved", { indices: idxs }); queuePopup.classList.remove('show'); showToast("🎙️ 开始朗读队列..."); }
        };
        footer.appendChild(pBtn); queuePopup.appendChild(footer);
      }

      function showToast(msg: string) {
        const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg;
        shadow.appendChild(t); setTimeout(() => t.remove(), 3000);
      }

      browser.runtime.onMessage.addListener((msg: any) => {
        if (msg.type === "QWEN_STATUS_UPDATE") {
          isPlaying = msg.data.is_playing;
          btnPlayPause.innerHTML = isPlaying ? ICONS.pause : ICONS.play;
          btnPlayPause.classList.toggle('active', isPlaying);
          bar.classList.toggle('active', isPlaying);
        }
      });
      
      let isDragging = false, sx: number, sy: number, ix: number, iy: number;
      logo.onmousedown = (e) => { isDragging = true; sx = e.clientX; sy = e.clientY; const r = container.getBoundingClientRect(); ix = r.left; iy = r.top; container.style.cursor = 'grabbing'; };
      window.onmousemove = (e) => { if (!isDragging) return; container.style.left = (ix + e.clientX - sx) + 'px'; container.style.top = (iy + e.clientY - sy) + 'px'; container.style.bottom = 'auto'; container.style.right = 'auto'; };
      window.onmouseup = () => { isDragging = false; container.style.cursor = 'default'; };
    };

    initUI();
  },
});
