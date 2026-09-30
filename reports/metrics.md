# metrics

Generated 2026-09-30 09:41; time scale 1.0, reps 1, models: TRAIL_LLM=auto, TRAIL_STT=whisper.

| Metric | Value | Target (PDF p. 17) |
| --- | --- | --- |
| Public set, plain average | 100.0 | clearly above ~52 |
| Public set, weighted (a/v x1.5, L3/L4 x1.25) | 100.0 | |
| Trail suite, plain average | None | |
| Time to yield p50 / p95 (virtual ms) | 37.0 / 37.0 | < 150 ms |
| Correction to revised answer p50 (virtual ms) | None | < 300 ms on a fork hit |
| Fork hit rate | 0.0 (0/1) | report it |
| Backchannel false stops | 0 | 0 |
| Stale-output leaks (trace / runtime) | 0 / 0 | 0 |
| Duplicate writes | 0 | 0 |
| Stale results ignored / calls cancelled / compensations | 0 / 1 / 0 | |
| Runtime errors | 0 | 0 |

| Scenario | Suite | Score (median) |
| --- | --- | --- |
| pub_01_text_simple | public | 100.0 |
| pub_02_text_interrupt | public | 100.0 |
| pub_03_text_chained_booking | public | 100.0 |
| pub_04_text_no_tool | public | 100.0 |
| pub_05_audio_asr_ambiguity | public | 100.0 |
| pub_06_audio_disfluency | public | 100.0 |
| pub_07_visual_port_lookup | public | 100.0 |
| pub_08_text_tool_failure | public | 100.0 |
| pub_09_text_unseen_tool | public | 100.0 |
