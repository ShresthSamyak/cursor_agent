// Electron shell: a transparent, frameless, always-on-top, click-through window over the whole
// screen that loads the shared renderer (../renderer) and feeds it the OS cursor position.
// Hotkeys: Ctrl+Alt+T agent mode, Ctrl+Alt+P perception, Ctrl+Alt+Esc stop.

const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require("electron");
const path = require("path");

const token = process.env.TRAIL_TOKEN || "trail-dev";
const bridge = process.env.TRAIL_BRIDGE || "127.0.0.1:8765";
let win;

function create() {
  const { bounds } = screen.getPrimaryDisplay();
  win = new BrowserWindow({
    x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height,
    transparent: true, frame: false, resizable: false, movable: false, skipTaskbar: true,
    alwaysOnTop: true, hasShadow: false, focusable: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false },
  });
  win.setAlwaysOnTop(true, "screen-saver");
  win.setIgnoreMouseEvents(true, { forward: true });      // click-through; the panel toggles this
  win.loadFile(path.join(__dirname, "..", "renderer", "index.html"), { query: { token, bridge } });

  // ~30 Hz OS cursor feed, relative to the window.
  setInterval(() => {
    if (!win || win.isDestroyed()) return;
    const p = screen.getCursorScreenPoint();
    const b = win.getBounds();
    win.webContents.send("trail:cursor", { x: p.x - b.x, y: p.y - b.y });
  }, 33);

  const hot = (accel, name) => globalShortcut.register(accel, () => win.webContents.send("trail:hotkey", name));
  hot("Control+Alt+T", "agent");
  hot("Control+Alt+P", "perception");
  hot("Control+Alt+Escape", "cancel");
}

ipcMain.on("trail:interactive", (_e, on) => {
  if (win && !win.isDestroyed()) win.setIgnoreMouseEvents(!on, { forward: true });
});

app.whenReady().then(create);
app.on("will-quit", () => globalShortcut.unregisterAll());
app.on("window-all-closed", () => app.quit());
