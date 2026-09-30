"""Windows UI Automation reader: the element under the pointer in Excel, PDF readers and native apps.

    python -m trail uia [--token trail-dev]

Polls the cursor (20 Hz); when it rests on one element past the dwell threshold, sends a `dwell`
with the element's name/value and, for grid cells, its row and column headers as context
(PDF p. 9). Foreground-app changes become `app_switch`. Password fields and anything whose
control type or name looks sensitive are never sent. Requires `pip install pywinauto`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time

DWELL_S = 0.35
_SENSITIVE = re.compile(r"password|passcode|pin|otp|card number|cvv|security code", re.I)
_APPS = {"excel": "excel", "acrord": "pdf", "acrobat": "pdf", "sumatra": "pdf", "winword": "word", "outlook": "outlook",
         "chrome": "chrome", "msedge": "edge", "code": "vscode"}


def _app_of(element) -> str:
    try:
        import psutil  # optional

        name = psutil.Process(element.element_info.process_id).name().lower()
    except Exception:
        name = str(getattr(element.element_info, "class_name", "")).lower()
    return next((v for k, v in _APPS.items() if k in name), name.split(".")[0] or "app")


def _describe(element) -> dict | None:
    info = element.element_info
    ctype = str(info.control_type or "")
    name = (info.name or "").strip()
    if ctype == "Edit" and getattr(info, "is_password", False) or _SENSITIVE.search(name):
        return {"text": "", "sensitive": True}
    value = ""
    try:
        value = element.iface_value.CurrentValue or ""
    except Exception:
        pass
    text = " · ".join(t for t in (name, value) if t)[:300]
    if not text:
        return None
    context = ""
    try:                                  # grid cells: row and column headers
        grid = element.iface_grid_item
        col = grid.CurrentColumn
        row = grid.CurrentRow
        parent = element.parent()
        headers = []
        for label, idx in (("row", row), ("column", col)):
            headers.append(f"{label} {idx + 1}")
        context = f"{parent.element_info.name or 'sheet'} ({', '.join(headers)})"
    except Exception:
        try:
            context = (element.parent().element_info.name or "")[:200]
        except Exception:
            context = ""
    role = {"DataItem": "cell", "Button": "button", "Hyperlink": "link", "Text": "text", "Image": "image"}.get(ctype, ctype.lower() or "text")
    return {"text": text, "context": context, "role": role, "sensitive": False}


async def run(url: str, token: str) -> None:
    from pywinauto import Desktop, mouse  # noqa: F401  (import check)
    from pywinauto.uia_element_info import UIAElementInfo
    from pywinauto.controls.uiawrapper import UIAWrapper
    import ctypes
    from websockets.asyncio.client import connect

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    async with connect(f"{url}?client=uia&token={token}") as ws:
        await ws.send(json.dumps({"type": "hello", "client": "uia", "version": 1, "app": "uia"}))
        last_key, since, sent_key, last_app = None, 0.0, None, None
        while True:
            await asyncio.sleep(0.05)
            pt = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            try:
                el = UIAWrapper(UIAElementInfo.from_point(pt.x, pt.y))
                desc = _describe(el)
                app = _app_of(el)
            except Exception:
                continue
            if app != last_app:
                last_app = app
                await ws.send(json.dumps({"type": "event", "event": {"type": "app_switch", "app": app, "source": "uia"}}))
            if not desc or desc.get("sensitive"):
                last_key = None
                continue
            key = (desc["text"], desc["context"])
            now = time.monotonic()
            if key != last_key:
                last_key, since = key, now
                continue
            if now - since >= DWELL_S and key != sent_key:
                sent_key = key
                await ws.send(json.dumps({"type": "event", "event": {
                    "type": "dwell", "app": app, "source": "uia",
                    "target": {**desc, "dwell_ms": round((now - since) * 1000)}}}, ensure_ascii=False))


def main(token: str | None = None, port: int = 8765) -> None:
    token = token or os.environ.get("TRAIL_TOKEN") or "trail-dev"
    try:
        asyncio.run(run(f"ws://127.0.0.1:{port}/ws", token))
    except KeyboardInterrupt:
        pass
