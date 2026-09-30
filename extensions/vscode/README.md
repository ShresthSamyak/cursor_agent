# Trail code mentor (VS Code)

Perception and UI for the Trail bridge (`docs/bridge-protocol.md`, client `vscode`).

* Sends: hover (+ dwell after 350 ms), selection, typing bursts (timing only), the document on each
  typing pause (and at once when a key is pasted), saves, test runs, terminal output via shell
  integration (opt-out `trail.readTerminal`), window focus.
* Never sends: files matching `trail.excludeFiles` (.env, keys, credentials…), key contents outside
  documents, or secrets — keys are redacted before sending and only their line numbers are reported.
* Renders: agent-initiated notices as notifications (critical = error, high = warning) with
  "Not now", gutter decorations on the finding's line (cleared when you edit it), a status bar
  item with a *waiting* badge while a warning is held for your pause.

Build: `npm install && npx tsc -p .` (output in `out/`). Run: open this folder in VS Code and press
F5 (Extension Development Host), or `npx @vscode/vsce package` to make a `.vsix`.
Commands: Trail: Declare what I'm building · Ask · Not now · Teach mode / Fix mode · Toggle perception · Stop talking.

Demo: `demo-workspace/README.md` walks through Act 3.
