# metrics_rules

Generated 2026-09-30 05:11; time scale 4.0, reps 1, models: TRAIL_LLM=none, TRAIL_STT=none.

| Metric | Value | Target (PDF p. 17) |
| --- | --- | --- |
| Public set, plain average | 87.0 | clearly above ~52 |
| Public set, weighted (a/v x1.5, L3/L4 x1.25) | 83.7 | |
| Trail suite, plain average | 99.4 | |
| Time to yield p50 / p95 (virtual ms) | 49.0 / 88.0 | < 150 ms |
| Correction to revised answer p50 (virtual ms) | 2060.0 | < 300 ms on a fork hit |
| Fork hit rate | 0.0 (0/7) | report it |
| Backchannel false stops | 0 | 0 |
| Stale-output leaks (trace / runtime) | 1 / 0 | 0 |
| Duplicate writes | 0 | 0 |
| Stale results ignored / calls cancelled / compensations | 0 / 11 / 1 | |
| Runtime errors | 0 | 0 |

| Scenario | Suite | Score (median) |
| --- | --- | --- |
| pub_01_text_simple | public | 100.0 |
| pub_02_text_interrupt | public | 100.0 |
| pub_03_text_chained_booking | public | 100.0 |
| pub_04_text_no_tool | public | 100.0 |
| pub_05_audio_asr_ambiguity | public | 53.8 |
| pub_06_audio_disfluency | public | 56.9 |
| pub_07_visual_port_lookup | public | 72.3 |
| pub_08_text_tool_failure | public | 100.0 |
| pub_09_text_unseen_tool | public | 100.0 |
| trail_01_backchannel_search | trail | 100.0 |
| trail_02_backchannel_booking | trail | 100.0 |
| trail_03_correction_city | trail | 99.8 |
| trail_04_correction_date | trail | 100.0 |
| trail_05_correction_passenger | trail | 100.0 |
| trail_06_compensate_committed | trail | 100.0 |
| trail_07_addition_passengers | trail | 100.0 |
| trail_08_addition_date | trail | 100.0 |
| trail_09_retraction | trail | 100.0 |
| trail_10_stop | trail | 100.0 |
| trail_11_detour_and_back | trail | 100.0 |
| trail_12_abandon_for_device | trail | 100.0 |
| trail_13_which_is_cheaper | trail | 100.0 |
| trail_14_say_again | trail | 89.1 |
| trail_15_ticket_confirmed | trail | 100.0 |
| trail_16_ticket_declined | trail | 100.0 |
| trail_17_ticket_interrupted | trail | 100.0 |
| trail_18_heckler | trail | 100.0 |
| trail_19_idempotent_booking | trail | 100.0 |
| trail_20_unseen_state_modifying | trail | 100.0 |
