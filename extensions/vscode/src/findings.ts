/**
 * Line decorations for mentor findings (output.meta.file / output.meta.line).
 *
 * Mirrors the core's staleness rule (PDF p. 11): a finding is tied to one line;
 * edits above it shift it, and any edit that touches the line itself removes it.
 * Findings for files that are not open yet are kept and shown once opened.
 */

import * as path from "path";
import * as vscode from "vscode";

interface Finding {
  file: string; // as the bridge named it (relative, absolute or bare name)
  uri?: string; // resolved document, once known
  line: number; // 0-based
  text: string;
  tier: string;
}

const MAX_FINDINGS = 50;

export class Findings implements vscode.Disposable {
  private items: Finding[] = [];
  private readonly normal: vscode.TextEditorDecorationType;
  private readonly critical: vscode.TextEditorDecorationType;
  private readonly subs: vscode.Disposable[] = [];

  constructor(ctx: vscode.ExtensionContext) {
    const base = (icon: string, ruler: string): vscode.DecorationRenderOptions => ({
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
    this.subs.push(
      vscode.workspace.onDidChangeTextDocument((e) => this.onChange(e)),
      vscode.window.onDidChangeVisibleTextEditors(() => this.refresh()),
      vscode.workspace.onDidOpenTextDocument(() => this.refresh()),
    );
  }

  /** Add a finding from output meta. `file` may be omitted: the fallback document is used. */
  add(file: string | undefined, line1: number, text: string, tier: string, fallback?: vscode.TextDocument): boolean {
    if (!Number.isFinite(line1) || line1 < 1) return false;
    const doc = file ? findDocument(file) : fallback;
    const name = file ?? (doc ? vscode.workspace.asRelativePath(doc.uri, false) : undefined);
    if (!name) return false;
    const f: Finding = { file: name, uri: doc?.uri.toString(), line: Math.floor(line1) - 1, text, tier };
    this.items = this.items.filter((x) => !(sameFile(x, f) && x.line === f.line));
    this.items.push(f);
    if (this.items.length > MAX_FINDINGS) this.items.shift();
    this.refresh();
    return true;
  }

  count(): number {
    return this.items.length;
  }

  clear(): void {
    this.items = [];
    this.refresh();
  }

  /** Open the most recent finding's line (used by the notification's "Show" button). */
  async revealLatest(): Promise<void> {
    const f = this.items[this.items.length - 1];
    if (!f) return;
    const doc = f.uri
      ? await vscode.workspace.openTextDocument(vscode.Uri.parse(f.uri))
      : findDocument(f.file) ?? (await openByName(f.file));
    if (!doc) return;
    const line = Math.min(f.line, doc.lineCount - 1);
    const range = new vscode.Range(line, 0, line, 0);
    await vscode.window.showTextDocument(doc, { selection: range, preserveFocus: false });
  }

  dispose(): void {
    this.subs.forEach((d) => d.dispose());
    this.normal.dispose();
    this.critical.dispose();
  }

  private onChange(e: vscode.TextDocumentChangeEvent): void {
    if (!this.items.length || !e.contentChanges.length) return;
    const uri = e.document.uri.toString();
    let touched = false;
    for (const f of this.items) {
      if (!f.uri && matches(e.document, f.file)) f.uri = uri;
    }
    const mine = this.items.filter((f) => f.uri === uri);
    if (!mine.length) return;
    const dead = new Set<Finding>();
    for (const c of e.contentChanges) {
      const s = c.range.start.line;
      const end = c.range.end.line;
      const delta = countNewlines(c.text) - (end - s);
      for (const f of mine) {
        if (dead.has(f)) continue;
        if (f.line >= s && f.line <= end) {
          dead.add(f); // the finding's own line changed: stale, drop it
        } else if (f.line > end && delta !== 0) {
          f.line += delta;
          touched = true;
        }
      }
    }
    if (dead.size) this.items = this.items.filter((f) => !dead.has(f));
    if (dead.size || touched) this.refresh();
  }

  private refresh(): void {
    for (const editor of vscode.window.visibleTextEditors) {
      const doc = editor.document;
      const normal: vscode.DecorationOptions[] = [];
      const critical: vscode.DecorationOptions[] = [];
      for (const f of this.items) {
        if (f.uri ? f.uri !== doc.uri.toString() : !matches(doc, f.file)) continue;
        if (!f.uri) f.uri = doc.uri.toString();
        if (f.line >= doc.lineCount) continue;
        const hover = new vscode.MarkdownString();
        hover.appendMarkdown(`**Trail** · ${f.tier}\n\n`);
        hover.appendText(f.text);
        const opt: vscode.DecorationOptions = {
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

function countNewlines(s: string): number {
  let n = 0;
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) === 10) n++;
  return n;
}

function shorten(s: string, n: number): string {
  const one = s.replace(/\s+/g, " ").trim();
  return one.length <= n ? one : one.slice(0, n - 1) + "…";
}

function norm(p: string): string {
  const s = p.replace(/\\/g, "/");
  return process.platform === "win32" ? s.toLowerCase() : s;
}

/** Does `doc` match a file reference that may be relative, absolute or a bare name? */
export function matches(doc: vscode.TextDocument, file: string): boolean {
  const ref = norm(file).replace(/^\.\//, "");
  const full = norm(doc.uri.fsPath);
  const rel = norm(vscode.workspace.asRelativePath(doc.uri, false));
  return full === ref || rel === ref || full.endsWith("/" + ref);
}

function sameFile(a: Finding, b: Finding): boolean {
  return (!!a.uri && a.uri === b.uri) || norm(a.file) === norm(b.file);
}

function findDocument(file: string): vscode.TextDocument | undefined {
  return vscode.workspace.textDocuments.find((d) => matches(d, file));
}

async function openByName(file: string): Promise<vscode.TextDocument | undefined> {
  if (path.isAbsolute(file)) return vscode.workspace.openTextDocument(file);
  const hits = await vscode.workspace.findFiles(`**/${file.replace(/\\/g, "/")}`, "**/node_modules/**", 1);
  return hits[0] ? vscode.workspace.openTextDocument(hits[0]) : undefined;
}
