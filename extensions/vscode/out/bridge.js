"use strict";
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
Object.defineProperty(exports, "__esModule", { value: true });
exports.BridgeClient = void 0;
exports.validateBridgeUrl = validateBridgeUrl;
const protocol_1 = require("./protocol");
const ws_1 = require("./ws");
const QUEUE_MAX = 20;
const QUEUE_TTL_MS = 120_000;
const READY_AFTER_MS = 400; // no refusal within this window: the token was accepted
const BACKOFF_MAX_MS = 15_000;
function validateBridgeUrl(raw) {
    let u;
    try {
        u = new URL(raw);
    }
    catch {
        return `not a URL: ${raw}`;
    }
    if (u.protocol !== "ws:")
        return "only ws:// is supported (the bridge is loopback-only)";
    const host = u.hostname.replace(/^\[|\]$/g, "");
    if (!["127.0.0.1", "localhost", "::1"].includes(host)) {
        return `refusing non-loopback host "${u.hostname}": document text must stay on this machine`;
    }
    return undefined;
}
class BridgeClient {
    status = "stopped";
    lastError = "";
    impl = "";
    opts;
    socket;
    stopped = true;
    ready = false;
    reconnectTimer;
    readyTimer;
    backoff = 0;
    tokens = [];
    tokenIdx = 0;
    goodToken;
    triedInCycle = 0;
    authFailures = 0;
    sawAuthError = false;
    queue = [];
    frameListeners = [];
    statusListeners = [];
    onFrame(cb) {
        this.frameListeners.push(cb);
        return { dispose: () => (this.frameListeners = this.frameListeners.filter((x) => x !== cb)) };
    }
    onStatus(cb) {
        this.statusListeners.push(cb);
        return { dispose: () => (this.statusListeners = this.statusListeners.filter((x) => x !== cb)) };
    }
    isOpen() {
        return this.ready && !!this.socket?.isOpen();
    }
    start(opts) {
        this.stop();
        this.opts = opts;
        this.stopped = false;
        this.backoff = 0;
        this.triedInCycle = 0;
        this.authFailures = 0;
        this.connect();
    }
    stop() {
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
    sendEvent(ev, queue = false) {
        const event = { ...ev, app: ev.app ?? protocol_1.APP, source: protocol_1.SOURCE, ts: Date.now() };
        return this.sendFrame({ type: "event", event }, queue);
    }
    sendControl(frame, queue = true) {
        return this.sendFrame(frame, queue);
    }
    // ---------------------------------------------------------------- internals
    sendFrame(frame, queue) {
        const data = JSON.stringify(frame);
        const bytes = Buffer.byteLength(data, "utf8");
        if (bytes > protocol_1.LIMITS.frameBytes) {
            this.log(`dropped ${describe(frame)}: ${bytes} bytes exceeds the frame limit`);
            return false;
        }
        if (this.isOpen() && this.socket.send(data))
            return true;
        if (queue) {
            this.queue.push({ data, expires: Date.now() + QUEUE_TTL_MS });
            if (this.queue.length > QUEUE_MAX)
                this.queue.shift();
        }
        return false;
    }
    connect() {
        if (this.stopped || !this.opts)
            return;
        const bad = validateBridgeUrl(this.opts.url);
        if (bad) {
            this.lastError = bad;
            this.log(bad);
            this.setStatus("invalid");
            return; // a settings change restarts us
        }
        if (this.triedInCycle === 0) {
            this.tokens = this.opts.tokens();
            if (this.tokens.length === 0)
                this.tokens = ["trail-dev"];
            const good = this.goodToken ? this.tokens.indexOf(this.goodToken) : -1;
            this.tokenIdx = good >= 0 ? good : 0;
        }
        const token = this.tokens[this.tokenIdx % this.tokens.length];
        const u = new URL(this.opts.url);
        u.searchParams.set("client", protocol_1.CLIENT);
        u.searchParams.set("token", token);
        this.ready = false;
        this.sawAuthError = false;
        // Retries keep showing "offline"/"unauthorized" instead of flickering through "connecting".
        if (this.status === "stopped" || this.status === "invalid")
            this.setStatus("connecting");
        const socket = (0, ws_1.openSocket)(u.toString(), {
            onOpen: () => {
                if (this.socket !== socket)
                    return;
                socket.send(JSON.stringify({ type: "hello", client: protocol_1.CLIENT, version: protocol_1.PROTOCOL_VERSION, app: protocol_1.APP }));
                // No refusal shortly after hello means the token was accepted.
                this.readyTimer = setTimeout(() => this.markReady(token), READY_AFTER_MS);
            },
            onMessage: (text) => {
                if (this.socket !== socket)
                    return;
                this.onMessage(text, token);
            },
            onClose: (code, reason) => {
                if (this.socket !== socket)
                    return;
                this.onClose(code, reason);
            },
        }, this.opts.forceFallback);
        this.socket = socket;
        this.impl = socket.impl;
    }
    markReady(token) {
        clearTimeout(this.readyTimer);
        if (this.ready || this.sawAuthError || !this.socket?.isOpen())
            return;
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
        for (const q of pending)
            this.socket.send(q.data);
    }
    onMessage(text, token) {
        let frame;
        try {
            frame = JSON.parse(text);
        }
        catch {
            this.log("ignored a non-JSON frame from the bridge");
            return;
        }
        if (frame.type === "error") {
            const code = String(frame.code ?? "");
            const detail = String(frame.text ?? "");
            if (code === "unauthorized") {
                this.sawAuthError = true;
                this.lastError = "token rejected";
            }
            else {
                this.log(`bridge error: ${code}${detail ? ` (${detail})` : ""}`);
            }
        }
        else {
            this.markReady(token);
        }
        for (const cb of this.frameListeners) {
            try {
                cb(frame);
            }
            catch (err) {
                this.log(`frame handler failed: ${String(err)}`);
            }
        }
    }
    onClose(code, reason) {
        clearTimeout(this.readyTimer);
        const wasReady = this.ready;
        this.ready = false;
        this.socket = undefined;
        if (this.stopped)
            return;
        let delay;
        if (wasReady && !this.sawAuthError) {
            // An established connection dropped (bridge restarted?): same token, quick retry.
            this.log(`disconnected (${code}${reason ? `: ${reason}` : ""}); reconnecting`);
            this.backoff = 0;
            delay = this.nextBackoff();
        }
        else {
            // Refused before becoming usable: bridge down, or this token is wrong. Try the next one.
            if (this.sawAuthError || /\b40[13]\b/.test(reason))
                this.authFailures++;
            if (reason)
                this.lastError = reason;
            this.tokenIdx = (this.tokenIdx + 1) % this.tokens.length;
            this.triedInCycle++;
            if (this.triedInCycle < this.tokens.length) {
                delay = 250;
            }
            else {
                const allRejected = this.authFailures >= this.tokens.length;
                this.triedInCycle = 0;
                this.authFailures = 0;
                this.setStatus(allRejected ? "unauthorized" : "offline");
                delay = this.nextBackoff();
            }
        }
        if (this.status === "open" || this.status === "connecting")
            this.setStatus("offline");
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => this.connect(), delay);
    }
    nextBackoff() {
        const base = Math.min(BACKOFF_MAX_MS, 500 * 2 ** this.backoff);
        this.backoff = Math.min(this.backoff + 1, 10);
        return Math.round(base * (0.8 + Math.random() * 0.4));
    }
    setStatus(s) {
        if (this.status === s)
            return;
        this.status = s;
        for (const cb of this.statusListeners) {
            try {
                cb(s);
            }
            catch (err) {
                this.log(`status handler failed: ${String(err)}`);
            }
        }
    }
    log(line) {
        this.opts?.log(`[bridge] ${line}`);
    }
}
exports.BridgeClient = BridgeClient;
function describe(frame) {
    return frame.type === "event" ? `event ${frame.event.type}` : frame.type;
}
//# sourceMappingURL=bridge.js.map