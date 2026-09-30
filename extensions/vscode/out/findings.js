"use strict";
/**
 * Line decorations for mentor findings (output.meta.file / output.meta.line).
 *
 * Mirrors the core's staleness rule (PDF p. 11): a finding is tied to one line;
 * edits above it shift it, and any edit that touches the line itself removes it.
 * Findings for files that are not open yet are kept and shown once opened.
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
exports.Findings = void 0;
exports.matches = matches;
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
const MAX_FINDINGS = 50;
class Findings {
    items = [];
    normal;
    critical;
    subs = [];
    constructor(ctx) {
        const base = (icon, ruler) => ({
            gutterIconPath: ctx.asAbsolutePath(path.join("media", icon)),
            gutterIconSize: "70%",
            isWholeLine: true,
            overviewRulerColor: new vscode.ThemeColor(ruler),
            overviewRulerLane: vscode.OverviewRulerLane.Right,
        });
        this.normal = vscode.window.createTextEditorDecorationType({
            ...base("finding.svg", "editorOverviewRuler.warningForeground"),
            backgroundColor: new vscode.ThemeColor("editor.hoverHighlightBackground"),
        });
        this.critical = vscode.window.createTextEditorDecorationType({
            ...base("finding-critical.svg", "editorOverviewRuler.errorForeground"),
            backgroundColor: new vscode.ThemeColor("diffEditor.removedLineBackground"),
        });
        this.subs.push(vscode.workspace.onDidChangeTextDocument((e) => this.onChange(e)), vscode.window.onDidChangeVisibleTextEditors(() => this.refresh()), vscode.workspace.onDidOpenTextDocument(() => this.refresh()));
    }
    /** Add a finding from output meta. `file` may be omitted: the fallback document is used. */
    add(file, line1, text, tier, fallback) {
        if (!Number.isFinite(line1) || line1 < 1)
            return false;
        const doc = file ? findDocument(file) : fallback;
        const name = file ?? (doc ? vscode.workspace.asRelativePath(doc.uri, false) : undefined);
        if (!name)
            return false;
        const f = { file: name, uri: doc?.uri.toString(), line: Math.floor(line1) - 1, text, tier };
        this.items = this.items.filter((x) => !(sameFile(x, f) && x.line === f.line));
        this.items.push(f);
        if (this.items.length > MAX_FINDINGS)
            this.items.shift();
        this.refresh();
        return true;
    }
    count() {
        return this.items.length;
    }
    clear() {
        this.items = [];
        this.refresh();
    }
    /** Open the most recent finding's line (used by the notification's "Show" button). */
    async revealLatest() {
        const f = this.items[this.items.length - 1];
        if (!f)
            return;
        const doc = f.uri
            ? await vscode.workspace.openTextDocument(vscode.Uri.parse(f.uri))
            : findDocument(f.file) ?? (await openByName(f.file));
        if (!doc)
            return;
        const line = Math.min(f.line, doc.lineCount - 1);
        const range = new vscode.Range(line, 0, line, 0);
        await vscode.window.showTextDocument(doc, { selection: range, preserveFocus: false });
    }
    dispose() {
        this.subs.forEach((d) => d.dispose());
        this.normal.dispose();
        this.critical.dispose();
    }
    onChange(e) {
        if (!this.items.length || !e.contentChanges.length)
            return;
        const uri = e.document.uri.toString();
        let touched = false;
        for (const f of this.items) {
            if (!f.uri && matches(e.document, f.file))
                f.uri = uri;
        }
        const mine = this.items.filter((f) => f.uri === uri);
        if (!mine.length)
            return;
        const dead = new Set();
        for (const c of e.contentChanges) {
            const s = c.range.start.line;
            const end = c.range.end.line;
            const delta = countNewlines(c.text) - (end - s);
            for (const f of mine) {
                if (dead.has(f))
                    continue;
                if (f.line >= s && f.line <= end) {
                    dead.add(f); // the finding's own line changed: stale, drop it
                }
                else if (f.line > end && delta !== 0) {
                    f.line += delta;
                    touched = true;
                }
            }
        }
        if (dead.size)
            this.items = this.items.filter((f) => !dead.has(f));
        if (dead.size || touched)
            this.refresh();
    }
    refresh() {
        for (const editor of vscode.window.visibleTextEditors) {
            const doc = editor.document;
            const normal = [];
            const critical = [];
            for (const f of this.items) {
                if (f.uri ? f.uri !== doc.uri.toString() : !matches(doc, f.file))
                    continue;
                if (!f.uri)
                    f.uri = doc.uri.toString();
                if (f.line >= doc.lineCount)
                    continue;
                const hover = new vscode.MarkdownString();
                hover.appendMarkdown(`**Trail** · ${f.tier}\n\n`);
                hover.appendText(f.text);
                const opt = {
                    range: new vscode.Range(f.line, 0, f.line, doc.lineAt(f.line).text.length),
                    hoverMessage: hover,
                    renderOptions: {
                        after: {
                            contentText: `  Trail: ${shorten(f.text, 70)}`,
                            color: new vscode.ThemeColor("editorCodeLens.foreground"),
                            fontStyle: "italic",
                        },
                    },
                };
                (f.tier === "critical" ? critical : normal).push(opt);
            }
            editor.setDecorations(this.normal, normal);
            editor.setDecorations(this.critical, critical);
        }
    }
}
exports.Findings = Findings;
function countNewlines(s) {
    let n = 0;
    for (let i = 0; i < s.length; i++)
        if (s.charCodeAt(i) === 10)
            n++;
    return n;
}
function shorten(s, n) {
    const one = s.replace(/\s+/g, " ").trim();
    return one.length <= n ? one : one.slice(0, n - 1) + "…";
}
function norm(p) {
    const s = p.replace(/\\/g, "/");
    return process.platform === "win32" ? s.toLowerCase() : s;
}
/** Does `doc` match a file reference that may be relative, absolute or a bare name? */
function matches(doc, file) {
    const ref = norm(file).replace(/^\.\//, "");
    const full = norm(doc.uri.fsPath);
    const rel = norm(vscode.workspace.asRelativePath(doc.uri, false));
    return full === ref || rel === ref || full.endsWith("/" + ref);
}
function sameFile(a, b) {
    return (!!a.uri && a.uri === b.uri) || norm(a.file) === norm(b.file);
}
function findDocument(file) {
    return vscode.workspace.textDocuments.find((d) => matches(d, file));
}
async function openByName(file) {
    if (path.isAbsolute(file))
        return vscode.workspace.openTextDocument(file);
    const hits = await vscode.workspace.findFiles(`**/${file.replace(/\\/g, "/")}`, "**/node_modules/**", 1);
    return hits[0] ? vscode.workspace.openTextDocument(hits[0]) : undefined;
}
//# sourceMappingURL=findings.js.map