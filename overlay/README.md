# Trail overlay

What judges see: the cursor bubble (streamed answers, acks, clarifying questions, ducking), the
perception ring (visible whenever perception is on), agent-initiated notices by tier, the
multiverse tree (goal trunk, parked goals, saga calls tagged reversible / compensable /
irreversible, barrier locks, speculative forks lighting green on a hit), latency counters and a
session audit panel.

* **Browser build (reliable fallback):** start the bridge (`python -m trail bridge --dev`) and open
  `http://127.0.0.1:8765/overlay/?token=trail-dev`.
* **Offline replay for recording:** `http://127.0.0.1:8765/overlay/?demo=1` (or open
  `renderer/index.html?demo=1` from any static server) plays the Act 1 beats with no bridge.
* **Electron (transparent, click-through, follows the OS cursor):**
  `cd overlay/electron && npm install && npm start`. Env: `TRAIL_TOKEN`, `TRAIL_BRIDGE`.
  Hotkeys: Ctrl+Alt+T agent mode, Ctrl+Alt+P perception, Ctrl+Alt+Esc stop.

`renderer/model.js` is a pure view-model (no DOM) and can be exercised in Node.
