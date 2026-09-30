"""Command line: evidence runs, the ablation ladder, and the desktop bridge.

    python -m trail eval [--suite all|public|trail] [--time-scale 4] [--reps 1]
    python -m trail ablate [--suite trail] [--time-scale 4]
    python -m trail bridge [--port 8765]          # desktop mode (see trail/desktop)
    python -m trail demo act1                      # scripted replay of a demo act
"""

import argparse
import json
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="trail", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("eval", help="score the public set and Trail's suite with the kit scorer")
    ev.add_argument("--suite", choices=["all", "public", "trail"], default="all")
    ev.add_argument("--time-scale", type=float, default=4.0)
    ev.add_argument("--reps", type=int, default=1)
    ev.add_argument("--name", default="metrics")
    ab = sub.add_parser("ablate", help="run the ablation ladder and draw the chart")
    ab.add_argument("--suite", choices=["all", "public", "trail"], default="all")
    ab.add_argument("--time-scale", type=float, default=4.0)
    ab.add_argument("--reps", type=int, default=1)
    br = sub.add_parser("bridge", help="run the desktop runtime behind the localhost WebSocket bridge")
    br.add_argument("--port", type=int, default=8765)
    br.add_argument("--corpus", default=None, help="travel corpus JSON (defaults to the bundled demo fares)")
    br.add_argument("--dev", action="store_true", help="use the fixed development token trail-dev")
    sp = sub.add_parser("speech", help="microphone -> VAD barge-in + streaming STT -> bridge; TTS for answers")
    sp.add_argument("--token", default=None)
    sp.add_argument("--port", type=int, default=8765)
    ua = sub.add_parser("uia", help="Windows UI Automation reader (Excel, PDF, native apps) -> bridge")
    ua.add_argument("--token", default=None)
    ua.add_argument("--port", type=int, default=8765)
    dm = sub.add_parser("demo", help="scripted replay of a demo act (no perception needed)")
    dm.add_argument("act", choices=["act1", "act2", "act3", "heckler", "all"])
    dm.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if args.command in {"eval", "ablate"}:
        from . import eval as E

        paths = E.scenario_paths(args.suite)
        if args.command == "eval":
            report = E.run(paths, time_scale=args.time_scale, reps=args.reps)
            report.update({"time_scale": args.time_scale, "reps": args.reps, "models": E.models_in_use()})
            md = E.write_report(report, args.name)
            print(json.dumps(report["summary"], indent=2))
            print(f"wrote {md}")
        else:
            result = E.ablate(paths, time_scale=args.time_scale, reps=args.reps)
            (E.REPORTS).mkdir(exist_ok=True)
            (E.REPORTS / "ablation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            chart = E.ablation_chart(result)
            print(f"wrote {E.REPORTS / 'ablation.json'}" + (f" and {chart}" if chart else ""))
        return
    if args.command == "bridge":
        from .desktop.bridge import main as bridge_main

        bridge_main(port=args.port, corpus=args.corpus, dev=args.dev)
        return
    if args.command == "speech":
        from .desktop.speech import main as speech_main

        speech_main(token=args.token, port=args.port)
        return
    if args.command == "uia":
        from .desktop.uia import main as uia_main

        uia_main(token=args.token, port=args.port)
        return
    if args.command == "demo":
        from .desktop.demo import main as demo_main

        demo_main(args.act, speed=args.speed)
        return
    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
