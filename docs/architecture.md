# Runtime invariants

`Runtime.run()` is the only session-state writer. It consumes input and background
proposals. Providers receive frozen snapshots. When input and a proposal are
ready together, input takes priority. A session belongs to one event loop.

`version` is a semantic epoch: requests, cancellations, and context changes
invalidate work. Token progress and ducking do not increment it, since a turn
must not invalidate itself. Both version and turn ID must match before a proposal
can update a checkpoint or emit output. This implements the PDF's stale-work
protection without interpreting "every change" as "every emitted token."

Workers prepare evidence or stream a prepared result. They submit proposals
through a bounded internal queue. Each chunk must be accepted before the producer
advances. The owner updates the checkpoint and publishes the chunk synchronously,
so the checkpoint matches output delivered to the runtime sink.

Cancellation advances the epoch before cancelling the worker. The checkpoint
already exists; saving it needs no network/file I/O. `CancelledError` propagates.
A provider returning despite cancellation cannot publish an old result. Providers
must cooperate with cancellation; this is not a process sandbox for hung code.

## Output and backpressure

The sink is an unbounded `asyncio.Queue`, published to with `put_nowait`. Checking
the version and publishing are atomic within this event loop. Bounded sinks are
rejected rather than blocking input handling.

The renderer must consume continuously and honor `duck`, `unduck`, version, and
turn ID. Duck stops playback and clears pending presentation output; a new
version invalidates old frames. Output already handed to a consumer cannot be
recalled by the core. There is no audio renderer yet, so the under-150-ms audio
yield target has not been measured.

Chunks are limited to 16,000 characters and each accumulated answer to 128,000.
Evidence is capped at 256 entries. Deduplication retains the last 4,096 event IDs,
not lifetime history. Transport rate/memory limits await the kit integration.

## Partial speech and resume

`speech_partial` starts preparation with output ducked. Identical partials reuse
work. A matching final reuses preparation or lets it finish. A changed final
invalidates it. This is early retrieval, not speculative correction forks.

VAD end and typing idle do not infer intent. An explicit `resume` event or final
transcript allows output. Natural-language classification of "mm-hm", "stop", or
"back to the flight" is Phase 1. Local replays use explicit control events.

Cancel retains one checkpoint. Resume uses cached preparation and the next
chunk; interrupted preparation must run again. New evidence conservatively
restarts an active answer and invalidates a paused answer's cached preparation.
Dependency-level recomputation and a parked goal stack are not implemented.

## Privacy and model boundary

Context is supplied explicitly; no perception source runs. Sensitive targets and
password/payment/OTP roles are rejected. Common token patterns and Luhn-valid
card numbers are redacted. This is defence in depth, not complete secret
detection. Desktop domain restrictions, incognito handling, source-side OTP
filtering, and visible opt-in are required before actual perception is enabled.

Evidence is tagged `untrusted=True`. The offline provider extracts fares and
exposes no tools. Real providers must preserve this boundary and derive tool
authority from user intent. Images are opaque references; the core never opens
their paths or URLs. Offline mode explicitly cannot interpret them.

Session context and checkpoints stay in RAM and are cleared on shutdown. No
session log is written. Caller-owned queues, consumer transcripts, custom
providers, process memory, and explicit verbose replay output are outside that
erasure guarantee. Local replay fares are fictional fixtures.

## Implementation references

- [Python cancellation](https://docs.python.org/3.11/library/asyncio-task.html#task-cancellation): propagate cancellation after cleanup.
- [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/): validate input and reject extra fields.
