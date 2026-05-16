import './style.css';

const ICONS = {
  prev: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/></svg>`,
  play: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>`,
  pause: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`,
  next: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg>`,
  stop: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/></svg>`,
  clipboard: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>`,
  bookmark: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`
};

const app = document.querySelector<HTMLDivElement>('#app')!;
app.innerHTML = `
  <div class="content">
    <button id="btnPrev" class="btn" title="上一句">${ICONS.prev}</button>
    <button id="btnPlayPause" class="btn btn-play-main" title="播放/暂停">${ICONS.play}</button>
    <button id="btnNext" class="btn" title="下一句">${ICONS.next}</button>
    <button id="btnStop" class="btn" title="停止">${ICONS.stop}</button>
    <div class="divider"></div>
    <button id="btnClip" class="btn" title="朗读剪切板">${ICONS.clipboard}</button>
    <button id="btnSave" class="btn" title="保存当前/选中">${ICONS.bookmark}</button>
  </div>
  <div class="queue-popup">
    <div class="queue-header">Saved · 0</div>
    <div class="queue-list"></div>
  </div>
  <div class="toast-container" id="toastContainer"></div>
`;

const callApi = async (endpoint: string, data?: any, method: string = 'POST') => {
  try {
    return await browser.runtime.sendMessage({ type: "QWEN_COMMAND", endpoint, data, method });
  } catch (err) {
    return { error: "Message failed" };
  }
};

let isPlaying = false;
const btnPlayPause = document.getElementById('btnPlayPause') as HTMLButtonElement;
const btnStop = document.getElementById('btnStop') as HTMLButtonElement;
const btnPrev = document.getElementById('btnPrev') as HTMLButtonElement;
const btnNext = document.getElementById('btnNext') as HTMLButtonElement;
const btnClip = document.getElementById('btnClip') as HTMLButtonElement;
const btnSave = document.getElementById('btnSave') as HTMLButtonElement;
const queuePopup = document.querySelector('.queue-popup') as HTMLDivElement;

btnPlayPause.onclick = () => callApi(isPlaying ? "/pause" : "/resume");
btnStop.onclick = () => callApi("/stop");
btnPrev.onclick = () => { callApi("/seek", { direction: -1 }); isPlaying = true; btnPlayPause.innerHTML = ICONS.pause; btnPlayPause.classList.add('active'); };
btnNext.onclick = () => { callApi("/seek", { direction: 1 }); isPlaying = true; btnPlayPause.innerHTML = ICONS.pause; btnPlayPause.classList.add('active'); };

btnClip.onclick = async () => {
  const res = await browser.runtime.sendMessage({ type: "READ_CLIPBOARD" });
  if (res?.error) showToast("❌ 剪切板为空"); else showToast("📋 正在朗读剪切板...");
};

btnSave.onclick = async () => {
  // Using active tab injection to try saving selection first
  try {
    const tabs = await browser.tabs.query({ active: true, currentWindow: true });
    if (tabs[0]?.id) {
      const results = await browser.scripting.executeScript({
        target: { tabId: tabs[0].id },
        func: () => window.getSelection()?.toString().trim() || ""
      });
      const sel = results[0]?.result;
      if (sel) {
        const res = await callApi("/save_for_later", { text: sel });
        if (!res?.error) { showToast("✨ 已存入选中内容"); renderQueue(); return; }
      }
    }
  } catch(e) {}
  
  // Fallback to saving current article
  const res = await callApi("/save_current");
  if (!res?.error) { showToast("💾 已保存当前文章"); renderQueue(); }
  else showToast("💡 请先划选或在朗读时点击");
};

function showToast(msg: string) {
  const container = document.getElementById('toastContainer')!;
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg;
  container.appendChild(t); setTimeout(() => t.remove(), 3000);
}

async function renderQueue() {
  const items = await callApi("/saved_items", null, "GET");
  const listArr = Array.isArray(items) ? items : [];
  
  queuePopup.innerHTML = `<div class="queue-header">Saved · ${listArr.length}</div><div class="queue-list"></div>`;
  const list = queuePopup.querySelector('.queue-list')!;
  
  if (listArr.length === 0) {
    list.innerHTML = `<div class="empty-state"><div class="empty-main">还没有收藏内容</div><div class="empty-sub">在网页点击 🔖 即可保存</div></div>`;
    return;
  }

  listArr.forEach((item, i) => {
    const row = document.createElement('div');
    row.className = 'queue-item';
    row.innerHTML = `<input type="checkbox" checked data-idx="${i}"><span class="queue-item-text" title="${item.text}">${item.title}</span><span class="queue-item-time">${Math.ceil(item.text.length / 200)}m</span><button class="btn-del" title="删除">${ICONS.trash}</button>`;
    row.onclick = (e) => {
      if ((e.target as HTMLElement).closest('.btn-del')) { e.stopPropagation(); callApi("/delete_saved", { index: i }).then(renderQueue); return; }
      if (e.target instanceof HTMLInputElement) return;
      const cb = row.querySelector('input') as HTMLInputElement; cb.checked = !cb.checked;
    };
    list.appendChild(row);
  });

  const footer = document.createElement('div'); footer.className = 'queue-footer';
  const pBtn = document.createElement('button'); pBtn.className = 'btn-play-selected';
  pBtn.textContent = `▶ Play Selected`;
  pBtn.onclick = async () => {
    const idxs = Array.from(queuePopup.querySelectorAll('input:checked')).map(cb => parseInt((cb as HTMLElement).getAttribute('data-idx')!));
    if (idxs.length) { await callApi("/play_saved", { indices: idxs }); showToast("🎙️ 开始朗读队列..."); }
  };
  footer.appendChild(pBtn); queuePopup.appendChild(footer);
}

// Initial render
renderQueue();

// Sync initial status
browser.runtime.sendMessage({ type: "GET_LAST_STATE" }).then(state => {
  if (state) {
    isPlaying = state.is_playing;
    btnPlayPause.innerHTML = isPlaying ? ICONS.pause : ICONS.play;
    btnPlayPause.classList.toggle('active', isPlaying);
  }
});

// Update state on message
browser.runtime.onMessage.addListener((msg: any) => {
  if (msg.type === "QWEN_STATUS_UPDATE") {
    isPlaying = msg.data.is_playing;
    btnPlayPause.innerHTML = isPlaying ? ICONS.pause : ICONS.play;
    btnPlayPause.classList.toggle('active', isPlaying);
  }
});
