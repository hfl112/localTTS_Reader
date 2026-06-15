import './style.css';

const savedList = document.getElementById('savedList') as HTMLDivElement;
const podcastList = document.getElementById('podcastList') as HTMLDivElement;
const cacheList = document.getElementById('cacheList') as HTMLDivElement;

const btnPlayAllSaved = document.getElementById('btnPlayAllSaved') as HTMLButtonElement;
const btnGenPodcast = document.getElementById('btnGenPodcast') as HTMLButtonElement;
const btnRefreshPodcasts = document.getElementById('btnRefreshPodcasts') as HTMLButtonElement;
const btnRefresh = document.getElementById('btnRefresh') as HTMLButtonElement;
const btnClearCache = document.getElementById('btnClearCache') as HTMLButtonElement;
const btnPlayToggle = document.getElementById('btnPlayToggle') as HTMLButtonElement;
const btnStop = document.getElementById('btnStop') as HTMLButtonElement;
const btnReadClipboard = document.getElementById('btnReadClipboard') as HTMLButtonElement;
const btnSaveForLater = document.getElementById('btnSaveForLater') as HTMLButtonElement;
const txtTargetUrl = document.getElementById('txtTargetUrl') as HTMLInputElement;
const selLangMode = document.getElementById('selLangMode') as HTMLSelectElement;

// Helper: Call Backend API through Background script
const callBackend = async (endpoint: string, method: string = "GET", data: any = null) => {
  return browser.runtime.sendMessage({
    type: "QWEN_COMMAND",
    endpoint,
    method,
    data
  });
};

// ==========================================
// 2. 最近收藏 (Saved Items)
// ==========================================
const fetchSavedItems = async () => {
  savedList.innerHTML = '<div class="loading-state">加载中...</div>';
  try {
    const response = await callBackend("/saved_items");
    if (response && !response.error && Array.isArray(response)) {
      renderSavedList(response);
    } else {
      savedList.innerHTML = '<div class="empty-state">暂无收藏内容</div>';
    }
  } catch (err) {
    savedList.innerHTML = '<div class="empty-state">连接后端失败</div>';
  }
};

const estimateReadingTime = (text: string): string => {
  if (!text) return "0秒";
  
  // Count Chinese characters
  const chineseChars = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
  
  // Count English words
  const englishText = text.replace(/[\u4e00-\u9fa5]/g, ' ');
  const englishWords = englishText.split(/\s+/).filter(w => w.trim().length > 0).length;
  
  // Estimate total seconds:
  // Chinese: ~250 chars / minute
  // English: ~150 words / minute
  const totalSeconds = Math.ceil((chineseChars / 250 + englishWords / 150) * 60);
  if (totalSeconds < 60) {
    return `~${totalSeconds}s`;
  }
  
  const minutes = Math.round(totalSeconds / 60);
  if (minutes < 60) {
    return `~${minutes}min`;
  }
  
  const hours = Math.round(totalSeconds / 3600);
  return `~${hours}hr`;
};

const renderSavedList = (items: any[]) => {
  if (items.length === 0) {
    savedList.innerHTML = '<div class="empty-state">暂无收藏内容</div>';
    return;
  }
  savedList.innerHTML = '';
  items.forEach((item, index) => {
    const itemEl = document.createElement('div');
    itemEl.className = 'cache-item';
    
    // 根据来源 source 生成指示标签
    let sourceTag = "";
    if (item.source === "video") {
      sourceTag = `<span style="color: #3b82f6; font-weight: bold; margin-right: 4px;">[视频]</span>`;
    } else if (item.source === "web") {
      sourceTag = `<span style="color: #8b5cf6; font-weight: bold; margin-right: 4px;">[网页]</span>`;
    } else if (item.source === "clipboard") {
      sourceTag = `<span style="color: #10b981; font-weight: bold; margin-right: 4px;">[剪贴]</span>`;
    } else {
      const isUrl = item.text.trim().startsWith("http://") || item.text.trim().startsWith("https://");
      if (isUrl) {
        sourceTag = `<span style="color: #8b5cf6; font-weight: bold; margin-right: 4px;">[网页]</span>`;
      } else {
        sourceTag = `<span style="color: #10b981; font-weight: bold; margin-right: 4px;">[剪贴]</span>`;
      }
    }

    // Snip text to fit
    let displayTitle = item.title || item.text.trim();
    if (displayTitle.length > 28) displayTitle = displayTitle.slice(0, 26) + '...';

    let actionsHtml = '';
    if (item.is_pending) {
      actionsHtml = `<span style="font-size: 11px; color: #f59e0b; font-weight: bold; padding: 4px 8px; background: rgba(245, 158, 11, 0.1); border-radius: 4px; animation: pulse 1.5s infinite;">⏳ 抓取中...</span>`;
    } else {
      actionsHtml = `
        <button class="btn-play action-icon-btn action-icon-btn-secondary" title="朗读">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>
        </button>
        <button class="btn-gen-podcast action-icon-btn action-icon-btn-secondary" title="单篇合成播客">
           <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
        </button>
        <button class="btn-delete action-icon-btn" title="删除">
          <svg viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" fill="none" stroke="currentColor" stroke-width="2"/></svg>
        </button>
      `;
    }

    itemEl.innerHTML = `
      <div class="cache-info" style="${item.is_pending ? 'opacity: 0.7;' : ''}">
        <div class="cache-text" title="${item.text}">${sourceTag}${displayTitle}</div>
        <div class="cache-meta">
          <span>${item.is_pending ? '等待完成' : new Date(item.timestamp * 1000).toLocaleString()}</span>
          ${item.is_pending ? '' : `<span>·</span><span>${estimateReadingTime(item.text)}</span>`}
        </div>
      </div>
      <div class="cache-actions" style="display: flex; gap: 4px;">
        ${actionsHtml}
      </div>
    `;

    if (!item.is_pending) {
      // Bind click on text area to play
      const textDiv = itemEl.querySelector('.cache-info') as HTMLDivElement;
      textDiv.style.cursor = 'pointer';
      textDiv.onclick = async () => {
        try {
          const res = await callBackend("/play_saved", "POST", { indices: [index] });
          if (res && res.error) alert(`❌ 播放失败: ${res.error}`);
        } catch {
          alert('❌ 连接失败');
        }
      };

      // Bind play
      const btnPlay = itemEl.querySelector('.btn-play') as HTMLButtonElement;
      if (btnPlay) {
        btnPlay.onclick = async () => {
          btnPlay.style.opacity = '0.5';
          btnPlay.disabled = true;
          try {
            const indicesToPlay = Array.from({ length: items.length - index }, (_, i) => index + i);
            const res = await callBackend("/play_saved", "POST", { indices: indicesToPlay });
            if (res && res.error) alert(`❌ 播放失败: ${res.error}`);
          } catch {
            alert('❌ 连接失败');
          } finally {
            btnPlay.style.opacity = '1';
            btnPlay.disabled = false;
          }
        };
      }

      // Bind single podcast generation
      const btnGenPodcastItem = itemEl.querySelector('.btn-gen-podcast') as HTMLButtonElement;
      if (btnGenPodcastItem) {
        btnGenPodcastItem.onclick = async () => {
          btnGenPodcastItem.style.opacity = '0.5';
          btnGenPodcastItem.disabled = true;
          try {
            const res = await callBackend("/generate_single_podcast", "POST", { text: item.text, source: item.source });
            if (res && !res.error) {
              fetchPodcasts();
            } else {
              alert(`❌ 生成失败: ${res?.error || '未知错误'}`);
            }
          } catch {
            alert('❌ 连接失败');
          } finally {
            btnGenPodcastItem.style.opacity = '1';
            btnGenPodcastItem.disabled = false;
          }
        };
      }

      // Bind delete
      const btnDelete = itemEl.querySelector('.btn-delete') as HTMLButtonElement;
      if (btnDelete) {
        btnDelete.onclick = async () => {
          try {
            const res = await callBackend("/delete_saved", "POST", { md5: item.md5, index });
            if (res && !res.error) {
              fetchSavedItems();
            }
          } catch {
            alert('❌ 删除失败');
          }
        };
      }
    }

    savedList.appendChild(itemEl);
  });
};


btnGenPodcast.onclick = async () => {
  btnGenPodcast.disabled = true;
  btnGenPodcast.style.opacity = '0.5';
  try {
    const res = await callBackend("/generate_podcast", "POST");
    if (res && !res.error) {
      fetchSavedItems();
      fetchPodcasts();
    } else {
      alert(`❌ 生成失败: ${res?.error || '未知错误'}`);
    }
  } catch {
    alert('❌ 连接后端失败');
  } finally {
    btnGenPodcast.disabled = false;
    btnGenPodcast.style.opacity = '1';
  }
};

const btnRefreshSaved = document.getElementById('btnRefreshSaved') as HTMLButtonElement;
if (btnRefreshSaved) btnRefreshSaved.onclick = fetchSavedItems;

const btnClearSaved = document.getElementById('btnClearSaved') as HTMLButtonElement;
if (btnClearSaved) {
  btnClearSaved.onclick = async () => {
    if (confirm('确认清空所有稍后朗读的内容吗？')) {
      try {
        const res = await callBackend("/saved_items/clear", "POST");
        if (res && !res.error) fetchSavedItems();
        else alert(`❌ 清空失败: ${res?.error}`);
      } catch {
        alert('❌ 连接后端失败');
      }
    }
  };
}

// ==========================================
// 3. 我的播客 (Generated Podcasts)
// ==========================================
const fetchPodcasts = async () => {
  podcastList.innerHTML = '<div class="loading-state">加载中...</div>';
  try {
    const response = await callBackend("/podcasts/list");
    if (response && !response.error && Array.isArray(response)) {
      renderPodcastList(response);
    } else {
      podcastList.innerHTML = '<div class="empty-state">暂无生成播客</div>';
    }
  } catch (err) {
    podcastList.innerHTML = '<div class="empty-state">连接后端失败</div>';
  }
};

const renderPodcastList = (items: any[]) => {
  if (items.length === 0) {
    podcastList.innerHTML = '<div class="empty-state">暂无生成播客</div>';
    return;
  }
  podcastList.innerHTML = '';
  items.forEach((item) => {
    const itemEl = document.createElement('div');
    itemEl.className = 'cache-item';
    itemEl.setAttribute('data-filename', item.filename);
    
    // 根据来源 source 生成指示标签
    let sourceTag = "";
    if (item.source === "video") {
      sourceTag = `<span style="color: #3b82f6; font-weight: bold; margin-right: 4px;">[视频]</span>`;
    } else if (item.source === "web") {
      sourceTag = `<span style="color: #8b5cf6; font-weight: bold; margin-right: 4px;">[网页]</span>`;
    } else if (item.source === "clipboard") {
      sourceTag = `<span style="color: #10b981; font-weight: bold; margin-right: 4px;">[剪贴]</span>`;
    } else if (item.source === "podcast") {
      sourceTag = `<span style="color: #f59e0b; font-weight: bold; margin-right: 4px;">[播客]</span>`;
    }
    
    // 处理显示标题
    let displayTitle = "";
    if (item.title && item.title !== (item.filename || item.title)) {
      displayTitle = item.title.trim();
      if (displayTitle.length > 28) displayTitle = displayTitle.slice(0, 26) + '...';
    } else {
      const filename = item.filename || item.title;
      const tsMatch = filename.match(/_(\d+)\.wav/);
      const fallbackTs = tsMatch ? tsMatch[1] : (filename.match(/podcast_(\d+)\.wav/)?.[1]);
      if (fallbackTs) {
        const date = new Date(parseInt(fallbackTs) * 1000);
        displayTitle = `播客 · ${date.toLocaleDateString()} ${date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}`;
      } else {
        displayTitle = filename;
      }
    }

    let actionsHtml = '';
    if (item.is_pending) {
      actionsHtml = `<span style="font-size: 11px; color: #f59e0b; font-weight: bold; padding: 4px 8px; background: rgba(245, 158, 11, 0.1); border-radius: 4px; animation: pulse 1.5s infinite;">⏳ 生成中...</span>`;
    } else {
      actionsHtml = `
        <button class="btn-pin action-icon-btn action-icon-btn-secondary" title="${item.is_pinned ? '取消置顶' : '置顶播客'}">
          ${item.is_pinned ? 
            `<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" style="color: #f59e0b;"><path d="M16 11V5.5A2.5 2.5 0 0 0 13.5 3h-3A2.5 2.5 0 0 0 8 5.5V11L6 14v1.5h5V21l1 1 1-1v-5.5h5V14l-2-3z" /></svg>` : 
            `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 11V5.5A2.5 2.5 0 0 0 13.5 3h-3A2.5 2.5 0 0 0 8 5.5V11L6 14v1.5h5V21l1 1 1-1v-5.5h5V14l-2-3z" /></svg>`
          }
        </button>
        <button class="btn-play action-icon-btn action-icon-btn-secondary" title="本地系统播放">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3" /></svg>
        </button>
        <button class="btn-delete action-icon-btn" title="删除文件">
          <svg viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" fill="none" stroke="currentColor" stroke-width="2"/></svg>
        </button>
      `;
    }

    itemEl.innerHTML = `
      <div class="cache-info" style="${item.is_pending ? 'opacity: 0.7;' : ''} ${item.is_pinned ? 'border-left: 3px solid #f59e0b; padding-left: 8px;' : ''}">
        <div class="cache-text" title="${item.title || item.filename}">${sourceTag}${displayTitle}</div>
        <div class="cache-meta">
          <span>${item.is_pending ? '等待完成...' : `大小: ${(item.size_mb || 0).toFixed(2)} MB`}</span>
        </div>
      </div>
      <div class="cache-actions" style="display: flex; gap: 4px;">
        ${actionsHtml}
      </div>
    `;

    if (!item.is_pending) {
      // Bind pin
      const btnPin = itemEl.querySelector('.btn-pin') as HTMLButtonElement;
      if (btnPin) {
        btnPin.onclick = async () => {
          try {
            const res = await callBackend("/podcasts/toggle_pin", "POST", { filename: item.filename });
            if (res && !res.error) fetchPodcasts();
            else alert(`❌ 置顶操作失败: ${res?.error}`);
          } catch {
            alert('❌ 连接失败');
          }
        };
      }
      // Bind play
      const btnPlay = itemEl.querySelector('.btn-play') as HTMLButtonElement;
      if (btnPlay) {
        btnPlay.onclick = async () => {
          btnPlay.style.opacity = '0.5';
          btnPlay.disabled = true;
          try {
            if (currentPlayingPodcastFile === item.filename) {
              if (localIsPlaying && !localIsPaused) {
                await callBackend("/pause", "POST");
                localIsPaused = true;
              } else {
                await callBackend("/resume", "POST");
                localIsPaused = false;
              }
              updatePlayToggleUI();
              updatePodcastListUI();
            } else {
              const res = await callBackend("/podcasts/play", "POST", { filename: item.filename });
              if (res && res.error) {
                alert(`❌ 播放失败: ${res.error}`);
              } else {
                currentPlayingPodcastFile = item.filename;
                localIsPlaying = true;
                localIsPaused = false;
                updatePlayToggleUI();
                updatePodcastListUI();
              }
            }
          } catch {
            alert('❌ 操作失败');
          } finally {
            btnPlay.style.opacity = '1';
            btnPlay.disabled = false;
          }
        };
      }

      // Bind delete
      const btnDelete = itemEl.querySelector('.btn-delete') as HTMLButtonElement;
      if (btnDelete) {
        btnDelete.onclick = async () => {
          if (confirm('确认删除该播客文件？')) {
            try {
              const res = await callBackend("/podcasts/delete", "POST", { filename: item.filename });
              if (res && !res.error) {
                itemEl.remove();
                if (podcastList.children.length === 0) {
                  podcastList.innerHTML = '<div class="empty-state">暂无生成播客</div>';
                }
              }
            } catch {
              alert('❌ 删除失败');
            }
          }
        };
      }
    }

    podcastList.appendChild(itemEl);
  });
  updatePodcastListUI();
};

btnRefreshPodcasts.onclick = fetchPodcasts;

const btnClearPodcasts = document.getElementById('btnClearPodcasts') as HTMLButtonElement;
if (btnClearPodcasts) {
  btnClearPodcasts.onclick = async () => {
    if (confirm('确认清空所有非置顶的播客文件吗？')) {
      try {
        const res = await callBackend("/podcasts/clear", "POST");
        if (res && !res.error) fetchPodcasts();
        else alert(`❌ 清空失败: ${res?.error}`);
      } catch {
        alert('❌ 连接后端失败');
      }
    }
  };
}

// ==========================================
// 4. 本地音频缓存 (SQLite)
// ==========================================
const fetchCaches = async () => {
  cacheList.innerHTML = '<div class="loading-state">加载中...</div>';
  try {
    const response = await callBackend("/cache/items");
    if (response && !response.error && Array.isArray(response)) {
      renderCacheList(response);
    } else {
      cacheList.innerHTML = '<div class="empty-state">暂无缓存元数据</div>';
    }
  } catch (err) {
    cacheList.innerHTML = '<div class="empty-state">无法连接到本地后端</div>';
  }
};

const renderCacheList = (items: any[]) => {
  if (items.length === 0) {
    cacheList.innerHTML = '<div class="empty-state">暂无本地缓存音频</div>';
    return;
  }

  cacheList.innerHTML = '';
  items.forEach((item) => {
    const itemEl = document.createElement('div');
    itemEl.className = 'cache-item';

    const dateStr = new Date(item.created_at * 1000).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric'
    });

    const actionBtnHtml = item.is_exported
      ? `<button class="btn-play" title="播放音频" data-md5="${item.md5}">
           <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3" fill="currentColor" stroke="none"/></svg>
         </button>`
      : `<button class="btn-export" title="导出为播客 (WAV)" data-md5="${item.md5}">
           <svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
         </button>`;

    // Snip text to fit
    let displayTitle = item.text.trim();
    if (displayTitle.length > 25) displayTitle = displayTitle.slice(0, 22) + '...';

    itemEl.innerHTML = `
      <div class="cache-info">
        <div class="cache-text" title="${item.text}">${displayTitle}</div>
        <div class="cache-meta">
          <span>${item.voice}</span>
          <span>·</span>
          <span>${item.duration.toFixed(1)}s</span>
          <span>·</span>
          <span>${dateStr}</span>
        </div>
      </div>
      <div class="cache-actions">
        ${actionBtnHtml}
        <button class="btn-delete" title="删除" data-md5="${item.md5}">
          <svg viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
        </button>
      </div>
    `;

    const bindPlayEvent = (btn: HTMLButtonElement, md5: string) => {
      btn.onclick = async () => {
        btn.style.opacity = '0.5';
        btn.disabled = true;
        try {
          const res = await callBackend("/cache/play", "POST", { md5: md5 });
          if (res && res.error) {
            alert(`❌ 播放失败: ${res.error}`);
          }
        } catch {
          alert('❌ 播放连接失败');
        } finally {
          btn.style.opacity = '1';
          btn.disabled = false;
        }
      };
    };

    const bindExportEvent = (btn: HTMLButtonElement, md5: string) => {
      btn.onclick = async () => {
        btn.style.opacity = '0.5';
        btn.disabled = true;
        try {
          const res = await callBackend("/cache/export", "POST", { md5: md5 });
          if (res && !res.error) {
            const parent = btn.parentElement;
            if (parent) {
              const newBtn = document.createElement('button');
              newBtn.className = 'btn-play';
              newBtn.title = '播放音频';
              newBtn.dataset.md5 = md5;
              newBtn.innerHTML = `<svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3" fill="currentColor" stroke="none"/></svg>`;
              parent.insertBefore(newBtn, btn);
              btn.remove();
              bindPlayEvent(newBtn, md5);
            }
          } else {
            alert(`❌ 导出失败: ${res.error || '未知错误'}`);
            btn.style.opacity = '1';
            btn.disabled = false;
          }
        } catch {
          alert('❌ 导出连接失败');
          btn.style.opacity = '1';
          btn.disabled = false;
        }
      };
    };

    const btnExport = itemEl.querySelector('.btn-export') as HTMLButtonElement;
    if (btnExport) {
      bindExportEvent(btnExport, item.md5);
    }

    const btnPlay = itemEl.querySelector('.btn-play') as HTMLButtonElement;
    if (btnPlay) {
      bindPlayEvent(btnPlay, item.md5);
    }

    const btnDelete = itemEl.querySelector('.btn-delete') as HTMLButtonElement;
    btnDelete.onclick = async () => {
      if (confirm('确认删除该段音频缓存？')) {
        try {
          const res = await callBackend("/cache/delete", "POST", { md5: item.md5 });
          if (res && !res.error) {
            itemEl.remove();
            if (cacheList.children.length === 0) {
              cacheList.innerHTML = '<div class="empty-state">暂无本地缓存音频</div>';
            }
          }
        } catch {
          alert('❌ 删除失败');
        }
      }
    };

    cacheList.appendChild(itemEl);
  });
};

btnRefresh.onclick = fetchCaches;

btnClearCache.onclick = async () => {
  if (confirm('警告：确认清空数据库及本地所有音频缓存？该操作不可逆！')) {
    try {
      const res = await callBackend("/cache/clear", "POST");
      if (res && !res.error) {
        fetchCaches();
      }
    } catch {
      alert('❌ 清空失败');
    }
  }
};

// 自动获取并填充当前活动标签页的 URL
const autoFillCurrentUrl = async () => {
  let tabs;
  try {
    tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  } catch (err) {
    try {
      tabs = await browser.tabs.query({ active: true, currentWindow: true });
    } catch (e) {
      return;
    }
  }
  const activeTab = tabs && tabs[0];
  if (activeTab && activeTab.url) {
    const url = activeTab.url;
    if (!url.startsWith("chrome://") && !url.startsWith("about:") && !url.startsWith("edge://")) {
      txtTargetUrl.value = url;
    }
  }
};

// 状态缓存变量 (提升到前面)
let localIsPaused = false;
let localIsPlaying = false;
let currentPlayingPodcastFile: string | null = null;

// 动态更新播放/暂停按钮的 SVG 图标与 title
const updatePlayToggleUI = () => {
  if (!btnPlayToggle) return;
  if (localIsPlaying && !localIsPaused) {
    // 正在播放中，显示为暂停图标
    btnPlayToggle.innerHTML = `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round"><rect x="5" y="4" width="4" height="16" rx="1"/><rect x="15" y="4" width="4" height="16" rx="1"/></svg>`;
    btnPlayToggle.title = "暂停播放";
  } else if (localIsPlaying && localIsPaused) {
    // 已暂停，显示为播放（恢复）图标
    btnPlayToggle.innerHTML = `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg>`;
    btnPlayToggle.title = "恢复播放";
  } else {
    // IDLE 状态，显示为播放图标，对应朗读当前 URL
    btnPlayToggle.innerHTML = `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg>`;
    btnPlayToggle.title = "直接朗读当前 URL";
  }
};

// 动态更新播客列表中每一项的播放/暂停按钮图标
const updatePodcastListUI = () => {
  if (!podcastList) return;
  const items = podcastList.querySelectorAll('.cache-item');
  items.forEach((itemEl) => {
    const filename = itemEl.getAttribute('data-filename');
    const btnPlay = itemEl.querySelector('.btn-play') as HTMLButtonElement;
    if (!btnPlay) return;
    
    if (currentPlayingPodcastFile && currentPlayingPodcastFile === filename) {
      if (localIsPlaying && !localIsPaused) {
        // 正在播放中，显示为暂停图标
        btnPlay.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>`;
        btnPlay.title = "暂停播放";
      } else {
        // 已暂停，显示为播放（恢复）图标
        btnPlay.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3" /></svg>`;
        btnPlay.title = "恢复播放";
      }
    } else {
      // 其他播客，全部显示为播放图标
      btnPlay.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3" /></svg>`;
      btnPlay.title = "本地系统播放";
    }
  });
};

// 绑定一键朗读当前网页 / 暂停 / 恢复事件
btnPlayToggle.onclick = async () => {
  btnPlayToggle.disabled = true;
  btnPlayToggle.style.opacity = '0.5';

  try {
    if (localIsPlaying && !localIsPaused) {
      // 当前正在播放，执行暂停
      await callBackend("/pause", "POST");
      localIsPaused = true;
      updatePlayToggleUI();
    } else if (localIsPlaying && localIsPaused) {
      // 当前已暂停，执行恢复
      await callBackend("/resume", "POST");
      localIsPaused = false;
      updatePlayToggleUI();
    } else {
      // 空闲状态，执行原本的朗读当前 URL 逻辑
      const url = txtTargetUrl.value.trim();
      if (!url) {
        alert("❌ 请输入或粘贴要朗读的网页 URL 链接！");
        return;
      }

      if (url.startsWith("chrome://") || url.startsWith("about:") || url.startsWith("edge://")) {
        alert("❌ 无法朗读浏览器系统级页面");
        return;
      }

      const mode = selLangMode.value;
      const translate = mode !== "original";
      const originalPlaceholder = txtTargetUrl.placeholder;
      txtTargetUrl.placeholder = "⏳ 正在推送网页朗读任务...";

      const res = await callBackend("/read_url", "POST", { url, translate, mode });
      if (res && (res.error || res.status === "error")) {
        alert(`❌ 朗读推送失败: ${res.error || res.message}`);
        txtTargetUrl.placeholder = originalPlaceholder;
      } else {
        txtTargetUrl.placeholder = "✔ 已成功推送朗读任务！";
        setTimeout(() => {
          txtTargetUrl.placeholder = originalPlaceholder;
        }, 2000);
      }
    }
  } catch (err) {
    alert("❌ 操作失败或无法连接到 QwenTTS 后端");
  } finally {
    btnPlayToggle.disabled = false;
    btnPlayToggle.style.opacity = '1';
  }
};

// 绑定朗读剪贴板事件
btnReadClipboard.onclick = async () => {
  btnReadClipboard.disabled = true;
  btnReadClipboard.style.opacity = '0.5';
  
  const originalPlaceholder = txtTargetUrl.placeholder;
  txtTargetUrl.placeholder = "⏳ 正在读取剪贴板并推送...";

  try {
    const text = await navigator.clipboard.readText();
    const trimmedText = text.trim();
    if (!trimmedText) {
      alert("❌ 剪贴板中没有文本内容！");
      txtTargetUrl.placeholder = originalPlaceholder;
      return;
    }
    const res = await callBackend("/read", "POST", { text: trimmedText, source: "clipboard" });
    if (res && res.error) {
      alert(`❌ 朗读推送失败: ${res.error}`);
      txtTargetUrl.placeholder = originalPlaceholder;
    } else {
      txtTargetUrl.placeholder = "✔ 剪贴板文本推送并备份成功！";
      
      // 异步在后台保存备份，自动触发播客生成
      try {
        await callBackend("/save_for_later", "POST", { text: trimmedText, source: "clipboard" });
        fetchSavedItems(); // 刷新稍后朗读列表
      } catch (saveErr) {
        console.error("Clipboard backup failed", saveErr);
      }

      setTimeout(() => {
        txtTargetUrl.placeholder = originalPlaceholder;
      }, 2000);
    }
  } catch (err) {
    alert("❌ 无法读取剪贴板，请确保已授予剪贴板访问权限，或后端连接失败！");
    txtTargetUrl.placeholder = originalPlaceholder;
  } finally {
    btnReadClipboard.disabled = false;
    btnReadClipboard.style.opacity = '1';
  }
};

// 绑定网页保存为稍后朗读（最近收藏）事件
btnSaveForLater.onclick = async () => {
  const url = txtTargetUrl.value.trim();
  if (!url) {
    alert("❌ 请输入或粘贴要保存的网页 URL 链接！");
    return;
  }

  // 过滤浏览器内部特殊页面
  if (url.startsWith("chrome://") || url.startsWith("about:") || url.startsWith("edge://")) {
    alert("❌ 无法保存浏览器系统级页面");
    return;
  }

  const mode = selLangMode.value;
  const translate = mode !== "original";

  btnSaveForLater.disabled = true;
  btnSaveForLater.style.opacity = '0.5';
  
  const originalPlaceholder = txtTargetUrl.placeholder;
  txtTargetUrl.placeholder = "⏳ 正在抓取并保存至稍后朗读...";

  try {
    const res = await callBackend("/read_url", "POST", { url, translate, mode, save: true });
    if (res && (res.error || res.status === "error")) {
      alert(`❌ 保存失败: ${res.error || res.message}`);
      txtTargetUrl.placeholder = originalPlaceholder;
    } else {
      txtTargetUrl.placeholder = "✔ 已成功保存至稍后朗读！";
      setTimeout(() => {
        txtTargetUrl.placeholder = originalPlaceholder;
        fetchSavedItems(); // 刷新收藏列表
      }, 2000);
    }
  } catch (err) {
    alert("❌ 无法连接到 QwenTTS 后端");
    txtTargetUrl.placeholder = originalPlaceholder;
  } finally {
    btnSaveForLater.disabled = false;
    btnSaveForLater.style.opacity = '1';
  }
};

const btnSaveAndPodcast = document.getElementById('btnSaveAndPodcast') as HTMLButtonElement;
if (btnSaveAndPodcast) {
  btnSaveAndPodcast.onclick = async () => {
    const url = txtTargetUrl.value.trim();
    if (!url) {
      alert("❌ 请输入或粘贴要保存并生成播客的网页 URL 链接！");
      return;
    }

    if (url.startsWith("chrome://") || url.startsWith("about:") || url.startsWith("edge://")) {
      alert("❌ 无法保存浏览器系统级页面");
      return;
    }

    const mode = selLangMode.value;
    const translate = mode !== "original";

    btnSaveAndPodcast.disabled = true;
    btnSaveAndPodcast.style.opacity = '0.5';
    
    const originalPlaceholder = txtTargetUrl.placeholder;
    txtTargetUrl.placeholder = "⏳ 正在保存并生成播客...";

    try {
      const res = await callBackend("/read_url", "POST", { url, translate, mode, podcast: true });
      if (res && (res.error || res.status === "error")) {
        alert(`❌ 保存并生成失败: ${res.error || res.message}`);
        txtTargetUrl.placeholder = originalPlaceholder;
      } else {
        txtTargetUrl.placeholder = "✔ 已成功保存并在后台生成播客！";
        setTimeout(() => {
          txtTargetUrl.placeholder = originalPlaceholder;
          fetchSavedItems();
          fetchPodcasts();
        }, 2000);
      }
    } catch (err) {
      alert("❌ 无法连接到 QwenTTS 后端");
      txtTargetUrl.placeholder = originalPlaceholder;
    } finally {
      btnSaveAndPodcast.disabled = false;
      btnSaveAndPodcast.style.opacity = '1';
    }
  };
}

// 绑定停止播放事件
btnStop.onclick = async () => {
  btnStop.disabled = true;
  btnStop.style.opacity = '0.5';
  try {
    await callBackend("/stop", "POST");
    localIsPlaying = false;
    localIsPaused = false;
    updatePlayToggleUI();
  } catch (err) {
    console.error("Stop failed", err);
  } finally {
    btnStop.disabled = false;
    btnStop.style.opacity = '1';
  }
};

// 定时 1Hz 轮询更新状态
const startStatusPolling = () => {
  setInterval(async () => {
    try {
      const res = await callBackend("/status");
      if (res && !res.error) {
        let changed = false;
        if (localIsPaused !== res.is_paused) {
          localIsPaused = res.is_paused;
          changed = true;
        }
        if (localIsPlaying !== res.is_playing) {
          localIsPlaying = res.is_playing;
          changed = true;
        }
        if (currentPlayingPodcastFile !== (res.current_podcast_file || null)) {
          currentPlayingPodcastFile = res.current_podcast_file || null;
          changed = true;
        }
        if (changed) {
          updatePlayToggleUI();
          updatePodcastListUI();
        }
      }
    } catch {}
  }, 1000);
};

// Initial Load All Lists
const loadAll = () => {
  autoFillCurrentUrl();
  fetchSavedItems();
  fetchPodcasts();
  fetchCaches();
  startStatusPolling();
};

loadAll();
