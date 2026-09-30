// Content script: turns the element under the pointer into Trail perception events
// (docs/bridge-protocol.md). Hover when the pointer enters a new target, dwell after ~350 ms
// on the same one, nothing while the pointer is sweeping fast. Sensitive fields never leave.


const DWELL_MS = 350;
const FAST_PX_PER_MS = 1.2;          // faster than this is transit, not attention
const HOVER_DEBOUNCE_MS = 120;
const DATE_RE = /\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\b|\b\d{1,2}[ /-](?:\d{1,2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b/i;
const PRICE_RE = /(?:₹|\$|€|£|rs\.?|inr|usd)\s*\d[\d,]*(?:\.\d+)?/i;

let target: Element | null = null;
let dwellTimer: number | undefined;
let hoverTimer: number | undefined;
let last = { x: 0, y: 0, t: 0 };
let paused = false;

function send(event: Record<string, unknown>): void {
  if (paused) return;
  try {
    chrome.runtime.sendMessage({ kind: "trail-event", event });
  } catch {
    /* extension reloaded; ignore */
  }
}

function isSensitive(el: Element): boolean {
  if (el.closest("[data-sensitive]")) return true;
  const input = el.closest("input, textarea, select") as HTMLInputElement | null;
  if (!input) return false;
  const name = `${input.name} ${input.id} ${input.getAttribute("autocomplete") || ""}`.toLowerCase();
  return input.type === "password" || /cc-|card|cvv|cvc|otp|pin|iban|ssn|password|one-time/.test(name);
}

function visibleText(el: Element): string {
  const t = (el as HTMLElement).innerText ?? el.textContent ?? "";
  return t.replace(/\s+/g, " ").trim().slice(0, 300);
}

/** The smallest meaningful element: prefer one that pairs a date and a price (a fare cell). */
function meaningful(el: Element): Element {
  let cur: Element | null = el;
  for (let depth = 0; cur && depth < 5; depth++, cur = cur.parentElement) {
    const text = visibleText(cur);
    if (text.length > 160) break;
    if (DATE_RE.test(text) && PRICE_RE.test(text)) return cur;
  }
  return el;
}

/** Nearest labelled container: aria-label, data-route, table caption, or a heading above it. */
function contextOf(el: Element): string {
  let cur: Element | null = el.parentElement;
  while (cur && cur !== document.body) {
    const label = cur.getAttribute("aria-label") || (cur as HTMLElement).dataset?.route;
    if (label) return label.slice(0, 200);
    const caption = cur.querySelector(":scope > caption, :scope > h1, :scope > h2, :scope > h3");
    if (caption && !caption.contains(el)) return visibleText(caption).slice(0, 200);
    cur = cur.parentElement;
  }
  return document.title.slice(0, 200);
}

function roleOf(el: Element): string {
  const role = el.getAttribute("role");
  if (role) return role;
  const tag = el.tagName.toLowerCase();
  return { td: "cell", th: "cell", a: "link", button: "button", img: "image" }[tag] || "text";
}

function targetPayload(el: Element, dwellMs: number) {
  const r = el.getBoundingClientRect();
  const sx = window.screenX + (window.outerWidth - window.innerWidth);
  const sy = window.screenY + (window.outerHeight - window.innerHeight);
  return {
    role: roleOf(el), text: visibleText(el), context: contextOf(el), url: location.href.slice(0, 1000),
    bbox: [Math.round(r.left + sx), Math.round(r.top + sy), Math.round(r.right + sx), Math.round(r.bottom + sy)],
    dwell_ms: dwellMs, sensitive: isSensitive(el),
  };
}

document.addEventListener("mousemove", (e: MouseEvent) => {
  const now = performance.now();
  const speed = Math.hypot(e.clientX - last.x, e.clientY - last.y) / Math.max(now - last.t, 1);
  last = { x: e.clientX, y: e.clientY, t: now };
  const raw = document.elementFromPoint(e.clientX, e.clientY);
  if (!raw) return;
  const el = meaningful(raw);
  if (el === target) return;
  target = el;
  clearTimeout(dwellTimer);
  clearTimeout(hoverTimer);
  if (speed > FAST_PX_PER_MS || isSensitive(el)) return;      // transit or sensitive: nothing is sent
  const text = visibleText(el);
  if (!text) return;
  hoverTimer = window.setTimeout(() => send({ type: "hover", app: "chrome", target: targetPayload(el, 0) }), HOVER_DEBOUNCE_MS);
  dwellTimer = window.setTimeout(() => {
    if (target === el) send({ type: "dwell", app: "chrome", target: targetPayload(el, DWELL_MS) });
  }, DWELL_MS);
}, { passive: true });

document.addEventListener("selectionchange", () => {
  const sel = document.getSelection();
  const text = sel?.toString().trim();
  if (!sel || !text || !sel.anchorNode) return;
  const el = sel.anchorNode.parentElement;
  if (!el || isSensitive(el)) return;
  clearTimeout((window as any).__trailSel);
  (window as any).__trailSel = setTimeout(() => send({ type: "select", app: "chrome",
    target: { ...targetPayload(el, 0), text: text.slice(0, 2000) } }), 400);
});

document.addEventListener("keydown", (e: KeyboardEvent) => {
  if (e.key === "Escape") send({ type: "cancel", app: "chrome" });       // only Esc; key contents are never read
}, true);

window.addEventListener("focus", () => send({ type: "app_switch", app: "chrome" }));

// Fallback renderer: a small toast near the pointer when the overlay is not running.
chrome.runtime.onMessage.addListener((msg: any) => {
  if (msg?.kind === "trail-paused") { paused = !!msg.paused; return; }
  if (msg?.kind !== "trail-output") return;
  const o = msg.output;
  if ((o?.type !== "speak" && o?.type !== "speak_end") || !o.text) return;
  const toast = document.createElement("div");
  toast.textContent = o.text;
  const tier = o.meta?.tier;
  toast.style.cssText = `position:fixed;left:${Math.min(last.x + 18, innerWidth - 340)}px;top:${Math.min(last.y + 18, innerHeight - 80)}px;` +
    "max-width:320px;padding:10px 12px;border-radius:12px;background:rgba(16,20,30,.94);color:#eef1f7;z-index:2147483647;" +
    `font:14px system-ui;box-shadow:0 10px 30px rgba(0,0,0,.35);border-left:4px solid ${tier === "critical" ? "#ff5d6c" : tier === "high" ? "#ffb547" : "#7c8cff"}`;
  document.documentElement.append(toast);
  setTimeout(() => toast.remove(), 7000);
});
