# Act 3 demo workspace (code mentor)

Start the bridge (`python -m trail bridge --dev`) and the overlay, open this folder in VS Code
with the Trail extension installed, then:

1. Run **Trail: Declare what I'm building** → `OAuth login`.
2. Type a bug in `login()` (e.g. change the guard to `return user.email` right after `get_user`).
   The status bar shows a *waiting* badge; the warning arrives when you pause typing.
3. Type a second bug (`if password == user["password_hash"]:`) and fix it before pausing:
   the Trail output channel logs that the warning was dropped because you fixed it.
4. Paste a fake key such as `API_KEY = "sk-live-0123456789abcdefghijklmnop"`: Trail interrupts at once
   (the key is redacted in the editor before anything is sent; only the line number leaves).
5. Run `pytest` in the integrated terminal; `test_login_unknown_user` fails. Ask
   **Trail: Ask** → `why did that break?`: the diagnosis is already there.
