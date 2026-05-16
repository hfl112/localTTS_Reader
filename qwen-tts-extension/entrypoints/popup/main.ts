import './style.css';

const toggleFab = document.getElementById('toggleFab') as HTMLInputElement;

// Toggle logic
browser.storage.local.get("hideFloatingBar").then((res) => {
  toggleFab.checked = !res.hideFloatingBar;
});

toggleFab.onchange = () => {
  browser.storage.local.set({ hideFloatingBar: !toggleFab.checked });
};
