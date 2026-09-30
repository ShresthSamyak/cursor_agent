/**
 * Connection to the Trail bridge (docs/bridge-protocol.md).
 *
 * Deliberately independent of the `vscode` module so it can be exercised from
 * plain Node (scripts/check-ws.js). Responsibilities:
 *   - connect to ws://127.0.0.1:8765/ws?client=vscode&token=... (loopback only);
 *   - send `hello` first, then JSON frames;
 *   - rotate through token candidates when a connection is refused before it
 *     becomes usable, and reconnect with capped exponential backoff;
 *   - drop perception events while offline (they would be stale), but queue a few
 *     explicit user actions (questions, "not now", mode changes) for a short time.
 */

import { APP, BusEvent, CLIENT, ClientFrame, ControlFrame, LIMITS, PROTOCOL_VERSION, SOURCE, ServerFrame } from "./protocol";
import { Socket, openSocket } from "./ws";

export type ConnStatus = "stopped" | "connecting" | "open" | "offline" | "unauthorized" | "invalid";

export interface BridgeOptions {
  url: string;
  /** Token candidates in priority order; re-evaluated at the start of every cycle. */
  tokens: () => string[];
  log: (line: string) => void;
  /** Test hook: use the hand-written client even when a global WebSocket exists. */
  forceFallback?: boolean;
}

type EventInput = Omit<BusEvent, "app" | "source" | "ts"> & { app?: string };

const QUEUE_MAX = 20;
const QUEUE_TTL_MS = 120_000;
const READY_AFTER_MS = 400; // no refusal within this window: the token was accepted
const BACKOFF_MAX_MS = 15_000;

export function validateBridgeUrl(raw: string): string | undefined {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return `not a URL: ${raw}`;
  }
  if (u.protocol !== "ws:") return "only ws:// is supported (the bridge is loopback-only)";
  const host = u.hostname.replace(/^\[|\]$/g, "");
  if (!["127.0.0.1", "localhost", "::1"].includes(host)) {
    return `refusing non-loopback host "${u.hostname}": document text must stay on this machine`;
  }
  return undefined;
}

export class BridgeClient {
  status: ConnStatus = "stopped";
  lastError = "";
  impl: "global" | "fallback" | "" = "";

  private opts?: BridgeOptions;
  private socket?: Socket;
  private stopped = true;
  private ready = false;
  private reconnectTimer?: NodeJS.Timeout;
  private readyTimer?: NodeJS.Timeout;
  private backoff = 0;
  private tokens: string[] = [];
  private tokenIdx = 0;
  private goodToken?: string;
  private triedInCycle = 0;
  private authFailures = 0;
  private sawAuthError = false;
  private queue: { data: string; expires: number }[] = [];
  private frameListeners: ((f: ServerFrame) => void)[] = [];
  private statusListeners: ((s: ConnStatus) => void)[] = [];

  onFrame(cb: (f: ServerFrame) => void): { dispose(): void } {
    this.frameListeners.push(cb);
    return { dispose: () => (this.frameListeners = this.frameListeners.filter((x) => x !== cb)) };
  }

  onStatus(cb: (s: ConnStatus) => void): { dispose(): void } {
    this.statusListeners.push(cb);
    return { dispose: () => (this.statusListeners = this.statusListeners.filter((x) => x !== cb)) };
  }

  isOpen(): boolean {
    return this.ready && !!this.socket?.isOpen();
  }

  start(opts: BridgeOptions): void {
    this.stop();
    this.opts = opts;
    this.stopped = false;
    this.backoff = 0;
    this.triedInCycle = 0;
    this.authFailures = 0;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    clearTimeout(this.reconnectTimer);
    clearTimeout(this.readyTimer);
    const s = this.socket;
    this.socket = undefined;
    this.ready = false;
    s?.close(1000, "client stopping");
    this.setStatus("stopped");
  }

  /** Send a perception or user event. `queue` keeps it for delivery after a reconnect. */
  sendEvent(ev: EventInput, queue = false): boolean {
    const event: BusEvent = { ...ev, app: ev.app ?? APP, source: SOURCE, ts: Date.now() };
    return this.sendFrame({ type: "event", event }, queue);
  }

  sendControl(frame: ControlFrame, queue = true): boolean {
    return this.sendFrame(frame, queue);
  }

  // ---------------------------------------------------------------- internals

  private sendFrame(frame: ClientFrame, queue: boolean): boolean {
    const data = JSON.stringify(frame);
    const bytes = Buffer.byteLength(data, "utf8");
    if (bytes > LIMITS.frameBytes) {
      this.log(`dropped ${describe(frame)}: ${bytes} bytes exceeds the frame limit`);
      return false;
    }
    if (this.isOpen() && this.socket!.send(data)) return true;
    if (queue) {
      this.queue.push({ data, expires: Date.now() + QUEUE_TTL_MS });
      if (this.queue.length > QUEUE_MAX) this.queue.shift();
    }
    return false;
  }

  private connect(): void {
    if (this.stopped || !this.opts) return;
    const bad = validateBridgeUrl(this.opts.url);
    if (bad) {
      this.lastError = bad;
      this.log(bad);
      this.setStatus("invalid");
      return; // a settings change restarts us
    }
    if (this.triedInCycle === 0) {
      this.tokens = this.opts.tokens();
      if (this.tokens.length === 0) this.tokens = ["trail-dev"];
      const good = this.goodToken ? this.tokens.indexOf(this.goodToken) : -1;
      this.tokenIdx = good >= 0 ? good : 0;
    }
    const token = this.tokens[this.tokenIdx % this.tokens.length];
    const u = new URL(this.opts.url);
    u.searchParams.set("client", CLIENT);
    u.searchParams.set("token", token);

    this.ready = false;
    this.sawAuthError = false;
    this.setStatus("connecting");
    const socket = openSocket(
      u.toString(),
      {
        onOpen: () => {
          if (this.socket !== socket) return;
          socket.send(JSON.stringify({ type: "hello", client: CLIENT, version: PROTOCOL_VERSION, app: APP }));
          // No refusal shortly after hello means the token was accepted.
          this.readyTimer = setTimeout(() => this.markReady(token), READY_AFTER_MS);
        },
        onMessage: (text) => {
          if (this.socket !== socket) return;
          this.onMessage(text, token);
        },
        onClose: (code, reason) => {
          if (this.socket !== socket) return;
          this.onClose(code, reason);
        },
      },
      this.opts.forceFallback,
    );
    this.socket = socket;
    this.impl = socket.impl;
  }

  private markReady(token: string): void {
    clearTimeout(this.readyTimer);
    if (this.ready || this.sawAuthError || !this.socket?.isOpen()) return;
    this.ready = true;
    this.goodToken = token;
    this.backoff = 0;
    this.triedInCycle = 0;
    this.authFailures = 0;
    this.lastError = "";
    this.log(`connected (${this.impl} WebSocket)`);
    this.setStatus("open");
    const now = Date.now();
    const pending = this.queue.filter((q) => q.expires > now);
    this.queue = [];
    for (const q of pending) this.socket.send(q.data);
  }

  private onMessage(text: string, token: string): void {
    let frame: ServerFrame;
    try {
      frame = JSON.parse(text) as ServerFrame;
    } catch {
      this.log("ignored a non-JSON frame from the bridge");
      return;
    }
    if (frame.type === "error") {
      const code = String((frame as { code?: unknown }).code ?? "");
      const detail = String((frame as { text?: unknown }).text ?? "");
      if (code === "unauthorized") {
        this.sawAuthError = true;
        this.lastError = "token rejected";
      } else {
        this.log(`bridge error: ${code}${detail ? ` (${detail})` : ""}`);
      }
    } else {
      this.markReady(token);
    }
    for (const cb of this.frameListeners) {
      try {
        cb(frame);
      } catch (err) {
        this.log(`frame handler failed: ${String(err)}`);
      }
    }
  }

  private onClose(code: number, reason: string): void {
    clearTimeout(this.readyTimer);
    const wasReady = this.ready;
    this.ready = false;
    this.socket = undefined;
    if (this.stopped) return;

    let delay: number;
    if (wasReady && !this.sawAuthError) {
      // An established connection dropped (bridge restarted?): same token, quick retry.
      this.log(`disconnected (${code}${reason ? `: ${reason}` : ""}); reconnecting`);
      this.backoff = 0;
      delay = this.nextBackoff();
    } else {
      // Refused before becoming usable: bridge down, or this token is wrong. Try the next one.
      if (this.sawAuthError || /\b40[13]\b/.test(reason)) this.authFailures++;
      if (reason) this.lastError = reason;
      this.tokenIdx = (this.tokenIdx + 1) % this.tokens.length;
      this.triedInCycle++;
      if (this.triedInCycle < this.tokens.length) {
        delay = 250;
      } else {
        const allRejected = this.authFailures >= this.tokens.length;
        this.triedInCycle = 0;
        this.authFailures = 0;
        this.setStatus(allRejected ? "unauthorized" : "offline");
        delay = this.nextBackoff();
      }
    }
    if (this.status === "open" || this.status === "connecting") this.setStatus("offline");
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private nextBackoff(): number {
    const base = Math.min(BACKOFF_MAX_MS, 500 * 2 ** this.backoff);
    this.backoff = Math.min(this.backoff + 1, 10);
    return Math.round(base * (0.8 + Math.random() * 0.4));
  }

  private setStatus(s: ConnStatus): void {
    if (this.status === s) return;
    this.status = s;
    for (const cb of this.statusListeners) {
      try {
        cb(s);
      } catch (err) {
        this.log(`status handler failed: ${String(err)}`);
      }
    }
  }

  private log(line: string): void {
    this.opts?.log(`[bridge] ${line}`);
  }
}

function describe(frame: ClientFrame): string {
  return frame.type === "event" ? `event ${frame.event.type}` : frame.type;
}
