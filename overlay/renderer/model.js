// Pure view-model for the overlay. No DOM access, so it runs (and is tested) in Node too.
//
// applyFrame() folds one bridge frame (docs/bridge-protocol.md) into the model and
// returns which parts of the UI changed plus one-shot effects (ripples, branch lighting).
// buildTree() turns the model into flat rows for the multiverse view.

export const TIERS = ["critical", "high", "normal", "low"];
const SERVED_MEMORY_MS = 9000;      // keep a served fork lit after the core drops it
const DUCK_OUTPUT_WINS_MS = 400;    // state.ducked may lag the immediate duck/unduck output
const MAX_CALLS_SHOWN = 6;

export function createModel() {
  return {
    conn: { status: "idle", detail: "" },       // idle | connecting | live | retrying | refused | demo
    state: normalizeState({}),
    stateSeen: false,
    ducked: false,
    duckChangedAt: -Infinity,
    // The bubble shows one thing at a time; the notice card stacks above it.
    bubble: { mode: "idle", text: "", ack: "", turn: -1, streaming: false, fromFork: false, at: 0 },
    held: [],                                   // tokens that arrived while ducked (same turn)
    streamTurn: -1,                             // turn of the last speak_start; older tokens are dropped
    droppedTokens: 0,
    notice: null,                               // {text, tier, at}
    user: null,                                 // {text, at} last thing the user asked (typed or scripted)
    calls: new Map(),                           // call_id -> {tool, tag, goal, status, undoes, step}
    served: new Map(),                          // fork id -> {goal, hypothesis, at}
    forkHitTurn: -1,
    barrier: null,                              // {tool, text, goal, at}
    audit: { read: [], sent: [], dropped: [], at: 0 },
    rejected: 0,
    toasts: [],                                 // {id, text, tone, at}
    toastSeq: 0,
  };
}

export function normalizeState(s) {
  return {
    agent_mode: !!s.agent_mode,
    perception: !!s.perception,
    active_app: s.active_app || "",
    specialist: s.specialist || "",
    phase: s.phase || "listening",
    version: s.version ?? 0,
    ducked: !!s.ducked,
    focus: s.focus || { intent: "none", slots: {} },
    goals: Array.isArray(s.goals) ? s.goals : [],
    forks: Array.isArray(s.forks) ? s.forks : [],
    calls: Array.isArray(s.calls) ? s.calls : [],
    trail: Array.isArray(s.trail) ? s.trail : [],
    latency: s.latency || {},
    pending_notices: Array.isArray(s.pending_notices) ? s.pending_notices : [],
    barrier: s.barrier ?? undefined,            // not in protocol v1; honoured if a bridge adds it
  };
}

/** Fold one frame into the model. Returns {changed:Set<string>, effects:Array}. */
export function applyFrame(m, frame, now = Date.now()) {
  const changed = new Set();
  const effects = [];
  if (!frame || typeof frame !== "object") return { changed, effects };

  switch (frame.type) {
    case "state":
      applyState(m, frame.state || {}, now, changed);
      break;
    case "output":
      applyOutput(m, frame.output || {}, now, changed, effects);
      break;
    case "audit":
      m.audit = {
        read: Array.isArray(frame.read) ? frame.read : [],
        sent: Array.isArray(frame.sent_to_cloud) ? frame.sent_to_cloud : [],
        dropped: Array.isArray(frame.dropped) ? frame.dropped : [],
        at: now,
      };
      changed.add("audit");
      break;
    case "error":
      pushToast(m, errorText(frame), frame.code === "unauthorized" ? "critical" : "high", now);
      if (frame.code === "unauthorized") m.conn = { status: "refused", detail: "" };
      changed.add("toasts").add("header");
      break;
    default:
      break; // unknown frame types are ignored, per protocol
  }
  return { changed, effects };
}

function applyState(m, raw, now, changed) {
  const prev = m.state;
  const s = normalizeState(raw);
  m.state = s;
  m.stateSeen = true;

  // The immediate duck/unduck output is authoritative; the throttled snapshot only corrects drift.
  if (s.ducked !== m.ducked && now - m.duckChangedAt > DUCK_OUTPUT_WINS_MS) {
    setDucked(m, s.ducked, now);
    changed.add("bubble");
  }

  for (const c of s.calls) {
    const info = m.calls.get(c.call_id) || {};
    m.calls.set(c.call_id, { ...info, tool: c.tool || info.tool, tag: c.tag || info.tag, status: c.status || info.status,
                             goal: c.goal || info.goal });
  }

  if (s.barrier !== undefined) {
    m.barrier = s.barrier ? { ...s.barrier, tool: s.barrier.tool || s.barrier.api_name, at: m.barrier?.at ?? now } : null;
  } else if (m.barrier) {
    // A held call that has since been issued (or a session with no live goal) is no longer held.
    const issued = s.calls.some(c => c.tool === m.barrier.tool && c.status !== "held");
    const live = s.goals.some(g => g.status === "active" || g.status === "waiting");
    if (issued || !live) m.barrier = null;
  }

  for (const [id, f] of m.served) if (now - f.at > SERVED_MEMORY_MS) m.served.delete(id);

  changed.add("tree").add("header").add("ring").add("latency");
  if (prev.trail.length !== s.trail.length) changed.add("audit");
}

function applyOutput(m, o, now, changed, effects) {
  const turn = Number.isFinite(o.turn) ? o.turn : 0;
  switch (o.type) {
    case "speak_start":
      if (turn < m.streamTurn) return;          // a start from an older turn is stale
      m.streamTurn = turn;
      m.held = [];
      m.bubble = { mode: "answer", text: "", ack: "", turn, streaming: true, fromFork: m.forkHitTurn === turn, at: now };
      changed.add("bubble");
      return;

    case "token": {
      if (turn < m.streamTurn) { m.droppedTokens++; return; }   // protocol: drop tokens from older turns
      if (turn > m.streamTurn) {                                 // token without its start: open the stream
        m.streamTurn = turn;
        m.held = [];
        m.bubble = { mode: "answer", text: "", ack: "", turn, streaming: true, fromFork: m.forkHitTurn === turn, at: now };
      }
      if (!m.bubble.streaming || m.bubble.turn !== turn) { m.droppedTokens++; return; }
      if (m.ducked) m.held.push(o.text || "");
      else m.bubble.text += o.text || "";
      m.bubble.at = now;
      changed.add("bubble");
      return;
    }

    case "speak_end":
      if (turn < m.streamTurn || m.bubble.turn !== turn) return;
      if (m.ducked) return;                     // the rest shows on unduck; speak_end text is the whole answer
      m.bubble.text = o.text || m.bubble.text + m.held.join("");
      m.held = [];
      m.bubble.streaming = false;
      m.bubble.at = now;
      changed.add("bubble");
      return;

    case "speak":
      applySpeak(m, o, turn, now, changed, effects);
      return;

    case "duck":
      setDucked(m, true, now);
      effects.push({ type: "duck" });
      changed.add("bubble").add("ring");
      return;

    case "unduck":
      setDucked(m, false, now);
      changed.add("bubble").add("ring");
      return;

    case "tool_call": {
      const meta = o.meta || {};
      const prev = m.calls.get(o.call_id) || {};
      m.calls.set(o.call_id, { ...prev, tool: o.api_name || prev.tool, tag: meta.tag || prev.tag, goal: meta.goal || prev.goal,
                               status: prev.status || "in_flight", undoes: meta.undoes, step: meta.step });
      if (m.barrier && m.barrier.tool === o.api_name) m.barrier = null;
      changed.add("tree");
      return;
    }

    case "tool_cancel": {
      const prev = m.calls.get(o.call_id);
      if (prev) prev.status = "cancelled";
      changed.add("tree");
      return;
    }

    case "status":
      applyStatus(m, o, turn, now, changed, effects);
      return;

    default:
      return;
  }
}

function applySpeak(m, o, turn, now, changed, effects) {
  const kind = o.kind || "final";
  const text = o.text || "";
  if (kind === "notice") {
    // Agent-initiated; valid whatever the turn. The tier decides its colour.
    const tier = TIERS.includes(o.meta?.tier) ? o.meta.tier : "normal";
    m.notice = { text, tier, at: now };
    effects.push({ type: "notice", tier });
    changed.add("bubble");
    return;
  }
  if (turn < m.streamTurn) return;              // whole utterances from older turns are stale too
  if (kind === "ack") {
    // A filler covers slow work; it never replaces an answer already on screen for this turn.
    if (m.bubble.mode === "answer" && m.bubble.turn === turn && m.bubble.text) return;
    m.bubble = { mode: "ack", text: "", ack: text, turn, streaming: false, fromFork: false, at: now };
  } else {
    m.streamTurn = Math.max(m.streamTurn, turn);
    m.held = [];
    m.bubble = { mode: kind === "clarify" ? "clarify" : "answer", text, ack: "", turn, streaming: false,
                 fromFork: m.forkHitTurn === turn, at: now };
  }
  changed.add("bubble");
}

function applyStatus(m, o, turn, now, changed, effects) {
  const meta = o.meta || {};
  switch (o.code) {
    case "fork_hit":
      if (meta.fork) {
        const known = m.state.forks.find(f => f.id === meta.fork);
        m.served.set(meta.fork, { goal: known?.goal, hypothesis: meta.hypothesis || known?.hypothesis || {}, at: now });
        effects.push({ type: "fork_hit", fork: meta.fork });
      }
      m.forkHitTurn = turn;
      if (m.bubble.turn === turn) m.bubble.fromFork = true;
      changed.add("tree").add("bubble").add("latency");
      return;
    case "trail_added":
      effects.push({ type: "ripple", tone: "iris" });
      changed.add("audit");
      return;
    case "context_rejected":
      m.rejected++;
      effects.push({ type: "ripple", tone: "red" });
      pushToast(m, "Skipped a sensitive field. It never reached the agent.", "normal", now);
      changed.add("toasts").add("audit");
      return;
    case "barrier_hold": {
      const tool = meta.tool || meta.api_name || o.api_name || "irreversible step";
      m.barrier = { tool, text: o.text || meta.text || "", goal: meta.goal, at: now };
      effects.push({ type: "barrier" });
      changed.add("tree");
      return;
    }
    default:
      return;
  }
}

function setDucked(m, ducked, now) {
  if (m.ducked === ducked) return;
  m.ducked = ducked;
  m.duckChangedAt = now;
  if (!ducked && m.held.length) {
    m.bubble.text += m.held.join("");
    m.held = [];
  }
}

export function pushToast(m, text, tone, now = Date.now()) {
  m.toasts.push({ id: ++m.toastSeq, text, tone, at: now });
  if (m.toasts.length > 3) m.toasts.shift();
}

function errorText(f) {
  switch (f.code) {
    case "unauthorized":
      return "The bridge refused the token. Reopen with ?token= set to the value in bridge.token.";
    case "rate_limited":
      return "The bridge is dropping a burst of frames (rate limit).";
    case "bad_frame":
      return "The bridge could not read a frame from the overlay.";
    default:
      return f.text || `Bridge error: ${f.code || "unknown"}`;
  }
}

// ---------------------------------------------------------------------------- tree

/**
 * Flatten goals, forks, saga calls and barrier holds into rows.
 * depth 0 rows sit on the trunk; depth 1 rows are branches drawn from row `from`.
 */
export function buildTree(m, now = Date.now()) {
  const s = m.state;
  const goals = s.goals;
  const trunk = goals.find(g => g.status === "active")
    || goals.find(g => g.status === "waiting")
    || [...goals].reverse().find(g => g.status === "done" && g.intent === s.focus?.intent)
    || null;

  const rows = [];
  rows.push({
    key: "root", kind: "root", depth: 0,
    label: s.specialist ? `${human(s.specialist)} specialist` : "This session",
    sub: s.active_app ? `Watching ${appName(s.active_app)}` : "",
  });

  for (const g of goals.filter(g => g.status === "parked")) {
    rows.push({ key: `goal:${g.id}`, kind: "parked", depth: 1, from: 0, label: human(g.intent),
                sub: slotSummary(g.slots), status: "parked" });
  }

  if (!trunk) return { rows, trunkGoal: null, empty: goals.length === 0 };

  rows.push({ key: `goal:${trunk.id}`, kind: "goal", depth: 0, label: human(trunk.intent),
              sub: slotSummary(trunk.slots), status: trunk.status });

  // Saga calls for this goal, oldest first. The core's state carries no goal id, so calls
  // are matched through the tool_call output's meta.goal when we saw it.
  const calls = s.calls
    .map(c => ({ ...c, ...pick(m.calls.get(c.call_id)), status: c.status || m.calls.get(c.call_id)?.status }))
    .filter(c => !c.goal || c.goal === trunk.id)
    .slice(-MAX_CALLS_SHOWN);
  for (const c of calls) {
    rows.push({ key: `call:${c.call_id}`, kind: "call", depth: 0, label: c.tool || c.call_id,
                tag: c.tag || "reversible", status: c.status || "in_flight",
                sub: c.undoes ? `Undoes ${m.calls.get(c.undoes)?.tool || c.undoes}` : "" });
  }

  if (m.barrier && (!m.barrier.goal || m.barrier.goal === trunk.id)) {
    rows.push({ key: `barrier:${m.barrier.tool}`, kind: "barrier", depth: 0, label: m.barrier.tool,
                tag: "irreversible", status: "held", sub: "Held until you say yes" });
  }

  // Forks branch from the tip of the trunk.
  const tip = rows.length - 1;
  const seen = new Set();
  for (const f of s.forks.filter(f => !f.goal || f.goal === trunk.id)) {
    seen.add(f.id);
    const lit = m.served.has(f.id) || f.status === "served";
    if (f.status === "dead" && !lit) continue;
    rows.push({ key: `fork:${f.id}`, kind: "fork", depth: 1, from: tip, hypothesis: f.hypothesis || {},
                label: hypothesisText(f.hypothesis), status: lit ? "served" : f.status || "running", lit });
  }
  for (const [id, f] of m.served) {
    if (seen.has(id) || (f.goal && f.goal !== trunk.id) || now - f.at > SERVED_MEMORY_MS) continue;
    rows.push({ key: `fork:${id}`, kind: "fork", depth: 1, from: tip, hypothesis: f.hypothesis,
                label: hypothesisText(f.hypothesis), status: "served", lit: true });
  }

  return { rows, trunkGoal: trunk, empty: false };
}

function pick(info) {
  if (!info) return {};
  const out = {};
  for (const k of ["tool", "tag", "goal", "undoes"]) if (info[k] != null) out[k] = info[k];
  return out;
}

// ---------------------------------------------------------------------------- text helpers

export function human(id) {
  if (!id) return "";
  const s = String(id).replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function appName(app) {
  const names = { chrome: "Chrome", vscode: "VS Code", excel: "Excel", pdf: "PDF viewer", overlay: "Trail" };
  return names[app] || human(app);
}

export function formatValue(v) {
  if (v == null) return "";
  if (typeof v === "object") {
    if ("value" in v) return formatValue(v.value);
    return Object.values(v).map(formatValue).filter(Boolean).join(" ");
  }
  return String(v);
}

export function slotSummary(slots) {
  if (!slots || typeof slots !== "object") return "";
  const parts = [];
  for (const [k, raw] of Object.entries(slots)) {
    if (k.startsWith("_")) continue;
    const v = formatValue(raw);
    if (!v) continue;
    if (k === "passengers") parts.push(`${v} ${Number(v) === 1 ? "passenger" : "passengers"}`);
    else parts.push(v);
    if (parts.length === 4) break;
  }
  return parts.join(", ");
}

export function hypothesisText(h) {
  const entries = Object.entries(h || {});
  if (!entries.length) return "Alternative";
  return entries.map(([k, v]) => {
    const val = formatValue(v);
    return k === "passengers" ? `${val} ${Number(val) === 1 ? "passenger" : "passengers"}` : `${human(k)} ${val}`;
  }).join(", ");
}

/** Latency for display: {value, unit}, or null when the bridge has not measured it yet. */
export function formatMs(ms) {
  if (ms == null || ms === "" || !Number.isFinite(Number(ms))) return null;
  const n = Number(ms);
  if (n < 10) return { value: n.toFixed(1), unit: "ms" };
  if (n < 10000) return { value: String(Math.round(n)), unit: "ms" };
  return { value: (n / 1000).toFixed(1), unit: "s" };
}
