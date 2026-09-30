"""Evidence: harness scores, interruption metrics, and the ablation ladder (PDF p. 16-18).

    python -m trail eval                     # public + Trail suite, metrics table
    python -m trail eval --reps 3 --time-scale 1
    python -m trail ablate                   # five rungs, chart + table in reports/

Scores come from the kit's own scorer (harness/scorer.py). Interruption
metrics are computed from the same traces, so nothing here is self-reported.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
SPOKEN = {"filler_speech", "clarification_request", "final_response"}


def _load(paths: list[Path]) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def scenario_paths(which: str) -> list[Path]:
    pub = sorted((ROOT / "scenarios").glob("*.json"))
    own = sorted((ROOT / "scenarios_trail").glob("*.json"))
    stress = sorted((ROOT / "scenarios_stress").glob("*.json"))
    return {"public": pub, "trail": own, "stress": stress, "all": pub + own, "everything": pub + own + stress}[which]


def agent_class(features=None, *, base=None):
    from trail.core.agent import ParticipantAgent
    from trail.core.config import RuntimeConfig

    base = base or ParticipantAgent
    if features is None:
        return base
    return type("TrailVariant", (base,), {"config": RuntimeConfig(mode="harness", features=features)})


async def _run_one(scenario: dict, cls, time_scale: float) -> tuple[list[dict], Any]:
    from harness.runner import EvaluationHarness

    holder: list[Any] = []

    def factory(i, o):
        agent = cls(i, o)
        holder.append(agent)
        return agent

    h = EvaluationHarness(scenario, factory, time_scale=time_scale, verbose=False)
    await asyncio.wait_for(h.prepare(), 300)
    trace = await asyncio.wait_for(h.run(), 120)
    agent = holder[0] if holder else None
    runtime = getattr(agent, "runtime", None)
    return trace, runtime


def trace_metrics(scenario: dict, trace: list[dict]) -> dict[str, Any]:
    """Interruption metrics from the trace alone (virtual ms)."""
    events = scenario.get("events", [])
    actions = [e for e in trace if e.get("kind") == "action"]
    out: dict[str, Any] = {"yield_ms": [], "revised_answer_ms": [], "backchannel_false_stops": 0,
                           "stale_output_leaks": 0, "duplicate_writes": 0, "fillers": 0}
    out["fillers"] = sum(1 for a in actions if a.get("action") == "filler_speech")
    for ev in events:
        if ev.get("event_type") != "interruption":
            continue
        t = float(ev["timestamp_ms"])
        kind = ev.get("_interrupt", "")
        spoken = [a for a in actions if a.get("action") in SPOKEN and a["t_ms"] >= t]
        if spoken:
            out["yield_ms"].append(spoken[0]["t_ms"] - t)
        if kind == "correction":
            finals = [a for a in actions if a.get("action") == "final_response" and a["t_ms"] >= t]
            if finals:
                out["revised_answer_ms"].append(finals[0]["t_ms"] - t)
        if kind == "backchannel":
            window = [e for e in trace if t <= e.get("t_ms", 0) <= t + 1000]
            if any(e.get("kind") == "tool_cancelled" for e in window):
                out["backchannel_false_stops"] += 1
    # Duplicate state-modifying completions with identical arguments.
    seen: dict[str, int] = {}
    for c in trace:
        if c.get("kind") == "tool_completed" and c.get("status") == "success" and c.get("api_name") in {
                "book_flight", "cancel_booking", "create_support_ticket"} | set((scenario.get("tool_manifest") or {})):
            spec = (scenario.get("tool_manifest") or {}).get(c["api_name"], {})
            if c["api_name"] in (scenario.get("tool_manifest") or {}) and spec.get("kind") != "state_modifying":
                continue
            key = c["api_name"] + json.dumps(c.get("args", {}), sort_keys=True).lower()
            seen[key] = seen.get(key, 0) + 1
    out["duplicate_writes"] = sum(n - 1 for n in seen.values() if n > 1)
    # Stale output: a final that names a value the user already corrected away.
    rec = (scenario.get("ground_truth") or {}).get("recovery") or {}
    for inv in rec.get("invalidated_calls", []):
        after = float(inv.get("invalid_after_ms", 0))
        # Only values the user moved away from count: a value that is still in the final state is not stale.
        final_state = next((a.get("state_snapshot") for a in reversed(actions) if isinstance(a.get("state_snapshot"), dict)), {}) or {}
        current = {str(v).lower() for v in (final_state.get("slots") or {}).values()}
        olds = [str(v).lower() for vals in (inv.get("args_subset") or {}).values()
                for v in (vals if isinstance(vals, list) else [vals]) if str(v).lower() not in current]
        for a in actions:
            if a.get("action") == "final_response" and a["t_ms"] > after + 50:
                text = str(a.get("payload", {}).get("text", "")).lower()
                if any(o and len(o) > 3 and o in text for o in olds) and "instead" not in text and "not " + olds[0] not in text:
                    out["stale_output_leaks"] += 1
    return out


def run(paths: list[Path], *, cls=None, time_scale: float = 4.0, reps: int = 1, quiet: bool = False) -> dict[str, Any]:
    from harness.scorer import score_scenario

    cls = cls or agent_class()
    rows = []
    for sc in _load(paths):
        totals, metrics, rt_metrics = [], [], []
        for _ in range(reps):
            trace, runtime = asyncio.run(_run_one(sc, cls, time_scale))
            result = score_scenario(sc, trace)
            totals.append(result["total"])
            metrics.append(trace_metrics(sc, trace))
            if runtime is not None:
                rt_metrics.append(runtime.metrics.as_dict())
        med = statistics.median(totals)
        row = {"scenario_id": sc["scenario_id"], "suite": sc.get("metadata", {}).get("suite", "public"),
               "modality": sc.get("metadata", {}).get("modality"), "difficulty": sc.get("metadata", {}).get("difficulty"),
               "totals": totals, "median": med, "metrics": metrics[len(metrics) // 2],
               "runtime": rt_metrics[len(rt_metrics) // 2] if rt_metrics else {}}
        rows.append(row)
        if not quiet:
            print(f"  {med:>5.1f}  {row['scenario_id']}", flush=True)
    return {"rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict[str, Any]:
    def weight(r):
        w = 1.5 if r["modality"] in {"audio", "visual"} else 1.0
        return w * (1.25 if r["difficulty"] in {"L3", "L4"} else 1.0)

    pub = [r for r in rows if r["suite"] == "public"]
    own = [r for r in rows if r["suite"] == "trail"]

    def avg(rs):
        return round(sum(r["median"] for r in rs) / len(rs), 1) if rs else None

    def wavg(rs):
        ws = sum(weight(r) for r in rs)
        return round(sum(r["median"] * weight(r) for r in rs) / ws, 1) if rs else None

    ylds = [x for r in rows for x in r["metrics"]["yield_ms"]]
    revs = [x for r in rows for x in r["metrics"]["revised_answer_ms"]]
    corrections = sum(r["runtime"].get("corrections", 0) for r in rows)
    hits = sum(r["runtime"].get("fork_hits", 0) for r in rows)

    def pct(xs, q):
        if not xs:
            return None
        s = sorted(xs)
        return round(s[min(int(q * (len(s) - 1) + 0.5), len(s) - 1)], 1)

    return {
        "public_plain": avg(pub), "public_weighted": wavg(pub), "trail_plain": avg(own), "all_plain": avg(rows),
        "time_to_yield_ms_p50": pct(ylds, 0.5), "time_to_yield_ms_p95": pct(ylds, 0.95),
        "revised_answer_ms_p50": pct(revs, 0.5), "corrections": corrections, "fork_hits": hits,
        "fork_hit_rate": round(hits / corrections, 3) if corrections else None,
        "backchannel_false_stops": sum(r["metrics"]["backchannel_false_stops"] for r in rows),
        "stale_output_leaks": sum(r["metrics"]["stale_output_leaks"] for r in rows),
        "stale_output_leaks_runtime": sum(r["runtime"].get("stale_output_leaks", 0) for r in rows),
        "duplicate_writes": sum(r["metrics"]["duplicate_writes"] for r in rows),
        "stale_results_ignored": sum(r["runtime"].get("stale_results_ignored", 0) for r in rows),
        "calls_cancelled": sum(r["runtime"].get("calls_cancelled", 0) for r in rows),
        "compensations": sum(r["runtime"].get("compensations", 0) for r in rows),
        "held_at_barrier": sum(r["runtime"].get("held_at_barrier", 0) for r in rows),
        "runtime_errors": sum(r["runtime"].get("errors", 0) for r in rows),
    }


def write_report(report: dict[str, Any], name: str) -> Path:
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{name}.json"
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    s = report["summary"]
    lines = [f"# {name}", "", f"Generated {time.strftime('%Y-%m-%d %H:%M')}; time scale {report.get('time_scale')}, "
             f"reps {report.get('reps')}, models: {report.get('models')}.", "",
             "| Metric | Value | Target (PDF p. 17) |", "| --- | --- | --- |",
             f"| Public set, plain average | {s['public_plain']} | clearly above ~52 |",
             f"| Public set, weighted (a/v x1.5, L3/L4 x1.25) | {s['public_weighted']} | |",
             f"| Trail suite, plain average | {s['trail_plain']} | |",
             f"| Time to yield p50 / p95 (virtual ms) | {s['time_to_yield_ms_p50']} / {s['time_to_yield_ms_p95']} | < 150 ms |",
             f"| Correction to revised answer p50 (virtual ms) | {s['revised_answer_ms_p50']} | < 300 ms on a fork hit |",
             f"| Fork hit rate | {s['fork_hit_rate']} ({s['fork_hits']}/{s['corrections']}) | report it |",
             f"| Backchannel false stops | {s['backchannel_false_stops']} | 0 |",
             f"| Stale-output leaks (trace / runtime) | {s['stale_output_leaks']} / {s['stale_output_leaks_runtime']} | 0 |",
             f"| Duplicate writes | {s['duplicate_writes']} | 0 |",
             f"| Stale results ignored / calls cancelled / compensations | {s['stale_results_ignored']} / {s['calls_cancelled']} / {s['compensations']} | |",
             f"| Runtime errors | {s['runtime_errors']} | 0 |", "", "| Scenario | Suite | Score (median) |", "| --- | --- | --- |"]
    for r in report["rows"]:
        lines.append(f"| {r['scenario_id']} | {r['suite']} | {r['median']:.1f} |")
    md = REPORTS / f"{name}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md


def ablate(paths: list[Path], *, time_scale: float = 4.0, reps: int = 1) -> dict[str, Any]:
    from trail.core.config import Features

    rungs = []
    for label, features in Features.ladder():
        print(f"== {label}", flush=True)
        rep = run(paths, cls=agent_class(features), time_scale=time_scale, reps=reps, quiet=True)
        s = rep["summary"]
        rungs.append({"rung": label, "summary": s, "rows": [{"scenario_id": r["scenario_id"], "median": r["median"]} for r in rep["rows"]]})
        print(f"   public {s['public_plain']}  trail {s['trail_plain']}  revised-answer p50 {s['revised_answer_ms_p50']}  "
              f"false stops {s['backchannel_false_stops']}  dup writes {s['duplicate_writes']}", flush=True)
    return {"rungs": rungs, "time_scale": time_scale, "reps": reps}


def ablation_chart(result: dict[str, Any]) -> Path | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    labels = [r["rung"] for r in result["rungs"]]
    pub = [r["summary"]["public_plain"] or 0 for r in result["rungs"]]
    own = [r["summary"]["trail_plain"] or 0 for r in result["rungs"]]
    stops = [r["summary"]["backchannel_false_stops"] or 0 for r in result["rungs"]]
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=150)
    x = range(len(labels))
    ax.plot(x, own, marker="o", color="#1f5fbf", label="Trail interruption suite (20)")
    ax.plot(x, pub, marker="s", color="#8a8a8a", label="Kit public set (9, rules only: no speech/vision models)")
    ax.axhline(52, color="#c44", linestyle=":", linewidth=1)
    ax.text(len(labels) - 1, 53, "kit reference agent ~52", color="#c44", ha="right", fontsize=8)
    for i, (y, n) in enumerate(zip(own, stops)):
        ax.annotate(f"{y:.1f}", (i, y), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8, color="#1f5fbf")
        ax.annotate(f"backchannel false stops: {n}", (i, 8), ha="center", fontsize=7, color="#c44" if n else "#2a8a4a")
    ax.set_xticks(list(x), labels, rotation=12, fontsize=8)
    ax.set_ylabel("Harness score (kit scorer, median)")
    ax.set_ylim(0, 108)
    ax.set_title("Ablation: each runtime mechanism switched on in turn")
    ax.legend(loc="center right", fontsize=8)
    for s in ("top",):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / "ablation.png"
    fig.savefig(out)
    plt.close(fig)
    return out


def models_in_use() -> str:
    return f"TRAIL_LLM={os.environ.get('TRAIL_LLM', 'auto')}, TRAIL_STT={os.environ.get('TRAIL_STT', 'whisper')}"


def replace_features(**kw):
    from trail.core.config import Features

    return replace(Features(), **kw)
