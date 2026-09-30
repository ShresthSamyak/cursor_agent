// Narrow bridge between the Electron shell and the renderer (window.trailOverlay).
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("trailOverlay", {
  onCursor: cb => ipcRenderer.on("trail:cursor", (_e, p) => cb(p)),
  onHotkey: cb => ipcRenderer.on("trail:hotkey", (_e, name) => cb(name)),
  setInteractive: on => ipcRenderer.send("trail:interactive", !!on),
});
