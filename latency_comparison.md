# Latency comparison — 10 development questions

End-to-end timing on `data/dev_questions.json`. **`llm_ms` is full LLM wall time** (the chat-completion round-trip until the complete response), **not** time-to-first-token (TTFT). `sql_ms` is local SQLite execution only.

Harness: `python scripts/bench_latency.py`.

## Results

| Model | Quality | P50 (total) | P95 (total) | vs 3s P50 target |
|-------|---------|-------------|-------------|------------------|
| `accounts/fireworks/models/kimi-k2p6` | 10/10 (eval) | **≈ 6.1s** | **≈ 54s** | Misses (LLM-dominated) |
| `accounts/fireworks/models/deepseek-v4p1-flash` | **10/10** | **≈ 0.85s** | **≈ 2.5s** | **Met** — all 10 under 3s |

Relative to kimi-k2p6, deepseek-v4p1-flash is **~7× faster at P50** and **~22× faster at P95**.

## Where time goes

- **SQL is not the bottleneck:** SQLite on `financial.sqlite` is **<100ms** per question.
- **LLM dominates** kimi-k2p6 P50/P95. A reasoning-style completion (full wall time, not TTFT) explains the multi-second — and occasionally multi-tens-of-seconds — tail.
- **EU→US RTT is not the driver.** Cross-Atlantic network delay is tens of milliseconds, not seconds. Shaving RTT would not move P50/P95 onto the 3s budget while the model call itself is 6s–54s.

## Pricing

Customer baseline: **GPT-5.5** (~7s, mixed quality, ~30k queries/day). GPT-5.5 is **not on Fireworks**; that row uses the **OpenAI API list**. Fireworks rows are **Standard serverless** ([pricing](https://docs.fireworks.ai/serverless/pricing)): `kimi-k2p6` → **Kimi K2.6**; `deepseek-v4p1-flash` → published **DeepSeek V4 Flash (0731) Flash**.

Rates are USD per 1M tokens (**input / cached input / output**).

| Model | Input | Cached input | Output |
|-------|------:|-------------:|-------:|
| GPT-5.5 (OpenAI API list) | $5.00 | $0.50 | $30.00 |
| Kimi K2.6 | $0.95 | $0.16 | $4.00 |
| DeepSeek V4 Flash | $0.22 | $0.007 | $0.66 |

**Illustrative 30k queries/day** using this PoC prompt: **~2,250 input tokens** (schema+question) and **~50 SQL output tokens** (chars/4 estimate — **not billed usage**). Kimi **reasoning tokens would raise kimi $/query further**.

| Model | Uncached $/query | Uncached $/day | vs GPT-5.5 | Prompt-cached schema $/day |
|-------|------------------|----------------|------------|----------------------------|
| GPT-5.5 | ~$0.013 | ~$379 | — | ~$82 |
| Kimi K2.6 | ~$0.0023 | ~$69 | ~5.5× cheaper | ~$17 |
| DeepSeek V4 Flash | ~$0.0005 | ~$16 | ~24× cheaper | ~$1.60 |

## Recommendation

Ship a **model router**:

1. **Fast default** (`deepseek-v4p1-flash`) for the interactive CLI — already under 3s P50 and P95 on this set, with 10/10 result match.
2. **Escalate hard queries** (low confidence, repair-loop exhaustion, or known-hard analytical patterns) to a stronger reasoning model such as `kimi-k2p6`.

Keep P50/P95 in CI via `scripts/bench_latency.py` as the question set grows.

Checked-in machine-readable reports: `latency_report.json`, `latency_report_kimi-k2p6.json`, `latency_report_deepseek-v4p1-flash.json`.
