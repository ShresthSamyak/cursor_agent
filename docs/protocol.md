# Local protocol, version 0

This is a development protocol, **not verified official kit compatibility**.
`ParticipantAgent(in_queue, out_queue)` has optional `setup()` and argument-free
`run()` coroutines. Inject `provider` and `protocol` via keyword arguments.
The adapter owns external serialization; the runtime receives validated events.
Queues belong to the same asyncio event loop.

## Inputs

Envelopes have `type`, optional unique `event_id`, timestamp `ts`, `source`, and
`app`. IDs/timestamps default locally. Repeated IDs are deduplicated within the
bounded window. Unknown fields or types are rejected without echoing contents.

| Type | Payload | Behavior |
| --- | --- | --- |
| `user_text` | nonempty `text` | Duck and start a turn |
| `speech_partial` | nonempty `text` | Prepare early without speaking |
| `speech_final` | nonempty `text` | Reuse matching partial or replace it |
| `vad_start` | none | Mark speech active and duck |
| `vad_end` | none | Clear speech activity; wait for intent |
| `typing` | boolean `active` | Track activity; duck on start |
| `cancel` | none | Invalidate work, retain checkpoint |
| `resume` | none | Continue when user is inactive |
| `dwell`, `select` | `target` | Filter/store context; invalidate answer |
| `image` | `image_ref`, optional `text` | Pass opaque reference to provider |
| `app_switch` | `app` | Track active app |
| `hover`, `save`, `test_run` | target for hover | Reserved, acknowledged; no specialist |
| `session_end` | none | Cancel workers, clear memory, finish |

`None` on the adapter's input queue also ends the session.

Example messages, one at a time:

```json
{"type":"dwell","app":"chrome","target":{"text":"Saturday INR 5,000","context":"Chandigarh to Goa"}}
{"type":"user_text","text":"Which flight is cheapest?"}
{"type":"cancel"}
{"type":"resume"}
{"type":"session_end"}
```

To demonstrate resume, send cancel after receiving a `token`, and resume after
`cancelled`. Sending all messages at once prioritizes input and may end the
session before an answer. Scenario files implement these synchronization points.

Target fields: `text`, `context`, `role`, `sensitive`, `dwell_ms`, optional `bbox`.
Dwell measurement and the approximately 350-ms threshold belong to later
perception sources; the core currently accepts explicit events.

## Outputs

Outputs include `type`, `session_id`, `version`, and `text`; `turn_id`, `code`, and
`event_id` are included when applicable.

| Type | Meaning |
| --- | --- |
| `session_started`, `session_ended` | Session lifecycle |
| `turn_started`, `turn_resumed` | Output identity; prefetch has code `prefetch` |
| `duck`, `unduck` | Renderer flow control |
| `token` | Append streamed chunk without extra spaces |
| `done` | Answer completed |
| `cancelled` | Work stopped with checkpoint retained |
| `context_added`, `context_rejected` | Context accepted/rejected |
| `event_ack` | Valid nonduplicate input handled |
| `status` | Reserved event or user-active response |
| `error` | Sanitized error code and description |

Malformed input leaves the session usable. `event_ack` is not a provider
completion signal. Consumers must enforce playback ducking and output identity;
see [architecture.md](architecture.md).

## Official integration

1. Read the kit protocol and reference agent.
2. Map every official input/output in a `ProtocolAdapter` implementation.
3. Match session boundaries, queues, streaming, tool manifests, errors, and media.
4. Connect the allowed cloud provider and corpus through the provider contract.
5. Add synthetic protocol fixtures without credentials or private data.
6. Run every public scenario and evaluator; record actual results.
