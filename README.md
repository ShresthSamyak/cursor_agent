# Trail — interruptible cursor agent

Trail remembers what you looked at and handles interruptions in both directions: you can cut
it off, correct it, or point somewhere else mid-sentence, and it decides when it may interrupt
you. Built from `Trail — Interruptible Cursor Agent.pdf` for Samsung PRISM GenAI Theme 05.

| | |
| --- | --- |
| **Official evaluator** (`eval_submission.py . --reps 3`, time scale 1) | **weighted 100.0 / 100** (kit reference agent ≈ 52) |
| Trail's own 20-scenario interruption suite (kit format, kit scorer) | 99+ average |
| Heckler (5 interrupts, kit scorer) | Trail 100 vs naive cancel-and-restart 54.5 |
| Tests | 87 (`pytest`) |

**Detailed status, decisions and change log: [HANDOFF.md](HANDOFF.md).**

## What makes it different

* **Speculative forks** — likely corrections are pre-computed; a hit is served instantly and lights up in the overlay.
* **Two-way interruptibility** — every user input is classified (7 types, duck-then-decide) before anything is cancelled; an arbiter schedules the agent's own interruptions by severity, flow state and staleness.
* **Transactional tools** — every call is reversible, compensable or irreversible (from the manifest); interrupts cancel or compensate; irreversible calls wait at a commit barrier; `call_id` idempotency.

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONIOENCODING="utf-8"

# Scored path (kit harness)
.venv\Scripts\python run_local.py --all --time-scale 1 --quiet --agent agent.agent:ParticipantAgent
.venv\Scripts\python eval_submission.py . --reps 3
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m trail eval          # PDF metrics table -> reports/
.venv\Scripts\python -m trail ablate        # ablation ladder + chart -> reports/

# Demo layer
.venv\Scripts\python -m trail demo all      # scripted Acts 1-3 through the real runtime
.venv\Scripts\python -m trail bridge --dev  # ws://127.0.0.1:8765/ws + /demo/flights, /demo/budget, /overlay/
.venv\Scripts\python -m trail speech        # microphone: VAD barge-in, streaming STT, TTS
```
Models are optional (rules-only works): faster-whisper for audio, Ollama `gemma3:4b` for vision,
`SECRET_GEMINI_API_KEY` / `SECRET_OPENROUTER_API_KEY` for cloud. See HANDOFF.md §3.

## Layout

```
agent/            kit entry point (submission.yaml -> agent.agent:ParticipantAgent) + kit baseline
trail/core/       scored runtime, no desktop imports (see HANDOFF.md §4)
trail/desktop/    bridge, desktop tools + corpus, demo pages, speech, UI Automation, demo replay
extensions/       chrome/ (MV3) and vscode/ (code mentor) perception clients
overlay/          cursor bubble, perception ring, multiverse tree (browser + Electron)
scenarios/        kit public scenarios        scenarios_trail/  Trail's own suite
harness/ run_local.py eval_submission.py docs/kit/   kit files (vendored, see HANDOFF.md §6)
docs/             bridge protocol, architecture      reports/  generated evidence
```
