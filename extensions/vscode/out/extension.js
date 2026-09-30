"use strict";
/**
 * Trail code mentor: VS Code perception + UI for the Trail bridge (docs/bridge-protocol.md).
 *
 * Sends hover/dwell, selection, typing rhythm, document text on typing pauses (and at once when
 * a key is pasted), saves, test runs and terminal output. Renders agent-initiated notices as
 * notifications and line decorations. Secrets are redacted here; only their line numbers leave.
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
exports.activate = activate;
exports.deactivate = deactivate;
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
const bridge_1 = require("./bridge");
const findings_1 = require("./findings");
const privacy_1 = require("./privacy");
const protocol_1 = require("./protocol");
const token_1 = require("./token");
const DWELL_MS = 350;
function activate(ctx) {
    const out = vscode.window.createOutputChannel("Trail");
    const bridge = new bridge_1.BridgeClient();
    const findings = new findings_1.Findings(ctx);
    const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    status.command = "trail.menu";
    status.show();
    ctx.subscriptions.push(out, findings, status);
    const cfg = () => vscode.workspace.getConfiguration("trail");
    let perception = true;
    let waiting = 0;
    let conn = "stopped";
    const log = (line) => out.appendLine(`[${new Date().toLocaleTimeString()}] ${line}`);
    const renderStatus = (flash) => {
        const icon = conn === "open" ? (waiting ? "$(watch)" : "$(eye)") : conn === "connecting" ? "$(sync~spin)" : "$(debug-disconnect)";
        status.text = `${icon} Trail${waiting ? ` · ${waiting} waiting` : ""}${perception ? "" : " · paused"}`;
        status.tooltip = flash || `Bridge: ${conn}${bridge.lastError ? ` (${bridge.lastError})` : ""}`;
    };
    const start = () => {
        if (!cfg().get("enabled", true)) {
            bridge.stop();
            return;
        }
        const url = cfg().get("bridgeUrl", "ws://127.0.0.1:8765/ws");
        const bad = (0, bridge_1.validateBridgeUrl)(url);
        if (bad) {
            log(bad);
            vscode.window.showWarningMessage(`Trail: ${bad}`);
            return;
        }
        const inspected = cfg().inspect("token");
        const explicit = inspected?.globalValue !== undefined || inspected?.workspaceValue !== undefined;
        bridge.start({ url, tokens: () => (0, token_1.tokenCandidates)(cfg().get("token"), explicit), log });
    };
    const excluded = (doc) => doc.uri.scheme !== "file" && doc.uri.scheme !== "untitled" ||
        (cfg().get("excludeFiles") || []).some((pattern) => vscode.languages.match({ pattern }, doc) > 0);
    const rel = (doc) => path.basename(doc.fileName) || vscode.workspace.asRelativePath(doc.uri, false);
    const send = (ev, queue = false) => perception && bridge.sendEvent(ev, queue);
    // ------------------------------------------------------------------ bridge -> UI
    ctx.subscriptions.push(bridge.onStatus((s) => { conn = s; renderStatus(); }));
    ctx.subscriptions.push(bridge.onFrame((f) => {
        if (f.type === "output")
            onOutput(f.output);
        if (f.type === "state") {
            const st = f.state;
            waiting = Array.isArray(st.pending_notices) ? st.pending_notices.length : waiting;
            renderStatus();
        }
        if (f.type === "error")
            log(`bridge error: ${f.code}`);
    }));
    function onOutput(o) {
        if (o.type === "speak" && o.text) {
            if (o.kind === "notice") {
                const tier = String(o.meta?.tier || "normal");
                const file = typeof o.meta?.file === "string" ? o.meta.file : undefined;
                const line = Number(o.meta?.line);
                findings.add(file, line, o.text, tier, vscode.window.activeTextEditor?.document);
                log(`[${tier}] ${o.text}`);
                if (cfg().get("showNotices", true)) {
                    const show = tier === "critical" ? vscode.window.showErrorMessage : tier === "high" ? vscode.window.showWarningMessage
                        : vscode.window.showInformationMessage;
                    show(`Trail: ${o.text}`, "Show line", "Not now").then((choice) => {
                        if (choice === "Not now")
                            send({ type: "not_now" });
                        if (choice === "Show line")
                            findings.revealLatest();
                    });
                }
            }
            else {
                log(`${o.kind === "ack" ? "…" : "▸"} ${o.text}`);
                renderStatus(o.text);
            }
        }
        else if (o.type === "speak_end" && o.text) {
            log(`▸ ${o.text}`);
            vscode.window.setStatusBarMessage(`Trail: ${o.text.slice(0, 120)}`, 8000);
        }
        else if (o.type === "status") {
            if (o.code === "notice_waiting") {
                waiting = Number(o.meta?.count) || waiting;
                renderStatus("Waiting for your pause");
            }
            if (o.code === "notice_dropped") {
                waiting = 0;
                log("dropped a warning: you fixed it before your pause");
                renderStatus();
            }
            if (o.code === "prediagnosis_ready")
                log(`pre-diagnosed a failure at ${o.meta?.file}:${o.meta?.line} (ask "why did that break?")`);
        }
    }
    // ------------------------------------------------------------------ hover / dwell
    let dwellTimer;
    ctx.subscriptions.push(vscode.languages.registerHoverProvider({ scheme: "file" }, {
        provideHover(doc, pos) {
            if (excluded(doc))
                return undefined;
            const range = doc.getWordRangeAtPosition(pos);
            if (!range)
                return undefined;
            const word = doc.getText(range);
            const lineText = doc.lineAt(pos.line).text;
            if ((0, privacy_1.looksSensitive)(lineText))
                return undefined;
            const target = { text: (0, protocol_1.cap)(word, protocol_1.LIMITS.targetText), context: `${rel(doc)}:${pos.line + 1}`, role: "code",
                url: doc.uri.toString() };
            send({ type: "hover", target });
            clearTimeout(dwellTimer);
            if (cfg().get("dwellFromHover", true)) {
                dwellTimer = setTimeout(() => send({ type: "dwell", target: { ...target, text: (0, protocol_1.cap)(lineText.trim(), 400), dwell_ms: DWELL_MS } }), DWELL_MS);
            }
            return undefined;
        },
    }));
    // ------------------------------------------------------------------ selection
    let selTimer;
    ctx.subscriptions.push(vscode.window.onDidChangeTextEditorSelection((e) => {
        const doc = e.textEditor.document;
        if (excluded(doc))
            return;
        clearTimeout(selTimer);
        selTimer = setTimeout(() => {
            const text = doc.getText(e.selections[0]);
            if (!text.trim() || (0, privacy_1.looksSensitive)(text))
                return;
            send({ type: "select", target: { text: (0, protocol_1.cap)(text, protocol_1.LIMITS.targetText), context: `${rel(doc)}:${e.selections[0].start.line + 1}`, role: "code" } });
        }, 400);
    }));
    // ------------------------------------------------------------------ typing + doc_change
    let typing = false;
    let idleTimer;
    const changedLines = new Map();
    const diagnosticsOf = (doc) => vscode.languages.getDiagnostics(doc.uri).slice(0, 50).map((d) => ({
        line: d.range.start.line + 1,
        severity: ["error", "warning", "information", "hint"][d.severity] ?? "information",
        message: d.message.slice(0, 300), source: d.source, code: typeof d.code === "object" ? String(d.code.value) : d.code?.toString(),
    }));
    function sendDoc(doc, reason) {
        if (excluded(doc))
            return;
        const key = doc.uri.toString();
        const lines = [...(changedLines.get(key) || [])].sort((a, b) => a - b).slice(0, 200);
        changedLines.delete(key);
        const raw = doc.getText();
        const secretLines = [];
        const safeLines = raw.split(/\r?\n/).map((l, i) => {
            if ((0, privacy_1.containsSecret)(l)) {
                secretLines.push(i + 1);
                return (0, privacy_1.redact)(l);
            }
            return l;
        });
        const maxBytes = Math.min(cfg().get("maxDocumentBytes", 200_000), protocol_1.LIMITS.frameBytes - 20_000);
        const text = safeLines.join("\n");
        const changed = lines.filter((n) => n < safeLines.length).map((n) => ({ line: n + 1, text: safeLines[n] }));
        send({ type: "doc_change", data: {
                file: rel(doc), version: doc.version, language: doc.languageId, text: (0, protocol_1.cap)(text, maxBytes), truncated: text.length > maxBytes,
                changed, diagnostics: diagnosticsOf(doc), reason, secret_lines: secretLines,
            } });
    }
    ctx.subscriptions.push(vscode.workspace.onDidChangeTextDocument((e) => {
        const doc = e.document;
        if (excluded(doc) || !e.contentChanges.length)
            return;
        const key = doc.uri.toString();
        const set = changedLines.get(key) || new Set();
        for (const c of e.contentChanges) {
            const first = c.range.start.line;
            const n = c.text.split("\n").length;
            for (let i = first; i < first + Math.max(n, 1); i++)
                set.add(i);
        }
        changedLines.set(key, set);
        if (!typing) {
            typing = true;
            send({ type: "typing", active: true });
        }
        // A pasted key cannot wait for the pause: the mentor interrupts at once.
        if (e.contentChanges.some((c) => (0, privacy_1.containsSecret)(c.text)))
            sendDoc(doc, "paste");
        clearTimeout(idleTimer);
        idleTimer = setTimeout(() => {
            sendDoc(doc, "pause");
            typing = false;
            send({ type: "typing", active: false });
        }, cfg().get("typingIdleMs", 900));
    }));
    ctx.subscriptions.push(vscode.workspace.onDidSaveTextDocument((doc) => {
        if (excluded(doc))
            return;
        sendDoc(doc, "save");
        send({ type: "save", data: { file: rel(doc), version: doc.version } });
    }));
    // ------------------------------------------------------------------ tests + terminal
    ctx.subscriptions.push(vscode.tasks.onDidEndTaskProcess((e) => {
        const name = e.execution.task.name;
        if (!/test|pytest|jest|mocha|unittest/i.test(`${name} ${e.execution.task.group?.id ?? ""}`))
            return;
        send({ type: "test_run", data: { passed: e.exitCode === 0, summary: `${name} exited ${e.exitCode}` } });
    }));
    const win = vscode.window;
    if (win.onDidStartTerminalShellExecution) {
        ctx.subscriptions.push(win.onDidStartTerminalShellExecution(async (e) => {
            if (!cfg().get("readTerminal", true))
                return;
            let buf = "";
            for await (const chunk of e.execution.read()) {
                buf = (buf + (0, privacy_1.cleanTerminal)(chunk)).slice(-protocol_1.LIMITS.eventText);
            }
            const cmd = e.execution.commandLine?.value || "";
            if (/pytest|test|jest|npm (?:run )?test/i.test(cmd)) {
                send({ type: "test_run", data: { passed: !/FAILED|Error|Traceback|failed/.test(buf), summary: cmd.slice(0, 120) } });
            }
            if (/Traceback|Error|Exception|FAILED|failed/.test(buf))
                send({ type: "terminal", text: (0, privacy_1.redact)(buf) });
        }));
    }
    ctx.subscriptions.push(vscode.window.onDidChangeWindowState((s) => { if (s.focused)
        send({ type: "app_switch" }); }));
    // ------------------------------------------------------------------ commands
    const ask = async (prompt, prefix = "") => {
        const text = await vscode.window.showInputBox({ prompt });
        if (text?.trim())
            bridge.sendEvent({ type: "speech_final", text: prefix + text.trim() }, true);
    };
    const cmds = {
        "trail.declareIntent": () => ask("What are you building? (e.g. OAuth login)", "I'm building "),
        "trail.ask": () => ask("Ask Trail"),
        "trail.notNow": () => bridge.sendEvent({ type: "not_now" }, true),
        "trail.setMode": async () => {
            const pick = await vscode.window.showQuickPick(["teach", "fix"], { placeHolder: "Teach mode asks a question; fix mode proposes the change" });
            if (pick === "teach" || pick === "fix")
                bridge.sendControl({ type: "control", action: "mode", name: pick });
        },
        "trail.togglePerception": () => {
            perception = !perception;
            bridge.sendControl({ type: "control", action: "perception", on: perception });
            renderStatus();
        },
        "trail.cancel": () => bridge.sendEvent({ type: "cancel" }, true),
        "trail.clearFindings": () => findings.clear(),
        "trail.reconnect": () => start(),
        "trail.showOutput": () => out.show(true),
        "trail.menu": async () => {
            const items = ["Ask", "Declare what I'm building", "Teach mode / Fix mode", "Not now", "Toggle perception", "Show output", "Reconnect"];
            const ids = ["trail.ask", "trail.declareIntent", "trail.setMode", "trail.notNow", "trail.togglePerception", "trail.showOutput", "trail.reconnect"];
            const pick = await vscode.window.showQuickPick(items);
            if (pick)
                vscode.commands.executeCommand(ids[items.indexOf(pick)]);
        },
    };
    for (const [id, fn] of Object.entries(cmds))
        ctx.subscriptions.push(vscode.commands.registerCommand(id, fn));
    ctx.subscriptions.push(vscode.workspace.onDidChangeConfiguration((e) => { if (e.affectsConfiguration("trail"))
        start(); }));
    ctx.subscriptions.push({ dispose: () => bridge.stop() });
    renderStatus();
    start();
}
function deactivate() { }
//# sourceMappingURL=extension.js.map