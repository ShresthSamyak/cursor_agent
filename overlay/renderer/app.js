// Overlay app: wires the bridge connection (or the scripted demo) to the pure view-model
// in model.js and renders the cursor bubble, perception ring, notice card, multiverse tree,
// latency counters and the session audit panel.

import { BridgeConnection, bridgeUrl } from "./connection.js";
import { playDemo } from "./demo.js";
import { appName, applyFrame, buildTree, createModel, formatMs, human, pushToast } from "./model.js";

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const isElectron = !!window.trailOverlay;
const demo = params.get("demo") === "1";
document.body.classList.add(isElectron ? "electron" : "browser");

const model = createModel();
const cursor = { x: innerWidth * 0.35, y: innerHeight * 0.45 };
let conn = null;
let flashFork = null;

// ---------------------------------------------------------------------------- input
function send(frame) {
  if (demo) return true;
  if (!conn || !conn.send(frame)) {
    pushToast(model, "Not connected to the Trail bridge.", "high");
    render(new Set(["toasts"]));
    return false;
  }
  return true;
}
const event = e => send({ type: "event", event: e });
const control = (action, extra = {}) => send({ type: "control", action, ...extra });

$("ask-form").addEventListener("submit", e => {
  e.preventDefault();
  const text = $("ask-input").value.trim();
  if (!text) return;
  model.user = { text, at: Date.now() };
  event({ type: "speech_final", text, app: "overlay" });
  $("ask-input").value = "";
});
$("btn-stop").onclick = () => event({ type: "cancel" });
$("btn-goon").onclick = () => event({ type: "resume" });
$("btn-notnow").onclick = () => event({ type: "not_now" });
$("btn-audit").onclick = () => {
  const panel = $("audit");
  panel.hidden = !panel.hidden;
  if (!panel.hidden) control("audit");
  render(new Set(["audit"]));
};
$("btn-agent").onclick = () => control("agent_mode", { on: !model.state.agent_mode });
$("btn-perception").onclick = () => control("perception", { on: !model.state.perception });
addEventListener("keydown", e => {
  if (e.key === "Escape") event({ type: "cancel" });
});

// Cursor: Electron sends the OS cursor; in a tab we follow the mouse.
if (isElectron) {
  window.trailOverlay.onCursor(p => { cursor.x = p.x; cursor.y = p.y; placeCursor(); });
  window.trailOverlay.onHotkey?.(name => {
    if (name === "agent") control("agent_mode", { on: !model.state.agent_mode });
    if (name === "perception") control("perception", { on: !model.state.perception });
    if (name === "cancel") event({ type: "cancel" });
  });
  // Click-through everywhere except the panel.
  const panel = $("panel");
  panel.addEventListener("mouseenter", () => window.trailOverlay.setInteractive(true));
  panel.addEventListener("mouseleave", () => window.trailOverlay.setInteractive(false));
} else {
  addEventListener("mousemove", e => { if (!demo) { cursor.x = e.clientX; cursor.y = e.clientY; placeCursor(); } });
}

// ---------------------------------------------------------------------------- frames
function onFrame(frame) {
  const { changed, effects } = applyFrame(model, frame);
  for (const fx of effects) {
    if (fx.type === "ripple") ripple(fx.tone);
    if (fx.type === "fork_hit") flashFork = `fork:${fx.fork}`;
  }
  render(changed);
}

function ripple(tone) {
  const ring = $("ring");
  ring.classList.remove("ripple", "red");
  void ring.offsetWidth;
  ring.classList.add("ripple");
  if (tone === "red") ring.classList.add("red");
}

// ---------------------------------------------------------------------------- render
function render(changed = new Set(["all"])) {
  const all = changed.has("all");
  if (all || changed.has("header")) renderHeader();
  if (all || changed.has("ring") || changed.has("bubble")) renderCursor();
  if (all || changed.has("tree")) renderTree();
  if (all || changed.has("latency") || changed.has("tree")) renderLatency();
  if (all || changed.has("audit")) renderAudit();
  if (all || changed.has("toasts")) renderToasts();
}

function renderHeader() {
  const s = model.state;
  const status = model.conn.status;
  $("conn-dot").className = `dot ${status}`;
  $("conn-text").textContent = { live: "connected", demo: "demo replay", retrying: "reconnecting…",
    refused: "token refused", connecting: "connecting…", idle: "offline" }[status] || status;
  $("chip-app").textContent = s.active_app ? appName(s.active_app) : "no app";
  $("chip-phase").textContent = human(s.phase);
  $("btn-agent").setAttribute("aria-pressed", String(s.agent_mode));
  $("btn-agent").textContent = s.agent_mode ? "Agent on" : "Agent off";
  $("btn-perception").setAttribute("aria-pressed", String(s.perception));
  $("btn-perception").textContent = s.perception ? "Perceiving" : "Paused";
}

function renderCursor() {
  const s = model.state;
  const ring = $("ring");
  ring.hidden = !(s.agent_mode && s.perception);        // the visible privacy signal
  ring.classList.toggle("ducked", model.ducked);

  const b = model.bubble;
  const bubble = $("bubble");
  const show = b.mode !== "idle" && (b.text || b.ack) && Date.now() - b.at < 30000;
  bubble.hidden = !show;
  if (show) {
    bubble.className = `bubble ${b.mode}${model.ducked ? " ducked" : ""}${b.fromFork ? " fork" : ""}`;
    $("bubble-ack").textContent = b.mode === "ack" ? b.ack : "";
    const text = $("bubble-text");
    text.textContent = b.mode === "ack" ? "" : b.text;
    if (b.streaming) text.insertAdjacentHTML("beforeend", '<span class="caret"></span>');
    $("bubble-meta").textContent = b.fromFork ? "served from a speculative fork" :
      model.ducked ? "paused while you talk" : b.mode === "clarify" ? "waiting for you" : "";
  }
  const n = model.notice;
  const notice = $("notice");
  const showNotice = n && Date.now() - n.at < 12000;
  notice.hidden = !showNotice;
  if (showNotice) {
    notice.className = `notice ${n.tier}`;
    notice.innerHTML = "";
    const tier = document.createElement("span");
    tier.className = "tier";
    tier.textContent = { critical: "Interrupting now", high: "At your pause", normal: "At a boundary", low: "Digest" }[n.tier] || n.tier;
    notice.append(tier, document.createTextNode(n.text));
  }
  placeCursor();
}

function placeCursor() {
  const ring = $("ring");
  ring.style.left = `${cursor.x}px`;
  ring.style.top = `${cursor.y}px`;
  const bubble = $("bubble");
  const w = bubble.offsetWidth || 300;
  const left = Math.min(cursor.x + 26, innerWidth - w - 16);
  const top = Math.min(cursor.y + 22, innerHeight - (bubble.offsetHeight || 80) - 16);
  bubble.style.left = `${Math.max(12, left)}px`;
  bubble.style.top = `${Math.max(12, top)}px`;
  const notice = $("notice");
  notice.style.left = `${Math.max(12, left)}px`;
  notice.style.top = `${Math.max(12, top - (notice.offsetHeight || 50) - 10)}px`;
}

const ICONS = { root: "◎", goal: "●", parked: "⏸", call: "⇄", barrier: "🔒", fork: "⑂" };

function renderTree() {
  const tree = $("tree");
  const { rows, empty } = buildTree(model);
  tree.innerHTML = "";
  if (empty) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "Hover something and ask a question; goals, forks and tool calls appear here.";
    tree.append(e);
  }
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = `row ${r.kind} depth-${r.depth}${r.lit ? " lit" : ""} status-${r.status || ""}`;
    if (flashFork && r.key === flashFork) row.classList.add("flash");
    const ico = document.createElement("span");
    ico.className = "ico";
    ico.textContent = r.kind === "fork" && r.lit ? "✓" : ICONS[r.kind] || "•";
    const lbl = document.createElement("span");
    lbl.className = "lbl";
    lbl.textContent = r.kind === "call" || r.kind === "barrier" ? human(r.label) : r.label;
    row.append(ico, lbl);
    if (r.tag) {
      const tag = document.createElement("span");
      tag.className = `tag ${r.tag}`;
      tag.textContent = r.tag;
      row.append(tag);
    }
    const sub = document.createElement("span");
    sub.className = "sub";
    sub.textContent = r.kind === "call" ? (r.sub || human(r.status)) : r.kind === "fork" ? (r.lit ? "served" : human(r.status)) : r.sub || "";
    row.append(sub);
    tree.append(row);
  }
  flashFork = null;
}

function renderLatency() {
  const lat = model.state.latency || {};
  const put = (id, ms) => {
    const f = formatMs(ms);
    $(id).innerHTML = f ? `${f.value}<small>${f.unit}</small>` : "–";
  };
  put("lat-yield", lat.time_to_yield_ms);
  put("lat-resp", lat.last_response_ms);
  $("lat-forks").textContent = String(model.served.size);
}

function renderAudit() {
  const read = $("audit-read");
  const sent = $("audit-sent");
  read.innerHTML = "";
  sent.innerHTML = "";
  const items = model.audit.read.length ? model.audit.read : model.state.trail;
  for (const e of items.slice(-20)) {
    const li = document.createElement("li");
    li.textContent = `${appName(e.app)}: ${e.text}${e.context ? ` (${e.context})` : ""}`;
    read.append(li);
  }
  for (const e of model.audit.sent) {
    const li = document.createElement("li");
    li.textContent = e.model ? `${e.model}${e.calls != null ? `, ${e.calls} calls` : ""}${e.chars ? `, ${e.chars} chars` : ""}` : JSON.stringify(e);
    sent.append(li);
  }
  if (!model.audit.sent.length) {
    const li = document.createElement("li");
    li.textContent = "Nothing sent to a cloud model.";
    sent.append(li);
  }
}

function renderToasts() {
  const box = $("toasts");
  box.innerHTML = "";
  const now = Date.now();
  for (const t of model.toasts.filter(t => now - t.at < 6000)) {
    const d = document.createElement("div");
    d.className = `toast ${t.tone || ""}`;
    d.textContent = t.text;
    box.append(d);
  }
}

// ---------------------------------------------------------------------------- start
if (demo) {
  model.conn = { status: "demo", detail: "" };
  const stage = buildStage();
  const ghost = document.createElement("div");
  ghost.className = "ghost";
  document.body.append(ghost);
  const moveTo = key => {
    const el = stage[key] || stage.start;
    const r = el.getBoundingClientRect();
    cursor.x = r.left + r.width / 2;
    cursor.y = r.top + r.height / 2;
    ghost.style.left = `${cursor.x}px`;
    ghost.style.top = `${cursor.y}px`;
    placeCursor();
  };
  moveTo("start");
  const run = () => playDemo({ onFrame, onCursor: moveTo, onUser: u => { model.user = { text: u, at: Date.now() }; },
                               onEnd: () => setTimeout(run, 4000), speed: Number(params.get("speed")) || 1 });
  run();
} else {
  conn = new BridgeConnection({
    url: bridgeUrl({ token: params.get("token"), bridge: params.get("bridge"), location }),
    onFrame,
    onStatus: (status, detail) => { model.conn = { status, detail: detail || "" }; render(new Set(["header"])); },
  });
  conn.start();
}
setInterval(() => render(new Set(["bubble", "toasts"])), 1000);
render();

function buildStage() {
  // A minimal fare strip so demo mode has something to point at (the real page is /demo/flights).
  const strip = document.createElement("div");
  strip.style.cssText = "position:fixed;left:48px;top:120px;display:flex;gap:14px;z-index:1;font:15px system-ui";
  const title = document.createElement("div");
  title.style.cssText = "position:fixed;left:48px;top:70px;font:600 20px system-ui;color:#dfe5ff";
  title.textContent = "Skylark Air · Chandigarh → Goa";
  document.body.append(title, strip);
  const cells = {};
  for (const [key, text] of [["fri", "Fri · ₹6,400"], ["sat", "Sat · ₹5,000"], ["sun", "Sun · ₹11,000"], ["mon", "Mon · ₹4,500"]]) {
    const c = document.createElement("div");
    c.textContent = text;
    c.style.cssText = "padding:18px 20px;border-radius:12px;background:#1a2033;border:1px solid #2c3552;color:#eef1f7";
    strip.append(c);
    cells[key] = c;
  }
  cells.start = title;
  return cells;
}
