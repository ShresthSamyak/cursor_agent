# Build status against the Trail PDF

Source: the 22-page PDF at the repository root, dated September 28, 2026.
Build gates are on pages 19-20; core requirements are on pages 7-8.

## Phase 0: portable core

| Requirement | Status |
| --- | --- |
| Read official protocol and map all messages | Blocked: kit absent |
| ParticipantAgent queue adapter | Local adapter implemented; official mapping pending |
| Versioned state with one writer | Implemented and tested |
| Cancellable turns and checkpoints | Implemented and tested |
| Retrieval during partial speech | Implemented with offline provider |
| Context retained through interruption | Single checkpoint; goal stack belongs to Phase 1 |
| Text, image, simulated voice | Events supported; actual vision/model integration pending |
| All public scenarios, no protocol errors | Not run: official scenarios absent |

Gate 1 is **not passed**. Local functional tests are not official protocol or
evaluator evidence. No official score is reported.

## Subsequent gates

1. **Phase 1:** seven-way classifier, goal stack, slot dependencies, fillers, and
   three evaluator runs exceeding the verified reference baseline.
2. **Phase 2:** budgeted forks, transaction rollback/compensation, commit barriers,
   call-ID idempotency, 15-20 behavioral scenarios, end-to-end metrics.
3. **Phase 3:** authenticated localhost bridge, Chrome dwell extraction, referent
   resolver, opt-in cursor overlay, multiverse view, booking corpus, speech.
4. **Phase 4:** VS Code perception and mentor, interrupt arbiter with staleness
   checks, ablations, recordings, slide deck, verified release requirements.

Desktop work waits for the core gates. PDF/sheet specialists remain stretch scope;
email/calendar remain pitch-only, following the source document.

## Available verification

Run `python -m pytest -q` and `python -m trail replay --all`. Checks exercise stale
results after cancellation, simultaneous input/output, retrieval cancellation,
exact chunk resume, partial-prefetch reuse, provider failures, validation,
sensitive context, session separation, and cleanup.

The source PDF marks its model choices, kit details, reference score, rubric,
and submission names as requiring verification. None is treated as a confirmed
official requirement here. No model was downloaded and no API key is needed
for this offline foundation.
