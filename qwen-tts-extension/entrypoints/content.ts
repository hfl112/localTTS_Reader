export default defineContentScript({
  matches: ["<all_urls>"],
  main() {
    console.log("Qwen App Remote Controller Loaded");

    browser.runtime.onMessage.addListener((message) => {
      if (message.type === "TTS_ERROR") {
        alert(message.error);
      }
    });

    // Inject Shadow DOM for UI isolation
    const container = document.createElement('div');
    container.style.position = 'fixed';
    container.style.bottom = '20px';
    container.style.right = '20px';
    container.style.zIndex = '999999';
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
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        padding: 4px;
        transition: all 0.3s ease;
        overflow: hidden;
      }
      .fab-icon {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        background: #4a4a4a;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        color: white;
        font-weight: bold;
        flex-shrink: 0;
      }
      .fab-icon:hover { background: #5a5a5a; }
      .controls {
        display: flex;
        align-items: center;
        width: 0;
        opacity: 0;
        transition: all 0.3s ease;
        pointer-events: none;
      }
      .fab-container.expanded .controls {
        width: 200px;
        opacity: 1;
        pointer-events: auto;
        padding-left: 10px;
      }
      button {
        background: none;
        border: none;
        color: white;
        font-size: 16px;
        cursor: pointer;
        padding: 5px 10px;
        border-radius: 4px;
      }
      button:hover { background: rgba(255,255,255,0.1); }
      .separator {
        width: 1px;
        height: 20px;
        background: rgba(255,255,255,0.2);
        margin: 0 5px;
      }
    `;
    shadow.appendChild(style);

    // UI Structure
    const wrapper = document.createElement('div');
    wrapper.className = 'fab-container';
    
    const iconBtn = document.createElement('div');
    iconBtn.className = 'fab-icon';
    iconBtn.textContent = 'Q';
    
    const controls = document.createElement('div');
    controls.className = 'controls';

    const btnPrev = document.createElement('button');
    btnPrev.innerHTML = '⏮';
    btnPrev.title = 'Previous Sentence';

    const btnPlayPause = document.createElement('button');
    btnPlayPause.innerHTML = '⏯';
    btnPlayPause.title = 'Play/Pause';

    const btnNext = document.createElement('button');
    btnNext.innerHTML = '⏭';
    btnNext.title = 'Next Sentence';

    const btnStop = document.createElement('button');
    btnStop.innerHTML = '⏹';
    btnStop.title = 'Stop';

    const sep = document.createElement('div');
    sep.className = 'separator';

    const btnSave = document.createElement('button');
    btnSave.innerHTML = '💾';
    btnSave.title = 'Save Selection for Later';

    const btnPodcast = document.createElement('button');
    btnPodcast.innerHTML = '🎙️';
    btnPodcast.title = 'Generate Podcast from Saved Items';

    controls.append(btnPrev, btnPlayPause, btnNext, btnStop, sep, btnSave, btnPodcast);
    wrapper.append(iconBtn, controls);
    shadow.appendChild(wrapper);

    // State & Logic
    let isExpanded = false;
    let isPlaying = false;
    const API_URL = "http://127.0.0.1:8001";

    iconBtn.onclick = () => {
      isExpanded = !isExpanded;
      wrapper.classList.toggle('expanded', isExpanded);
    };

    const callApi = async (endpoint: string, data?: any) => {
      try {
        await fetch(`${API_URL}${endpoint}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: data ? JSON.stringify(data) : undefined
        });
      } catch (err) {
        console.error("API call failed:", err);
      }
    };

    btnPlayPause.onclick = () => {
      callApi(isPlaying ? "/pause" : "/resume");
      isPlaying = !isPlaying; // Optimistic update
      btnPlayPause.innerHTML = isPlaying ? '⏸' : '▶️';
    };

    btnStop.onclick = () => { callApi("/stop"); };
    btnPrev.onclick = () => { callApi("/seek", { direction: -1 }); };
    btnNext.onclick = () => { callApi("/seek", { direction: 1 }); };
    
    btnSave.onclick = () => {
      const text = window.getSelection()?.toString();
      if (text) {
        callApi("/save_for_later", { text });
        alert("Saved for later!");
      } else {
        alert("Please select some text first.");
      }
    };

    btnPodcast.onclick = async () => {
      try {
        const res = await fetch(`${API_URL}/generate_podcast`, { method: 'POST' });
        const data = await res.json();
        if (data.error) {
          alert("Error: " + data.error);
        } else {
          alert("Podcast generation started in background! Check 'data/podcasts' folder later.");
        }
      } catch (err) {
        console.error("API call failed:", err);
      }
    };

    // Polling Status
    setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/status`);
        if (res.ok) {
          const data = await res.json();
          isPlaying = data.is_playing;
          btnPlayPause.innerHTML = isPlaying ? '⏸' : '▶️';
        }
      } catch (err) {
        // App probably offline, don't spam errors
      }
    }, 1000);
  },
});