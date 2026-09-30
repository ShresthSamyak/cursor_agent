# Trail desktop bridge protocol, version 1

The bridge (`python -m trail bridge`) is a WebSocket hub on `127.0.0.1` only.
Every perception source (Chrome extension, VS Code extension, UI Automation,
microphone) sends events in; the overlay and the extensions receive the agent's
output. The runtime behind it is the same one the kit harness scores; only the
adapters differ (PDF p. 6-7).

## Connection

```
ws://127.0.0.1:8765/ws?client=<chrome|vscode|overlay|uia|speech|cli>&token=<token>
```

* The bridge refuses non-loopback peers and any connection without the token.
* The token is printed at startup and written to `%LOCALAPPDATA%\Trail\bridge.token`
  (or `~/.trail/bridge.token`). `python -m trail bridge --dev` uses the fixed token
  `trail-dev` for local development. Browser extensions take it from their options page.
* All frames are UTF-8 JSON text frames. Unknown fields are ignored; malformed frames get
  `{"type": "error", "code": "bad_frame"}` and the connection stays open.
* The bridge also serves the demo pages over plain HTTP on the same port:
  `http://127.0.0.1:8765/demo/flights` (fictional airline results page) and
  `http://127.0.0.1:8765/demo/budget` (budget sheet stand-in for Excel).

## Client -> bridge

### hello (first frame from every client)
```json
{"type": "hello", "client": "chrome", "version": 1, "app": "chrome"}
```

### event: one perception event, the bus envelope
```json
{"type": "event", "event": {
  "type": "dwell", "app": "chrome", "source": "extension",
  "target": {"role": "cell", "text": "Sat · ₹5,000", "context": "Chandigarh → Goa",
             "bbox": [812, 440, 960, 470], "dwell_ms": 420, "url": "http://127.0.0.1:8765/demo/flights",
             "sensitive": false}}}
```

Event types and their payload fields (all optional unless noted):

| type | sent by | fields |
| --- | --- | --- |
| `dwell` | chrome, uia, vscode | `target` (required): text, context, role, bbox, dwell_ms, url, sensitive |
| `hover` | chrome, uia, vscode | `target` (required). Debounced pointer entry; prefetch hint only |
| `select` | chrome, vscode, uia | `target` (required): the selected text / cells |
| `app_switch` | uia, chrome, vscode | `app`: `chrome`, `vscode`, `excel`, `pdf`, ... |
| `typing` | vscode, uia | `active` (required): true when a burst starts, false when it ends |
| `save` | vscode | `data`: `{"file": "auth.py", "version": 12}` |
| `test_run` | vscode | `data`: `{"passed": false, "summary": "1 failed"}` |
| `doc_change` | vscode | `data`: `{"file": "auth.py", "version": 13, "language": "python", "text": "<full document>", "changed": [{"line": 41, "text": "..."}], "diagnostics": [{"line": 41, "severity": "error", "message": "...", "source": "pylint"}]}` |
| `terminal` | vscode | `text`: terminal output chunk (stack traces trigger pre-diagnosis) |
| `speech_partial` | speech, cli | `text` |
| `speech_final` | speech, cli, overlay | `text` (typed questions from the overlay use this too) |
| `vad_start` / `vad_end` | speech | none |
| `cancel` | any (Esc) | none |
| `resume` | overlay ("go on") | none |
| `not_now` | overlay, vscode | none |

Rules every source must follow (PDF p. 15-16):
* Never send password fields, payment fields, one-time codes, or anything marked sensitive;
  set `"sensitive": true` if unsure and the core will drop it.
* Outside VS Code, typing is sent as timing only (`typing` events); key contents never leave the machine.
* Dwell fires after ~350 ms on the same element; fast pointer transit is not sent.

### control
```json
{"type": "control", "action": "agent_mode", "on": true}
{"type": "control", "action": "perception", "on": false}
{"type": "control", "action": "specialist", "name": "booking"}
{"type": "control", "action": "mode", "name": "teach"}
{"type": "control", "action": "confirm", "answer": true}
```

## Bridge -> client

### output: one runtime output
```json
{"type": "output", "output": {"type": "speak", "kind": "final", "text": "...", "snapshot": {...},
                               "version": 12, "turn": 3, "meta": {"tier": "high"}}}
```
`output.type` is one of:

| type | meaning | who renders it |
| --- | --- | --- |
| `speak` | a whole utterance; `kind` = `ack`, `clarify`, `final`, `notice` (agent-initiated, with `meta.tier`) | overlay bubble (+ TTS); vscode shows `notice` as a notification |
| `speak_start` / `token` / `speak_end` | a streamed answer, token by token | overlay bubble |
| `duck` / `unduck` | lower or pause output now / resume | overlay, TTS |
| `tool_call` / `tool_cancel` | saga activity with `meta.tag` = reversible / compensable / irreversible | overlay tree |
| `status` | `code` = `trail_added`, `fork_hit`, `context_rejected`, `barrier_hold`, ... | overlay |

Renderers must drop `token` frames whose `turn` is older than the last `speak_start`, and must
stop playback immediately on `duck`.

### state (after every change, throttled to 20 Hz)
```json
{"type": "state", "state": {
  "agent_mode": true, "perception": true, "active_app": "chrome", "specialist": "booking",
  "phase": "speaking", "version": 42, "ducked": false,
  "focus": {"intent": "trail_compare", "slots": {...}},
  "goals": [{"id": "g1", "intent": "book_flight", "status": "active", "slots": {...}}],
  "forks": [{"id": "f1", "goal": "g1", "hypothesis": {"passengers": 2}, "status": "ready"}],
  "calls": [{"call_id": "t3-hold_fare", "tool": "hold_fare", "tag": "reversible", "status": "in_flight"}],
  "trail": [{"app": "chrome", "text": "Sat · ₹5,000", "context": "Chandigarh → Goa", "kind": "dwell"}],
  "latency": {"time_to_yield_ms": 3.1, "last_response_ms": 212.0},
  "pending_notices": [{"text": "...", "tier": "high"}]}}
```

### audit (on request: `{"type": "control", "action": "audit"}`)
```json
{"type": "audit", "read": [{"app": "chrome", "text": "Sat · ₹5,000"}], "sent_to_cloud": [{"model": "...", "chars": 312}]}
```

### error
```json
{"type": "error", "code": "unauthorized" | "bad_frame" | "rate_limited", "text": "..."}
```

## Rate limits

200 events per second per connection; larger bursts are dropped with `rate_limited`.
Frames over 256 KB are refused (VS Code sends full documents, so it should send
`doc_change` on a typing pause, not per keystroke).
