# Design Notes — Text-to-SQL PoC

This document answers the README **Things to Consider** and records architecture choices for this PoC and how to extend it in a follow-up session (tools, model routing, agent improvements).

## Architecture

```
User question
    -> TextToSQLAgent.ask()
        -> schema-grounded system prompt (PRAGMA + BIRD column CSVs + domain enums)
        -> Fireworks chat completion (kimi-k2p6 by default)
        -> strip markdown fences
        -> safety gate (SELECT/WITH only; block DML/DDL)
        -> utils.query_db
        -> on SQLite error: retry with error feedback (<=2 retries)
        -> structured AgentResult {sql, rows, summary, error, attempts, llm_ms, sql_ms, total_ms}
```

Modules:

| Module | Role |
|--------|------|
| `src/llm_client.py` | OpenAI-compatible Fireworks client; model via `FIREWORKS_MODEL` |
| `src/schema_context.py` | Prompt-ready schema + value enums (loan status, OWNER/DISPONENT, regions, ...) |
| `src/agent.py` | Generation, safety, execution, retries, conversation history, latency timers |
| `src/cli.py` | Interactive REPL (`uv run cli`); prints LLM/SQL/total latency |
| `src/utils.py` | Unchanged helpers: load/query schema, column docs |
| `scripts/run_eval.py` | Non-interactive run of 10 dev questions -> `dev_answers.json` + pass/fail |
| `scripts/bench_latency.py` | P50/P95 latency bench on the same 10 questions |

## What happens when generated SQL doesn't execute?

1. Catch the SQLite/`pandas` error.
2. Re-prompt the model with the original question **plus** the error text and an instruction to return only corrected SQL.
3. Allow up to **2 retries** (`max_retries=2`).
4. Surface the final error in the CLI / `AgentResult` if still failing.

This is cheaper than a full multi-agent planner for a first PoC and already recovers many hallucinated column/table mistakes. A follow-up can add tool-calling (`execute_sql` as a tool) so the model drives the repair loop itself.

## How does the agent know about the database?

**Static schema grounding** in the system prompt:

- Live `PRAGMA table_info` via `get_schema` (authoritative column names/types).
- BIRD `data/database_description/*.csv` via `load_column_descriptions` (human descriptions + value enums).
- Explicit domain hints: loan `A/B/C/D`, `OWNER` vs `DISPONENT`, `district.A3` regions, gender codes, ISO dates, quoting `"order"`.

Why not RAG for this PoC? The schema is small (~8 tables). Stuffing the full docs stays well within context and avoids retrieval miss risk. For production schemas with hundreds of tables, swap in embedding retrieval over `get_rag_corpus()` (already in utils) using `qwen3-embedding-8b` — the agent API stays the same if we inject `schema_text`.

## Agent architecture trade-offs

| Approach | Pros | Cons | This PoC |
|----------|------|------|----------|
| Single-shot prompt | Lowest latency/cost | Fragile on hard joins | Baseline customer had this |
| Schema-grounded + repair | Reliable on this DB size; simple | Prompt grows with schema | **Chosen** |
| Tool-calling agent | Model can inspect schema/sample rows | Extra round-trips; harder to debug | Next |
| Multi-agent (planner/SQL/critic) | Higher accuracy on hard queries | Latency/cost; ops complexity | Later |

## Open-source (Fireworks) vs proprietary models

Customer pain: GPT-5.5 quality at unsustainable cost (~30k queries/day) and ~7s latency, with mixed result quality.

GPT-5.5 is **not on Fireworks**; proprietary rates below are the **OpenAI API list**. Fireworks rates are **Standard serverless** ([docs](https://docs.fireworks.ai/serverless/pricing)). Mapping: `kimi-k2p6` → **Kimi K2.6**; `deepseek-v4p1-flash` → published **DeepSeek V4 Flash (0731) Flash**.

USD per 1M tokens (**input / cached input / output**):

| Model | Input | Cached input | Output |
|-------|------:|-------------:|-------:|
| GPT-5.5 (OpenAI) | $5.00 | $0.50 | $30.00 |
| Kimi K2.6 | $0.95 | $0.16 | $4.00 |
| DeepSeek V4 Flash | $0.22 | $0.007 | $0.66 |

**Illustrative 30k queries/day** on this PoC prompt: **~2,250 input tokens** (schema+question), **~50 SQL output tokens** (chars/4 estimate — **not billed usage**). Kimi **reasoning tokens would raise kimi $/query further**.

| Model | Uncached $/query | Uncached $/day | Prompt-cached schema $/day |
|-------|------------------|----------------|----------------------------|
| GPT-5.5 | ~$0.013 | ~$379 | ~$82 |
| Kimi K2.6 | ~$0.0023 | ~$69 (~5.5× cheaper) | ~$17 |
| DeepSeek V4 Flash | ~$0.0005 | ~$16 (~24× cheaper) | ~$1.60 |

- **Fireworks open models:** 10/10 on the public dev set, OpenAI-compatible drop-in for GitLab's CLI, and ~5.5×–24× lower uncached $/day than GPT-5.5 on this prompt shape.
- **Proprietary:** may still win on the hardest analytical questions; useful as a **router/escalation** (or judge) tier, not as the default interactive model.
- **Recommendation:** ship a Fireworks **model router** — fast/cheap default (`deepseek-v4p1-flash`), escalate hard queries to a stronger reasoning model (`kimi-k2p6`). Measure quality/latency/cost on a held-out set.

## Follow-up questions / conversation context

The CLI keeps a rolling `history` of user questions and assistant SQL+result summaries. Follow-ups ("break that down by district") reuse prior SQL/results. `reset` clears history. Eval runs with `keep_history=False` so questions stay independent.

## Latency, cost, accuracy for an interactive CLI

Target: **under 3s P50** end-to-end.

**Measurement:** `TextToSQLAgent.ask()` records `llm_ms`, `sql_ms`, and `total_ms` with `time.perf_counter`. **`llm_ms` is full LLM wall time** (the complete chat-completion round-trip), **not** time-to-first-token (TTFT). `sql_ms` is SQLite execution only. The CLI prints the split after each query. `scripts/bench_latency.py` runs the 10 dev questions and reports P50/P95.

**Measured on the 10-question dev set:**

| Model | Quality | P50 | P95 |
|-------|---------|-----|-----|
| `kimi-k2p6` | 10/10 (eval) | ≈ 6.1s | ≈ 54s |
| `deepseek-v4p1-flash` | 10/10, all under 3s | ≈ 0.85s | ≈ 2.5s |

kimi-k2p6 is **LLM-dominated** (SQL **<100ms**). EU→US RTT is not the driver of the 6s/54s figures. deepseek-v4p1-flash is **~7× faster at P50** and **~22× faster at P95**.

**Recommendation:** a **model router** — fast default (`deepseek-v4p1-flash`) for the interactive CLI; escalate hard/analytical queries to a stronger reasoning model.

Levers:

1. **Model choice / router** — fast default; escalate hard queries.
2. **Prompt size** — full schema is fine here; for larger DBs, retrieve top-k tables only.
3. **Retries** — only on failure (don't pay for a second call on success).
4. **Temperature 0** — deterministic SQL, better cacheability.
5. **Connection reuse** — single SQLite connection per CLI session.

Accuracy strategy: schema enums + repair loop + offline eval on `dev_questions_with_answers.json` (result-set match, not string-equal SQL).

## Post-training (fine-tuning)

**When prompt/RAG is not enough:** systematic failures on customer-specific dialect, ambiguous business terms, or schemas that change slowly but confuse the base model.

**Data needed:**

1. `(question, gold_sql, result)` triples from eval + production (with human review).
2. Hard negatives: failed generations with corrections (from the repair loop — free signal).
3. Schema-conditioned examples so the adapter doesn't memorize one DB only.
4. Preference pairs (correct vs incorrect SQL) if using DPO/ORPO.

**Approach on Fireworks:**

1. Continue prompt + retrieval until you have at least a few hundred clean examples.
2. Fine-tune / LoRA an open model on Fireworks for the GitLab SQL dialect + common join patterns.
3. Eval on held-out questions + regression suite; gate deploy on result-match rate and p95 latency.
4. Refresh periodically from reviewed production traces (human-in-the-loop).

Fine-tuning beats prompt engineering when error modes are **stable and frequent** and you can collect labeled SQL at low cost. It does **not** replace schema grounding or safety checks.

## Extensibility

- `FireworksLLM.chat` already accepts `**kwargs` (tools, response_format).
- `TextToSQLAgent` methods (`generate_sql`, `execute_sql`, `build_messages`) are overridable.
- `AgentResult` is a stable structured return for tooling / telemetry (`llm_ms`, `sql_ms`, `total_ms`).
- `get_rag_corpus()` is ready for embedding-based schema retrieval.


## Implementation note: reasoning models

`kimi-k2p6` emits chain-of-thought in `reasoning_content`. With a low `max_tokens` budget, all tokens can be consumed by reasoning and `content` arrives empty (finish_reason=`length`). This PoC defaults to `max_tokens=8192` and falls back to extracting the last SQL fence / SELECT from `reasoning_content` when `content` is blank.
