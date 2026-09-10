# Email to Raul Jimenez

**To:** Raul Jimenez <raul.j@gitlab.com>  
**From:** Solutions Team <solutions@fireworks.ai>  
**Subject:** Re: Help Needed: Agentic BI CLI Product for GitLab Customers

Hi Raul,

Thanks for the detailed brief — we built a working proof-of-concept that addresses the quality, latency, and cost concerns you raised, using open models on Fireworks rather than a proprietary baseline.

## What we built

An interactive terminal agent (`uv run cli`) that:

- Grounds every request in your live SQLite schema **plus** the BIRD column documentation (including value enums like loan status A/B/C/D, OWNER vs DISPONENT, and district regions)
- Generates read-only SQL via Fireworks (`accounts/fireworks/models/kimi-k2p6` by default)
- Executes the query, shows SQL + results, and supports follow-up questions
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

Artifacts: `dev_answers.json` (model SQL + answer) and `DESIGN.md` (architecture trade-offs).

We validated by (1) live Fireworks inference for every question, (2) executing generated SQL against `financial.sqlite`, and (3) comparing normalized result bags to the published expected results — not by string-matching SQL, since equivalent queries can differ.

## Key findings

- **Quality:** Schema docs + explicit enum hints (region names, loan status letters, OWNER vs DISPONENT) were enough to reach **10/10** on the public dev set, including tier-3 multi-join questions. Remaining production risk is generalization beyond these 10 questions — hence CI eval expansion below.
- **Latency path to under 3s P50:** Single successful generation + local SQLite exec is typically well under the old ~7s prototype; retries only fire on failure. Next step is instrumented TTFT/exec timing and a faster fallback model for simple lookups.
- **Cost:** Open Fireworks models avoid GPT-class unit economics at your projected ~30k queries/day. A quality router (cheap/fast model first, escalate hard queries) keeps cost predictable as you scale past the first 1,000 users.

## What we'd tackle next (scope to production)

1. **Tool-calling agent loop** — let the model call `list_tables` / `execute_sql` / `sample_rows` instead of a fixed retry wrapper (follow-up live session).
2. **Schema retrieval** for larger customer DBs — embed `get_rag_corpus()` with `qwen3-embedding-8b`; keep full-schema prompts only for small DBs.
3. **Eval harness in CI** — expand beyond the 10 public questions; track result-match rate, p50/p95 latency, $/query.
4. **Safety & tenancy** — read-only DB roles, statement timeouts, row limits, and per-customer connection strings as you envisioned.
5. **Latency budget** — measure and optimize prompt size, model tiering, and connection pooling toward under 3s P50.

## Post-training (fine-tuning) recommendation

Prompt + retrieval should remain the first line of defense. Once you have a few hundred reviewed `(question, gold_sql)` pairs from staging/production — including corrections from the repair loop — we recommend a Fireworks LoRA/fine-tune on your SQL dialect and join patterns. Ship behind the same eval gate (result match + latency). Fine-tuning pays off when failure modes are stable and frequent; it complements, rather than replaces, schema grounding and safety checks. We can also use preference pairs (correct vs incorrect SQL) if you want DPO-style alignment later.

## AI assistance note

I used AI coding assistance while implementing and iterating on this PoC (scaffolding, boilerplate, and draft wording), and personally reviewed the architecture, validation approach, and customer-facing recommendations.

Happy to walk through the repo in a follow-up and extend it live (tool calling, performance measurement, agent improvements).

Best regards,  
Tom Kurzydym  
Solutions / Field Engineering, Fireworks AI
