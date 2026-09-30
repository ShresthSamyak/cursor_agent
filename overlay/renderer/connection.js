// WebSocket client for the Trail bridge (docs/bridge-protocol.md, "Connection").
// Reconnects with backoff, sends the hello frame first, never logs the token.

export const DEFAULT_TOKEN = "trail-dev";
export const DEFAULT_BRIDGE = "127.0.0.1:8765";

/**
 * Build ws://<bridge>/ws?client=overlay&token=<token>.
 * The bridge serves this page at /overlay/, so when loaded from there we talk to the
 * same host; from file:// (Electron) or any other server we use 127.0.0.1:8765.
 */
export function bridgeUrl({ token, bridge, location } = {}) {
  let host = bridge || "";
  if (!host && location && /^https?:$/.test(location.protocol) && location.pathname.startsWith("/overlay")) {
    host = location.host;
  }
  host = host || DEFAULT_BRIDGE;
  const q = new URLSearchParams({ client: "overlay", token: token || DEFAULT_TOKEN });
  return `ws://${host}/ws?${q.toString()}`;
}

export class BridgeConnection {
  /**
   * @param {{url:string, onFrame:(f:any)=>void, onStatus:(status:string, detail?:string)=>void,
   *          WebSocketImpl?: typeof WebSocket}} opts
   */
  constructor({ url, onFrame, onStatus, WebSocketImpl = globalThis.WebSocket }) {
    this.url = url;
    this.onFrame = onFrame;
    this.onStatus = onStatus;
    this.WS = WebSocketImpl;
    this.ws = null;
    this.retryMs = 500;
    this.timer = null;
    this.stopped = true;
    this.refused = false;
  }

  start() {
    this.stopped = false;
    this.#open();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    if (this.ws) this.ws.close(1000, "overlay closed");
    this.ws = null;
  }

  get open() {
    return !!this.ws && this.ws.readyState === 1;
  }

  /** Send one JSON frame. Returns false when not connected (callers surface that). */
  send(frame) {
    if (!this.open) return false;
    this.ws.send(JSON.stringify(frame));
    return true;
  }

  #open() {
    if (this.stopped) return;
    this.onStatus("connecting");
    let ws;
    try {
      ws = new this.WS(this.url);
    } catch (err) {
      this.#retry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.retryMs = 500;
      this.refused = false;
      ws.send(JSON.stringify({ type: "hello", client: "overlay", version: 1, app: "overlay" }));
      this.onStatus("live");
    };
    ws.onmessage = ev => {
      let frame;
      try {
        frame = JSON.parse(typeof ev.data === "string" ? ev.data : String(ev.data));
      } catch {
        return; // not JSON: ignore rather than crash the overlay
      }
      if (frame?.type === "error" && frame.code === "unauthorized") this.refused = true;
      this.onFrame(frame);
    };
    ws.onclose = ev => {
      if (this.ws === ws) this.ws = null;
      // 1008 (policy) / 4401 are how a bridge typically closes an unauthenticated socket.
      if (ev && (ev.code === 1008 || ev.code === 4401 || ev.code === 4403)) this.refused = true;
      this.#retry();
    };
    ws.onerror = () => { /* onclose follows and schedules the retry */ };
  }

  #retry() {
    if (this.stopped) return;
    clearTimeout(this.timer);
    // A refused token will not fix itself quickly; poll slowly so a restarted bridge is still found.
    const wait = this.refused ? 10000 : this.retryMs;
    this.onStatus(this.refused ? "refused" : "retrying", String(Math.round(wait / 1000) || 1));
    this.timer = setTimeout(() => this.#open(), wait);
    if (!this.refused) this.retryMs = Math.min(this.retryMs * 2, 5000);
  }
}
