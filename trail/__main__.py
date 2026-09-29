import argparse
import asyncio
import json
from pathlib import Path

from .replay import run_replays


def main() -> None:
    parser = argparse.ArgumentParser(description="Trail core: offline interruption replays")
    parser.add_argument("command", choices=["replay"])
    parser.add_argument("scenario", nargs="?", type=Path)
    parser.add_argument("--all", action="store_true", help="run all local scenarios")
    parser.add_argument("--verbose", action="store_true", help="show streamed answers and interruption controls")
    args = parser.parse_args()
    if args.all and args.scenario:
        parser.error("choose a scenario or --all")
    paths = sorted(Path("scenarios").glob("*.json")) if args.all else [args.scenario or Path("scenarios/01_cancel_resume.json")]
    if not paths:
        parser.error("no scenarios found; run from the repository root")
    try:
        results = asyncio.run(run_replays(paths, verbose=args.verbose))
    except (OSError, ValueError, AssertionError, TimeoutError) as exc:
        parser.exit(1, f"Replay failed: {exc}\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
