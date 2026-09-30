"use strict";
// Service worker: owns the WebSocket to the local bridge, relays content-script events,
// forwards spoken output to the active tab (toast fallback), and toggles perception.
const BLOCKED = /(?:^|\.)(?:paypal|bankofamerica|chase|wellsfargo|hdfcbank|icicibank|sbi|axisbank|1password|lastpass|bitwarden)\./i;
let ws = null;
let backoff = 500;
let perception = true;
let queue = [];
async function settings() {
    const s = await chrome.storage.local.get({ url: "ws://127.0.0.1:8765/ws", token: "trail-dev" });
    return { url: s.url, token: s.token };
}
async function connect() {
    const { url, token } = await settings();
    const u = new URL(url);
    if (!["127.0.0.1", "localhost", "[::1]"].includes(u.hostname))
        return; // loopback only
    u.searchParams.set("client", "chrome");
    u.searchParams.set("token", token);
    const sock = new WebSocket(u.toString());
    ws = sock;
    sock.onopen = () => {
        backoff = 500;
        sock.send(JSON.stringify({ type: "hello", client: "chrome", version: 1, app: "chrome" }));
        for (const f of queue.splice(0))
            sock.send(f);
    };
    sock.onmessage = async (m) => {
        let frame;
        try {
            frame = JSON.parse(String(m.data));
        }
        catch {
            return;
        }
        const out = frame.type === "output" ? frame.output : null;
        if (out && ((out.type === "speak" && ["notice", "final", "clarify"].includes(out.kind)) || out.type === "speak_end")) {
            const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
            if (tab?.id != null)
                chrome.tabs.sendMessage(tab.id, { kind: "trail-output", output: frame.output }).catch(() => { });
        }
    };
    sock.onclose = () => {
        ws = null;
        setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 10000);
    };
}
function sendFrame(frame) {
    const data = JSON.stringify(frame);
    if (ws && ws.readyState === WebSocket.OPEN)
        ws.send(data);
    else if (queue.length < 50)
        queue.push(data);
}
chrome.runtime.onMessage.addListener((msg, sender) => {
    if (msg?.kind !== "trail-event" || !perception)
        return;
    const tab = sender?.tab;
    if (tab?.incognito)
        return;
    try {
        if (tab?.url && BLOCKED.test(new URL(tab.url).hostname))
            return; // auto-pause on banking / password managers
    }
    catch { /* ignore */ }
    const ev = msg.event;
    if (ev?.target?.sensitive)
        return; // belt and braces: never forward
    sendFrame({ type: "event", event: ev });
});
chrome.action.onClicked.addListener(async () => {
    perception = !perception;
    chrome.action.setBadgeText({ text: perception ? "ON" : "OFF" });
    chrome.action.setBadgeBackgroundColor({ color: perception ? "#3ddc97" : "#888" });
    sendFrame({ type: "control", action: "perception", on: perception });
    const tabs = await chrome.tabs.query({});
    for (const t of tabs)
        if (t.id != null)
            chrome.tabs.sendMessage(t.id, { kind: "trail-paused", paused: !perception }).catch(() => { });
});
chrome.runtime.onInstalled.addListener(() => chrome.action.setBadgeText({ text: "ON" }));
connect();
