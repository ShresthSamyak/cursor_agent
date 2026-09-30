# metrics_models

Generated 2026-09-30 18:22; time scale 1.0, reps 1, models: TRAIL_LLM=auto, TRAIL_STT=whisper.

| Metric | Value | Target (PDF p. 17) |
| --- | --- | --- |
| Public set, plain average | 100.0 | clearly above ~52 |
| Public set, weighted (a/v x1.5, L3/L4 x1.25) | 100.0 | |
| Trail suite, plain average | 100.0 | |
| Time to yield p50 / p95 (virtual ms) | 38.0 / 52.0 | < 150 ms |
| Correction to revised answer p50 (virtual ms) | 2282.0 | < 300 ms on a fork hit |
| Fork hit rate | 0.0 (0/10) | report it |
| Backchannel false stops | 0 | 0 |
| Stale-output leaks (trace / runtime) | 0 / 0 | 0 |
| Duplicate writes | 0 | 0 |
| Stale results ignored / calls cancelled / compensations | 0 / 16 / 1 | |
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
| trail_01_backchannel_search | trail | 100.0 |
| trail_02_backchannel_booking | trail | 100.0 |
| trail_03_correction_city | trail | 100.0 |
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
| trail_14_say_again | trail | 100.0 |
| trail_15_ticket_confirmed | trail | 100.0 |
| trail_16_ticket_declined | trail | 100.0 |
| trail_17_ticket_interrupted | trail | 100.0 |
| trail_18_heckler | trail | 100.0 |
| trail_19_idempotent_booking | trail | 100.0 |
| trail_20_unseen_state_modifying | trail | 100.0 |
| st_01_get_to | stress | 100.0 |
| st_02_any_seats | stress | 100.0 |
| st_03_whats_flying | stress | 100.0 |
| st_04_head_to | stress | 100.0 |
| st_05_double_correction | stress | 100.0 |
| st_06_interrupt_just_before_return | stress | 100.0 |
| st_07_retract_during_booking | stress | 100.0 |
| st_08_hotel_nights | stress | 100.0 |
| st_09_car_enum | stress | 100.0 |
| st_10_table_nested | stress | 100.0 |
| st_11_flight_status | stress | 100.0 |
| st_12_currency | stress | 100.0 |
| st_13_manual_timeout | stress | 100.0 |
| st_14_cancel_not_found | stress | 100.0 |
| st_15_ticket_high | stress | 100.0 |
| st_16_thanks | stress | 100.0 |
| st_17_book_three_passengers | stress | 100.0 |
| st_18_intent_change_to_weather | stress | 100.0 |
