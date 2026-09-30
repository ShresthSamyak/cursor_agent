const $ = (id: string) => document.getElementById(id) as HTMLInputElement;
chrome.storage.local.get({ url: "ws://127.0.0.1:8765/ws", token: "trail-dev" }).then((s: any) => {
  $("url").value = s.url;
  $("token").value = s.token;
});
document.getElementById("save")!.addEventListener("click", async () => {
  await chrome.storage.local.set({ url: $("url").value.trim(), token: $("token").value.trim() });
  document.getElementById("ok")!.textContent = "Saved. Reload the extension to reconnect.";
});
