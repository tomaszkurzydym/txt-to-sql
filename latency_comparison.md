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

## Recommendation

Ship a **model router**:

1. **Fast default** (`deepseek-v4p1-flash`) for the interactive CLI — already under 3s P50 and P95 on this set, with 10/10 result match.
2. **Escalate hard queries** (low confidence, repair-loop exhaustion, or known-hard analytical patterns) to a stronger reasoning model such as `kimi-k2p6`.

Keep P50/P95 in CI via `scripts/bench_latency.py` as the question set grows.

Checked-in machine-readable reports: `latency_report.json`, `latency_report_kimi-k2p6.json`, `latency_report_deepseek-v4p1-flash.json`.
