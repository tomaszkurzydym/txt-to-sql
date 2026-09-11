# Email to Raul Jimenez

**To:** Raul Jimenez <raul.j@gitlab.com>  
**From:** Solutions Team <solutions@fireworks.ai>  
**Subject:** Re: Help Needed: Agentic BI CLI Product for GitLab Customers

Hi Raul,

Thanks for the detailed brief — we built a working proof-of-concept that addresses the quality, latency, and cost concerns you raised, using open models on Fireworks rather than a proprietary baseline.

## What we built

An interactive terminal agent (`uv run cli`) that:

- Grounds every request in your live SQLite schema **plus** the BIRD column documentation (including value enums like loan status A/B/C/D, OWNER vs DISPONENT, and district regions)
- Generates read-only SQL via Fireworks (`accounts/fireworks/models/kimi-k2p6` by default; `deepseek-v4p1-flash` as a fast alternative)
- Executes the query, shows SQL + results + LLM/SQL/total latency, and supports follow-up questions
- Retries automatically (1–2 times) when SQLite returns an error, feeding the error back to the model
- Rejects non-SELECT / dangerous statements before execution

This is a deliberate step up from `Convert this question to SQL: {question}`: schema grounding and a repair loop are the main levers that reduce hallucinated tables/columns and invalid SQL.

## Validation on the 10 development questions

We ran the agent non-interactively against `data/dev_questions.json` and compared result sets to the gold `expected_result` values (column-name-agnostic match).

**Results: 10 / 10 PASS** (all tiers 1–3)

| ID | Result |
|----|--------|
| q_001 | PASS |
| q_002 | PASS |
| q_003 | PASS |
| q_004 | PASS |
| q_005 | PASS |
| q_006 | PASS |
| q_007 | PASS |
| q_008 | PASS |
| q_009 | PASS |
| q_010 | PASS |

Model: `accounts/fireworks/models/kimi-k2p6` (Fireworks serverless).

Artifacts: `dev_answers.json` (model SQL + answer), `DESIGN.md` (architecture trade-offs), and `latency_comparison.md` / `latency_report*.json` (P50/P95 + pricing bake-off).

We validated by (1) live Fireworks inference for every question, (2) executing generated SQL against `financial.sqlite`, and (3) comparing normalized result bags to the published expected results — not by string-matching SQL, since equivalent queries can differ.

## Key findings

- **Quality:** Schema docs + explicit enum hints (region names, loan status letters, OWNER vs DISPONENT) were enough to reach **10/10** on the public dev set, including tier-3 multi-join questions. Remaining production risk is generalization beyond these 10 questions — hence CI eval expansion below.
- **Latency (measured, 10 dev questions):** We instrumented end-to-end time and split **LLM vs SQL**. LLM time is **full completion wall time**, not time-to-first-token.
  - `kimi-k2p6`: **P50 ≈ 6.1s, P95 ≈ 54s**. Latency is **LLM-dominated**; SQL is **<100ms**. EU→US RTT is not the driver.
  - `deepseek-v4p1-flash`: **P50 ≈ 0.85s, P95 ≈ 2.5s**, **10/10**, **all under 3s** (~7× P50 / ~22× P95 vs kimi).
  - **Recommendation:** a **model router** — fast default for the interactive CLI, escalate hard/analytical queries to a stronger reasoning model.
- **Cost (illustrative, ~30k queries/day):** Your GPT-5.5 baseline (~7s, mixed quality) is **not on Fireworks** — that row uses the **OpenAI API list**. Fireworks rows are **Standard serverless** ([docs](https://docs.fireworks.ai/serverless/pricing)): `kimi-k2p6` → **Kimi K2.6**; `deepseek-v4p1-flash` → **DeepSeek V4 Flash (0731) Flash**. Token math is this PoC prompt (**~2,250 input** = schema+question, **~50 SQL output**, chars/4 estimate) — **not billed usage**. Kimi **reasoning tokens would raise kimi $/query further**.

| Model | $/1M tokens (input / cached input / output) | Uncached $/query | Uncached $/day | Prompt-cached schema $/day |
|-------|---------------------------------------------|------------------|----------------|----------------------------|
| GPT-5.5 (OpenAI) | $5.00 / $0.50 / $30.00 | ~$0.013 | ~$379 | ~$82 |
| Kimi K2.6 | $0.95 / $0.16 / $4.00 | ~$0.0023 | ~$69 (~5.5× cheaper) | ~$17 |
| DeepSeek V4 Flash | $0.22 / $0.007 / $0.66 | ~$0.0005 | ~$16 (~24× cheaper) | ~$1.60 |

  Same recommendation as latency: a **model router** (fast/cheap default, escalate hard queries) keeps unit cost predictable past the first 1,000 users.

## What we'd tackle next (scope to production)

1. **Tool-calling agent loop** — let the model call `list_tables` / `execute_sql` / `sample_rows` instead of a fixed retry wrapper (follow-up live session).
2. **Schema retrieval** for larger customer DBs — embed `get_rag_corpus()` with `qwen3-embedding-8b`; keep full-schema prompts only for small DBs.
3. **Eval harness in CI** — expand beyond the 10 public questions; track result-match rate, p50/p95 latency, $/query.
4. **Safety & tenancy** — read-only DB roles, statement timeouts, row limits, and per-customer connection strings as you envisioned.
5. **Model router** — default to `deepseek-v4p1-flash` for interactive P50/P95; escalate hard queries to a stronger reasoning model. Keep p50/p95 in CI as the eval set grows.

## Post-training (fine-tuning) recommendation

Prompt + retrieval should remain the first line of defense. Once you have a few hundred reviewed `(question, gold_sql)` pairs from staging/production — including corrections from the repair loop — we recommend a Fireworks LoRA/fine-tune on your SQL dialect and join patterns. Ship behind the same eval gate (result match + latency). Fine-tuning pays off when failure modes are stable and frequent; it complements, rather than replaces, schema grounding and safety checks. We can also use preference pairs (correct vs incorrect SQL) if you want DPO-style alignment later.

## AI assistance note

I used AI coding assistance while implementing and iterating on this PoC (scaffolding, boilerplate, and draft wording), and personally reviewed the architecture, validation approach, and customer-facing recommendations.

Happy to walk through the repo in a follow-up and extend it live (tool calling, model router, agent improvements).

Best regards,  
Tom Kurzydym  
Solutions / Field Engineering, Fireworks AI
