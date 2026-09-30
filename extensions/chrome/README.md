# Trail perception (Chrome, Manifest V3)

Sends hover, dwell (~350 ms on one element), selection, focus and Esc events to the local
Trail bridge (`docs/bridge-protocol.md`). Fast pointer transit sends nothing.

## Build and load

```powershell
npm install        # TypeScript only
npm run build      # src/*.ts -> dist/
```
Chrome → `chrome://extensions` → Developer mode → **Load unpacked** → this folder.
Options: bridge URL (default `ws://127.0.0.1:8765/ws`) and token (`trail-dev` with `python -m trail bridge --dev`).
Click the toolbar icon to pause/resume perception (badge ON/OFF).

## What is and is not sent
* Sent: the visible text of the element under the pointer, its nearest labelled container
  (aria-label / data-route / heading), role, screen bbox, URL. Fare cells are found by pairing a
  date-like and a price-like string in the nearest small container.
* Never sent: password inputs, `autocomplete=cc-*`, card/CVV/OTP/PIN fields, anything inside
  `[data-sensitive]`, incognito tabs (the extension is not allowed there), banking and
  password-manager domains. Key contents are never read; only Esc is reported.
* The bridge accepts loopback connections with a token only.

Demo page: `http://127.0.0.1:8765/demo/flights` (fictional Skylark Air fares).
