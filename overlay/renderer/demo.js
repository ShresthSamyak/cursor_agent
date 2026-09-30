// Scripted demo (?demo=1): the hook and Act 1 booking beats from the PDF (p. 18), played as
// the same bridge frames the live runtime sends, so the overlay renders them through the
// normal code path. No bridge or network needed. Fares match trail/desktop/corpus/travel.json
// (fictional airline).
//
// Timeline items: {at, frame} a bridge frame, {at, cursor} move the ghost pointer to a fare
// cell (stage only), {at, user} show what the user said, {at, end: true} end of script.

const ROUTE = "Chandigarh → Goa";
const FARES = {
  fri: "Fri · ₹6,400",
  sat: "Sat · ₹5,000",
  sun: "Sun · ₹11,000",
  mon: "Mon · ₹4,500",
};

export function demoTimeline() {
  const items = [];
  let t = 0;
  let version = 1;
  const S = {
    agent_mode: false, perception: false, active_app: "chrome", specialist: "booking",
    phase: "listening", version: 1, ducked: false,
    focus: { intent: "none", slots: {} },
    goals: [], forks: [], calls: [], trail: [],
    latency: { time_to_yield_ms: null, last_response_ms: null },
    pending_notices: [],
  };
  const sent = [];

  const wait = ms => { t += ms; };
  const push = item => items.push({ at: t, ...item });
  const state = patch => {
    Object.assign(S, patch, { version: ++version });
    push({ frame: { type: "state", state: JSON.parse(JSON.stringify(S)) } });
  };
  const out = (turn, o) => push({ frame: { type: "output", output: { version, turn, meta: {}, ...o } } });
  const audit = () => push({ frame: { type: "audit",
    read: S.trail.map(e => ({ app: e.app, text: e.text, context: e.context })),
    sent_to_cloud: sent.slice() } });
  const dwell = key => {
    push({ cursor: key });
    wait(450);                                  // dwell threshold ~350 ms plus transit
    const entry = { app: "chrome", text: FARES[key], context: ROUTE, kind: "dwell" };
    out(0, { type: "status", code: "trail_added", meta: { text: entry.text, context: ROUTE, app: "chrome" } });
    state({ trail: [...S.trail, entry] });
  };
  const goal = (id, patch) => S.goals.map(g => (g.id === id ? { ...g, ...patch } : g));
  // Stream `text` word by word; `upto` cuts it off (the interrupted answer in beat b).
  const stream = (turn, text, every, upto = Infinity) => {
    out(turn, { type: "speak_start", kind: "final", text: "" });
    const chunks = text.match(/\S+\s*/g) || [];
    chunks.slice(0, upto).forEach(c => { wait(every); out(turn, { type: "token", text: c }); });
    if (upto >= chunks.length) { wait(every); out(turn, { type: "speak_end", kind: "final", text }); }
  };

  // Hook: agent mode on, the ring appears.
  state({});
  push({ cursor: "start" });
  wait(1400);
  state({ agent_mode: true, perception: true });

  // Beat a: hover Friday, Saturday, Sunday; ask which to book.
  wait(900); dwell("fri");
  wait(700); dwell("sat");
  wait(700); dwell("sun");
  wait(900);
  push({ user: "Which should I book?" });
  wait(250);
  S.goals = [{ id: "g1", intent: "book_flight", status: "active", slots: { route: ROUTE } }];
  state({ phase: "planning", focus: { intent: "book_flight", slots: { route: ROUTE } } });
  out(1, { type: "tool_call", call_id: "t1-flight_search", api_name: "flight_search", args: { destination: "Goa" },
           meta: { tag: "reversible", goal: "g1", step: "search" } });
  state({ phase: "tool_calls", calls: [{ call_id: "t1-flight_search", tool: "flight_search", tag: "reversible", status: "in_flight" }] });
  wait(160);
  out(1, { type: "speak", kind: "ack", text: "Comparing the three dates you checked." });
  wait(650);
  sent.push({ model: "cloud-llm", chars: 312, purpose: "answer from 3 trail entries" });
  S.goals = goal("g1", { slots: { route: ROUTE, date: "Saturday", passengers: 1 } });
  state({
    phase: "speaking",
    calls: [{ call_id: "t1-flight_search", tool: "flight_search", tag: "reversible", status: "committed" }],
    forks: [{ id: "f1", goal: "g1", hypothesis: { passengers: 2 }, status: "running" },
            { id: "f2", goal: "g1", hypothesis: { date: "Sunday" }, status: "running" }],
    latency: { time_to_yield_ms: 2.4, last_response_ms: 212 },
  });
  audit();
  stream(1, "Saturday at ₹5,000 is the cheapest of the three dates you checked. It leaves at 09:40 and lands at 12:35, nonstop, with four seats left.", 120, 9);

  // Beat b: while it is still talking, hover Monday at ₹4,500. It cuts itself off.
  push({ cursor: "mon" });
  S.forks = S.forks.map(f => ({ ...f, status: "ready" }));
  state({});
  wait(4 * 120);
  out(1, { type: "token", text: "It " });       // still streaming as the pointer lands
  wait(250);
  out(1, { type: "duck" });                     // any user activity ducks at once
  state({ ducked: true, latency: { ...S.latency, time_to_yield_ms: 3.1 } });
  wait(40);
  out(1, { type: "status", code: "trail_added", meta: { text: FARES.mon, context: ROUTE, app: "chrome" } });
  state({ trail: [...S.trail, { app: "chrome", text: FARES.mon, context: ROUTE, kind: "dwell" }] });
  wait(180);
  out(2, { type: "speak", kind: "notice", text: "Wait, Monday is cheaper at ₹4,500.", meta: { tier: "high" } });
  wait(260);
  S.goals = goal("g1", { slots: { route: ROUTE, date: "Monday", passengers: 1 } });
  state({
    ducked: false,
    forks: [{ id: "f1", goal: "g1", hypothesis: { passengers: 2 }, status: "dead" },
            { id: "f2", goal: "g1", hypothesis: { date: "Sunday" }, status: "dead" },
            { id: "f3", goal: "g1", hypothesis: { passengers: 2 }, status: "running" },
            { id: "f4", goal: "g1", hypothesis: { date: "Tuesday" }, status: "running" }],
    latency: { time_to_yield_ms: 3.1, last_response_ms: 188 },
  });
  out(2, { type: "unduck" });
  sent.push({ model: "cloud-llm", chars: 268, purpose: "revise answer: new cheapest fare" });
  audit();
  const revised = "Monday at ₹4,500 is now the cheapest of the four dates you checked, 06:30 to 09:25, nonstop.";
  out(2, { type: "speak_start", kind: "final", text: "" });
  wait(60);
  out(1, { type: "token", text: "leaves " });   // a late token from the old turn: the renderer drops it
  (revised.match(/\S+\s*/g) || []).forEach((c, i) => {
    wait(105); out(2, { type: "token", text: c });
    if (i === 4) state({ forks: S.forks.filter(f => f.status !== "dead").map(f => (f.id === "f3" ? { ...f, status: "ready" } : f)) });
    if (i === 9) state({ forks: S.forks.map(f => ({ ...f, status: "ready" })) });
  });
  wait(105); out(2, { type: "speak_end", kind: "final", text: revised });
  state({ phase: "listening" });

  // Beat c: "actually, two passengers". The fork lights up green; the answer lands at once.
  wait(1900);
  push({ user: "Actually, two passengers." });
  wait(320);
  out(3, { type: "status", code: "fork_hit", meta: { fork: "f3", hypothesis: { passengers: 2 } } });
  S.goals = goal("g1", { slots: { route: ROUTE, date: "Monday", passengers: 2 } });
  state({
    phase: "speaking",
    forks: [{ id: "f3", goal: "g1", hypothesis: { passengers: 2 }, status: "served" },
            { id: "f4", goal: "g1", hypothesis: { date: "Tuesday" }, status: "dead" }],
    latency: { time_to_yield_ms: 2.8, last_response_ms: 41 },
  });
  stream(3, "Two seats on Monday, ₹9,000 in total. Same flight, 06:30 nonstop.", 55);
  wait(700);
  state({ phase: "listening", forks: [{ id: "f5", goal: "g1", hypothesis: { date: "Tuesday" }, status: "running" }] });
  wait(900);
  state({ forks: [{ id: "f5", goal: "g1", hypothesis: { date: "Tuesday" }, status: "ready" }] });

  // Beat d: a detour to baggage parks the booking; "back to the flight" restores it intact.
  wait(1800);
  push({ user: "What's the baggage allowance?" });
  wait(300);
  S.goals = [...goal("g1", { status: "parked" }),
             { id: "g2", intent: "fare_rules", status: "active", slots: { airline: "Skylark Air", topic: "baggage" } }];
  out(4, { type: "tool_call", call_id: "t4-lookup_manual", api_name: "lookup_manual", args: { query: "baggage allowance" },
           meta: { tag: "reversible", goal: "g2", step: "lookup" } });
  state({
    phase: "tool_calls", focus: { intent: "fare_rules", slots: { topic: "baggage" } }, forks: [],
    calls: [...S.calls, { call_id: "t4-lookup_manual", tool: "lookup_manual", tag: "reversible", status: "in_flight" }],
  });
  wait(150);
  out(4, { type: "speak", kind: "ack", text: "Checking Skylark Air's fare rules." });
  wait(900);
  sent.push({ model: "cloud-llm", chars: 204, purpose: "fare rules: baggage" });
  state({
    phase: "speaking", latency: { time_to_yield_ms: 2.8, last_response_ms: 236 },
    calls: S.calls.map(c => (c.call_id === "t4-lookup_manual" ? { ...c, status: "committed" } : c)),
  });
  audit();
  stream(4, "Skylark Air allows 15 kg checked and 7 kg cabin baggage per passenger on this fare.", 95);
  state({ phase: "listening" });
  wait(2000);
  push({ user: "Back to the flight." });
  wait(300);
  S.goals = [{ id: "g1", intent: "book_flight", status: "active", slots: { route: ROUTE, date: "Monday", passengers: 2 } },
             { id: "g2", intent: "fare_rules", status: "done", slots: { airline: "Skylark Air", topic: "baggage" } }];
  state({
    phase: "speaking", focus: { intent: "book_flight", slots: S.goals[0].slots },
    forks: [{ id: "f6", goal: "g1", hypothesis: { date: "Tuesday" }, status: "ready" }],
    latency: { time_to_yield_ms: 2.8, last_response_ms: 36 },
  });
  stream(5, "Back to Monday: two passengers, Chandigarh to Goa, ₹9,000. Shall I book it?", 90);
  state({ phase: "listening" });

  // Beat e: "book it". Hold and book run; payment waits at the commit barrier.
  wait(2000);
  push({ user: "Book it." });
  wait(300);
  out(6, { type: "tool_call", call_id: "t6-hold_fare", api_name: "hold_fare", args: { flight_id: "SK-IXC-GOI-MON" },
           meta: { tag: "reversible", goal: "g1", step: "hold" } });
  state({ phase: "tool_calls", forks: [],
          calls: [...S.calls, { call_id: "t6-hold_fare", tool: "hold_fare", tag: "reversible", status: "in_flight" }] });
  wait(120);
  out(6, { type: "speak", kind: "ack", text: "Holding the fare first." });
  wait(900);
  out(6, { type: "tool_call", call_id: "t6-book_flight", api_name: "book_flight",
           args: { flight_id: "SK-IXC-GOI-MON", passenger_name: "2 passengers" },
           meta: { tag: "compensable", goal: "g1", step: "book" } });
  state({ calls: [...S.calls.map(c => (c.call_id === "t6-hold_fare" ? { ...c, status: "committed" } : c)),
                  { call_id: "t6-book_flight", tool: "book_flight", tag: "compensable", status: "in_flight" }] });
  wait(1100);
  out(6, { type: "status", code: "barrier_hold", meta: { tool: "pay_fare", goal: "g1" } });
  S.goals = goal("g1", { status: "waiting" });
  state({ phase: "listening",
          calls: S.calls.map(c => (c.call_id === "t6-book_flight" ? { ...c, status: "committed" } : c)),
          latency: { time_to_yield_ms: 2.8, last_response_ms: 164 } });
  wait(150);
  out(6, { type: "speak", kind: "clarify", text: "Booked and held. Paying ₹9,000 can't be undone. Should I pay now?" });
  sent.push({ model: "cloud-llm", chars: 188, purpose: "confirm before irreversible step" });
  audit();

  wait(9000);
  push({ end: true });
  return items;
}

/**
 * Play the timeline in real time. Returns {stop()}.
 * @param {{onFrame:Function, onCursor?:Function, onUser?:Function, onEnd?:Function, speed?:number}} h
 */
export function playDemo(h) {
  const items = demoTimeline();
  const speed = h.speed || 1;
  const timers = [];
  for (const item of items) {
    timers.push(setTimeout(() => {
      if (item.frame) h.onFrame(item.frame);
      else if (item.cursor) h.onCursor?.(item.cursor);
      else if (item.user) h.onUser?.(item.user);
      else if (item.end) h.onEnd?.();
    }, item.at / speed));
  }
  return { stop: () => timers.forEach(clearTimeout), duration: items.at(-1).at / speed };
}
