# Experiments

One file per experiment: setup, scores, cost and caveats, every figure from a run that
passed `make verify`. Newest first. `README.md` at the repo root keeps only the overview and
the headline conclusions. Round-1 records (`r1_*`) were moved out of it on 2026-10-16; their
run ids are in the frozen v1 log, `report/token_log.csv`.

| Date | Experiment | Verdict |
|---|---|---|
| 2026-10-16 | [r2_jev](r2_jev.md) | **Jev no longer needs Gemini for Stage 2.** Jev → Jev-with-photo scores 97% / 98% against the hybrid's 100% / 98%, at about the same cost and with no OCR; 100% precision |
| 2026-10-16 | [r2_family_gpt4o_mini](r2_family_gpt4o_mini.md) | Behind the control on every axis and only 1.3–1.4× cheaper; images bill at ~26–36k tokens a call |
| 2026-10-16 | [r2_family_llama](r2_family_llama.md) | Autos only. S2 97% product vs not; out for S3 (US-dollar prices ×1,000) and S4 (39 false positives); gadgets run failed the gate |
| 2026-10-15 | [r2_family_qwen37_flash](r2_family_qwen37_flash.md) | **Best of the ultra-cheap tier.** Matches the control on product vs not-product, 13–18× cheaper; misses 3 of 19 autos prices |
| 2026-10-15 | [r2_family_mimo_flash](r2_family_mimo_flash.md) | Autos only. 100% prices, but 7× slower, S4 over-calls, only 2.6× cheaper; gadgets run cut short |
| 2026-10-15 | [r2_family_nex_mini](r2_family_nex_mini.md) | Autos only. S2 precision 86%, under target; gadgets run failed the gate on provider errors |
| 2026-10-15 | [r2_control_gemini](r2_control_gemini.md) | **The round-2 baseline.** 100% product vs not-product, 100% prices, S4 micro F1 87% / 77%, $0.002–0.003 per post |
| 2026-10-15 | [r2_family_qwen](r2_family_qwen.md) | Behind the control on all but one axis, 3–5× cheaper; Qwen 2.5 7B out for Stage 4 |
| 2026-09-24 | [r1_stage6_sync](r1_stage6_sync.md) | Stage 6 on a designed change set: 47/47 agreement, 56% fewer posts reprocessed, an unchanged re-sync makes zero AI calls; only `caption_edit` clears n >= 10 |
| 2026-09-24 | [r1_stage3_all_products](r1_stage3_all_products.md) | Scoring only the first product checked 34% of gadgets' prices and hid under-extraction; Gemini + OCR 98% / 100% on every product |
| 2026-09-23 | [r1_stage2_rebaseline](r1_stage2_rebaseline.md) | The label codebook; Jev + Gemini hybrid 100% / 95% at 100% precision; gadgets no longer held out for Stage 2 |
| 2026-09-22 | [r1_stage3_ocr](r1_stage3_ocr.md) | The Stage 3 OCR tier pays on caption-less product cards only, roughly break-even overall; the price guard voided two invented prices |
| 2026-09 | [r1_model_comparison](r1_model_comparison.md) | Round 1's families: Gemini beat GPT-4o Mini on every axis, and cost the most; Stage 3 is ~74% of tokens |
