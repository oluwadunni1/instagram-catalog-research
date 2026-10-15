# Experiments

One file per experiment: setup, scores, cost and caveats, every figure from a run that
passed `make verify`. Newest first. `README.md` at the repo root keeps only the headline
conclusions.

| Date | Experiment | Verdict |
|---|---|---|
| 2026-10-15 | [r2_control_gemini](r2_control_gemini.md) | **The round-2 baseline.** 100% product vs not-product, 100% prices, S4 micro F1 87% / 77%, $0.002–0.003 per post |
| 2026-10-15 | [r2_family_qwen](r2_family_qwen.md) | Behind the control on all but one axis, 3–5× cheaper; Qwen 2.5 7B out for Stage 4 |
