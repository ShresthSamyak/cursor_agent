import asyncio

from trail.core.arbiter import ACK, FINAL, Arbiter, Pending, Tier, claims
from trail.core.forks import ForkManager, likely_corrections
from trail.core.privacy import redact, safe_target
from trail.core.bus import Target
from trail.core.trail import TrailStore, make_entry


def test_filler_budget_repeats_and_premature_claims():
    a = Arbiter(filler_budget=2)
    assert a.allow(ACK, "Checking flights to Boston.", completed_kinds=set())
    assert not a.allow(ACK, "Checking flights to Boston.", completed_kinds=set())      # verbatim repeat
    assert a.allow(ACK, "Still on it.", completed_kinds=set())
    assert not a.allow(ACK, "One more.", completed_kinds=set())                          # budget
    assert not a.allow(FINAL, "Your flight is booked.", completed_kinds=set())           # claim before completion
    assert a.allow(FINAL, "I'll get that booked now.", completed_kinds=set())            # a promise is fine
    assert a.allow(FINAL, "Done, it's booked.", completed_kinds={"book"})
    assert claims("Your booking BK-1 was cancelled") == {"cancel"}


def test_agent_initiated_interruptions_respect_tiers_flow_and_staleness():
    a = Arbiter(mode="desktop", unsolicited_budget=5)
    fixed = {"done": False}
    a.submit(Pending("notice", "Secret in source", Tier.CRITICAL, key="s"))
    a.submit(Pending("notice", "Bug on line 4", Tier.HIGH, key="b", still_valid=lambda: not fixed["done"]))
    a.submit(Pending("notice", "Consider a helper", Tier.NORMAL, key="n"))
    # User is speaking: only Critical may break in.
    assert [p.key for p in a.due(now_ms=0, user_speaking=True, typing=False, boundary=False)] == ["s"]
    # Typing: High waits for a pause; Normal waits for a boundary.
    assert a.due(now_ms=1, user_speaking=False, typing=True, boundary=False) == []
    fixed["done"] = True                                                                   # user fixed it first
    assert [p.key for p in a.due(now_ms=2, user_speaking=False, typing=False, boundary=True)] == ["n"]
    assert a.dropped_stale == 1


def test_not_now_raises_the_threshold():
    a = Arbiter(mode="desktop")
    a.not_now(0)
    a.submit(Pending("notice", "style nit", Tier.NORMAL, key="n"))
    assert a.due(now_ms=1000, user_speaking=False, typing=False, boundary=True) == []


def test_trail_ranking_and_referents():
    t = TrailStore(tau_s=600, dwell0_ms=350)
    for i, (d, p) in enumerate([("Fri", "6,400"), ("Sat", "5,000"), ("Sun", "11,000")]):
        t.add(make_entry(id=str(i), ts=100 + i, app="chrome", source="ext", text=f"{d} · ₹{p}", context="Chandigarh → Goa"))
    assert t.cheapest(context="Chandigarh → Goa").dates == ("Saturday",)
    assert t.current().dates == ("Sunday",) and t.previous().dates == ("Saturday",)
    recent = t.search(["sun"], now_s=103)[0][1]
    assert recent.dates == ("Sunday",)
    old = make_entry(id="o", ts=0, app="chrome", source="ext", text="Sun · ₹1", context="x", dwell_ms=0)
    fresh = make_entry(id="f", ts=600, app="chrome", source="ext", text="Sun · ₹1", context="y", dwell_ms=3500)
    assert t.score(fresh, 1.0, 600) > t.score(old, 1.0, 600)


def test_privacy_redaction_and_sensitive_targets():
    assert redact("4111 1111 1111 1111 sk-abcdefghijklmnopqrstuvwxyz") == "[redacted card] [redacted secret]"
    assert safe_target(Target(text="hunter2", role="password")) is None


def test_forks_hit_on_the_predicted_correction_and_die_on_other_changes():
    async def check():
        fm = ForkManager(max_forks=3)
        alts = likely_corrections({"date": "Friday", "destination": "Goa"})
        assert {"date": "Saturday"} in alts and {"passengers": 2} in alts
        for h in alts:
            fm.spawn("g", h, {"date": 1, "destination": 1}, lambda h=h: asyncio.sleep(0, result=h))
        await asyncio.sleep(0.01)
        assert fm.match("g", {"date": "saturday"}).hypothesis == {"date": "Saturday"}
        fm.invalidate("g", {"date": 1, "destination": 2}, {"destination"})
        assert all(f.status in {"dead", "served"} for f in fm.forks.values())
        await fm.shutdown()
    asyncio.run(check())
