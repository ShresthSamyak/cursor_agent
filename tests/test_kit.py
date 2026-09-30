"""End to end through the kit's own harness and scorer (rules only, no models)."""


import random


import pytest

from tests.conftest import ROOT, actions, load, run_kit
from trail.core.agent import decode, encode
from trail.core.bus import EventType, Output

TEXT_PUBLIC = ["pub_01_text_simple", "pub_02_text_interrupt", "pub_03_text_chained_booking", "pub_04_text_no_tool",
               "pub_08_text_tool_failure", "pub_09_text_unseen_tool"]


def test_protocol_mapping_covers_every_kit_event_and_action():
    assert decode({"event_type": "tool_manifest", "payload": {"tools": {}}}).type == EventType.MANIFEST
    assert decode({"event_type": "user_speech_chunk", "payload": {"text": "to ", "end_of_turn": False}}).type == EventType.SPEECH_PARTIAL
    assert decode({"event_type": "user_speech_chunk", "payload": {"text": "", "end_of_turn": True}}).type == EventType.SPEECH_FINAL
    assert decode({"event_type": "interruption", "payload": {"text": "stop"}}).barge_in
    assert decode({"event_type": "user_audio_chunk", "payload": {"audio_ref": "a.mp3", "end_of_turn": True}}).type == EventType.AUDIO
    assert decode({"event_type": "video_frame", "payload": {"image_ref": "f.png"}}).type == EventType.FRAME
    assert decode({"event_type": "tool_result", "payload": {"call_id": "c1", "status": "error", "result": {"error": "timeout"}}}).status == "error"
    assert decode({"event_type": "scenario_end", "payload": {}}).type == EventType.INPUT_END
    assert decode({"event_type": "bogus"}) is None and decode("junk") is None
    final = encode(Output(type="speak", session_id="s", version=1, kind="final", text="hi", snapshot={"intent": "x", "slots": {}}))
    assert final == {"action": "final_response", "payload": {"text": "hi"}, "state_snapshot": {"intent": "x", "slots": {}}}
    assert encode(Output(type="tool_cancel", session_id="s", version=1, call_id="c9")) == {"action": "cancel_tool", "payload": {"call_id": "c9"}}
    assert encode(Output(type="duck", session_id="s", version=1)) is None


def test_contract_smoke_test_from_the_official_evaluator():
    import eval_submission
    from trail.core.agent import ParticipantAgent

    assert eval_submission.contract_smoke_test(ParticipantAgent, 60) == []


@pytest.mark.parametrize("name", TEXT_PUBLIC)
def test_public_text_scenarios_score_full_marks(name):
    trace, score, agent = run_kit(load(f"scenarios/{name}.json"))
    assert score["total"] == 100.0, score
    assert not [e for e in trace if e["kind"] in {"protocol_error", "agent_crash"}]
    assert agent.runtime.metrics.errors == 0


@pytest.mark.parametrize("path", sorted((ROOT / "scenarios_trail").glob("*.json")), ids=lambda p: p.stem)
def test_trail_interruption_suite(path):
    sc = load(path)
    trace, score, agent = run_kit(sc)
    if score["total"] < 98.0:
        trace, score, agent = run_kit(sc)     # one retry for replay-speed timer jitter (seen once on trail_11 under load)
    assert score["total"] >= 98.0, score
    # Zero-tolerance metrics from the PDF.
    assert agent.runtime.metrics.backchannel_false_stops == 0
    assert agent.runtime.metrics.duplicate_writes == 0
    assert agent.runtime.metrics.errors == 0
    finals = actions(trace, "final_response")
    assert all(isinstance(f.get("state_snapshot"), dict) for f in finals)


@pytest.mark.parametrize("path", sorted((ROOT / "scenarios_stress").glob("*.json")), ids=lambda p: p.stem)
def test_hidden_style_stress_suite(path):
    trace, score, agent = run_kit(load(path))
    if score["total"] < 98.0:
        trace, score, agent = run_kit(load(path))     # one retry for replay-speed timer jitter
    assert score["total"] >= 98.0, score
    assert agent.runtime.metrics.errors == 0


@pytest.mark.parametrize("template, seed", [("simple_search", 7), ("search_interrupt", 1), ("search_interrupt", 42),
                                            ("unseen_tool", 3), ("unseen_tool", 11)])
def test_generated_reskins(template, seed):
    from harness.scenario_gen import TEMPLATES

    rng = random.Random(seed)
    for i in range(4):
        sc = TEMPLATES[template](rng, i)
        trace, score, _ = run_kit(sc)
        if score["total"] < 97.0:
            # One retry: at 4x replay speed Windows timer jitter (~16 ms real = ~64 ms virtual) can move an
            # interrupt past a tool completion under full-suite load. A real regression fails twice.
            trace, score, _ = run_kit(sc)
        if score["total"] < 97.0:
            from harness.scorer import format_report
            detail = [(e["kind"], round(e["t_ms"]), e.get("action") or e.get("event_type"), str(e.get("payload") or e.get("args"))[:90])
                      for e in trace if e["kind"] in {"action", "event", "tool_completed", "tool_cancelled"}]
            raise AssertionError(format_report(score) + chr(10) + chr(10).join(map(str, detail)))


def test_naive_ablation_rung_loses_context_on_correction():
    from trail.core.agent import NaiveAgent

    _, full, _ = run_kit(load("scenarios/pub_02_text_interrupt.json"))
    _, naive, _ = run_kit(load("scenarios/pub_02_text_interrupt.json"), NaiveAgent)
    assert naive["total"] < full["total"]
