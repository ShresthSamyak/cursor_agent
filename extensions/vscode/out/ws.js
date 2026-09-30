"use strict";
/**
 * Minimal text-only WebSocket client.
 *
 * Uses the runtime's global WebSocket (Node 22+, so every current VS Code
 * extension host) and falls back to a small RFC 6455 client over `net` when it
 * is missing. Only ws:// to loopback is ever used, so there is no TLS path.
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.openSocket = openSocket;
const crypto = __importStar(require("crypto"));
const net = __importStar(require("net"));
function openSocket(url, handlers, forceFallback = false) {
    const Ctor = globalThis.WebSocket;
    if (Ctor && !forceFallback) {
        return openGlobal(Ctor, url, handlers);
    }
    return new MiniSocket(url, handlers);
}
function openGlobal(Ctor, url, h) {
    let closed = false;
    const finish = (code, reason) => {
        if (!closed) {
            closed = true;
            h.onClose(code, reason);
        }
    };
    let ws;
    try {
        ws = new Ctor(url);
    }
    catch (err) {
        setTimeout(() => finish(1006, String(err)), 0);
        return { impl: "global", isOpen: () => false, send: () => false, close: () => undefined };
    }
    const decoder = new TextDecoder();
    ws.onopen = () => h.onOpen();
    ws.onmessage = (ev) => {
        const d = ev.data;
        if (typeof d === "string")
            h.onMessage(d);
        else if (d instanceof ArrayBuffer)
            h.onMessage(decoder.decode(d));
    };
    ws.onerror = () => undefined; // onclose always follows with the code
    ws.onclose = (ev) => finish(ev.code, ev.reason || "");
    return {
        impl: "global",
        isOpen: () => ws.readyState === 1,
        send: (text) => {
            if (ws.readyState !== 1)
                return false;
            ws.send(text);
            return true;
        },
        close: (code = 1000, reason = "") => {
            try {
                ws.close(code, reason);
            }
            catch {
                /* already closing */
            }
        },
    };
}
const GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
const MAX_MESSAGE = 16 * 1024 * 1024;
/** Hand-written client: handshake, masked text frames, ping/pong, fragmentation, close. */
class MiniSocket {
    h;
    impl = "fallback";
    sock;
    open = false;
    closed = false;
    buf = Buffer.alloc(0);
    fragments = [];
    closeCode = 1006;
    closeReason = "";
    constructor(url, h) {
        this.h = h;
        const u = new URL(url);
        const key = crypto.randomBytes(16).toString("base64");
        const expected = crypto.createHash("sha1").update(key + GUID).digest("base64");
        const port = Number(u.port || 80);
        const host = u.hostname.replace(/^\[|\]$/g, "");
        this.sock = net.connect({ host, port });
        this.sock.setNoDelay(true);
        this.sock.on("connect", () => {
            this.sock.write(`GET ${u.pathname}${u.search} HTTP/1.1\r\n` +
                `Host: ${u.host}\r\n` +
                "Upgrade: websocket\r\nConnection: Upgrade\r\n" +
                `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
        });
        this.sock.on("data", (chunk) => {
            this.buf = Buffer.concat([this.buf, chunk]);
            if (!this.open) {
                const end = this.buf.indexOf("\r\n\r\n");
                if (end < 0)
                    return;
                const head = this.buf.subarray(0, end).toString("latin1");
                this.buf = this.buf.subarray(end + 4);
                const status = head.split("\r\n", 1)[0];
                const accept = /^sec-websocket-accept:\s*(\S+)/im.exec(head)?.[1];
                if (!/^HTTP\/1\.1 101\b/.test(status) || accept !== expected) {
                    // e.g. "HTTP/1.1 401 Unauthorized": surface the status as the close reason.
                    this.closeReason = status || "bad handshake";
                    this.sock.destroy();
                    return;
                }
                this.open = true;
                this.h.onOpen();
            }
            this.readFrames();
        });
        this.sock.on("error", (err) => {
            if (!this.closeReason)
                this.closeReason = err.message;
        });
        this.sock.on("close", () => this.finish());
    }
    isOpen() {
        return this.open && !this.closed;
    }
    send(text) {
        if (!this.isOpen())
            return false;
        this.sock.write(frame(0x1, Buffer.from(text, "utf8")));
        return true;
    }
    close(code = 1000, reason = "") {
        if (this.closed)
            return;
        if (this.open) {
            const payload = Buffer.alloc(2 + Buffer.byteLength(reason));
            payload.writeUInt16BE(code, 0);
            payload.write(reason, 2);
            this.sock.write(frame(0x8, payload));
        }
        this.closeCode = code;
        this.closeReason = reason;
        this.sock.end();
        // Do not wait on a peer that never answers the close.
        setTimeout(() => this.sock.destroy(), 1000).unref();
    }
    finish() {
        if (this.closed)
            return;
        this.closed = true;
        this.open = false;
        this.h.onClose(this.closeCode, this.closeReason);
    }
    readFrames() {
        for (;;) {
            const b = this.buf;
            if (b.length < 2)
                return;
            const fin = (b[0] & 0x80) !== 0;
            const opcode = b[0] & 0x0f;
            const masked = (b[1] & 0x80) !== 0;
            let len = b[1] & 0x7f;
            let off = 2;
            if (len === 126) {
                if (b.length < 4)
                    return;
                len = b.readUInt16BE(2);
                off = 4;
            }
            else if (len === 127) {
                if (b.length < 10)
                    return;
                len = Number(b.readBigUInt64BE(2));
                off = 10;
            }
            if (len > MAX_MESSAGE) {
                this.close(1009, "message too big");
                return;
            }
            const maskOff = off;
            if (masked)
                off += 4;
            if (b.length < off + len)
                return;
            const payload = Buffer.from(b.subarray(off, off + len));
            if (masked) {
                for (let i = 0; i < payload.length; i++)
                    payload[i] ^= b[maskOff + (i % 4)];
            }
            this.buf = b.subarray(off + len);
            switch (opcode) {
                case 0x0: // continuation
                case 0x1: // text
                case 0x2: // binary (treated as UTF-8 text)
                    this.fragments.push(payload);
                    if (fin) {
                        const msg = Buffer.concat(this.fragments).toString("utf8");
                        this.fragments = [];
                        this.h.onMessage(msg);
                    }
                    break;
                case 0x8: // close: echo it, then end
                    this.closeCode = payload.length >= 2 ? payload.readUInt16BE(0) : 1005;
                    this.closeReason = payload.subarray(2).toString("utf8");
                    if (!this.closed)
                        this.sock.write(frame(0x8, payload.subarray(0, 2)));
                    this.sock.end();
                    return;
                case 0x9: // ping -> pong
                    this.sock.write(frame(0xa, payload));
                    break;
                default: // pong or reserved: ignore
                    break;
            }
        }
    }
}
/** Build one client frame (always FIN, always masked as RFC 6455 requires). */
function frame(opcode, payload) {
    const len = payload.length;
    const head = len < 126 ? 2 : len < 65536 ? 4 : 10;
    const out = Buffer.alloc(head + 4 + len);
    out[0] = 0x80 | opcode;
    if (len < 126) {
        out[1] = 0x80 | len;
    }
    else if (len < 65536) {
        out[1] = 0x80 | 126;
        out.writeUInt16BE(len, 2);
    }
    else {
        out[1] = 0x80 | 127;
        out.writeBigUInt64BE(BigInt(len), 2);
    }
    const mask = crypto.randomBytes(4);
    mask.copy(out, head);
    for (let i = 0; i < len; i++)
        out[head + 4 + i] = payload[i] ^ mask[i % 4];
    return out;
}
//# sourceMappingURL=ws.js.map