export default defineContentScript({
  matches: ["<all_urls>"],
  main() {
    console.log("Qwen App Remote Controller Loaded");

    browser.runtime.onMessage.addListener((message) => {
      if (message.type === "TTS_ERROR") {
        alert(message.error);
      }
    });
  },
});
