# Code Explanation — Interview Walkthrough

This document explains how the text-to-SQL system works **module by module**,
aligned with the Google-style docstrings in `src/` and `scripts/`. Use it as a
spoken walkthrough (≈2–3 minutes) or a deeper dive when an interviewer opens a file.

## End-to-end flow

```
User question
  → CLI / TextToSQLAgent.ask()
      → system prompt: schema (PRAGMA + BIRD CSVs + domain enums)
      → Fireworks chat (kimi-k2p6 by default)
      → strip fences / extract SQL
      → safety gate (SELECT | WITH…SELECT only; comments stripped)
      → SQLite execute (busy_timeout + row cap)
      → on error: repair with failed SQL + error (≤2 retries)
  → AgentResult {sql, rows, summary, error, attempts}
```

Eval (`scripts/run_eval.py`) uses the same agent with `keep_history=False` and
scores **result-set equality**, not SQL string match.

---

## `src/__init__.py`

Package marker. Module docstring states this is the Fireworks text-to-SQL
take-home package.

---

## `src/utils.py` — DB I/O (no LLM)

Google docstrings cover every public helper (`load_db`, `query_db`, `get_schema`,
`load_column_descriptions`, `get_rag_corpus`, `print_table_schema`).

| Function | Role |
|----------|------|
| `load_db` | Open `data/financial.sqlite`, enable FKs, set `PRAGMA busy_timeout` |
| `query_db` | Run SQL → DataFrame or list[dict]; optional **row cap** (default 500) |
| `get_schema` | `PRAGMA table_info` for all tables |
| `load_column_descriptions` | BIRD per-table CSVs under `data/database_description/` |
| `get_rag_corpus` | Flat docs for a future RAG path (unused by the PoC prompt) |
| `print_table_schema` | Human-readable schema dump for debugging |

**Interview note:** Utils stay LLM-free so schema grounding and the agent can
share one connection/query path. Row cap + busy timeout are soft guards for
interactive use (e.g. large `trans` table), not a full production sandbox.

---

## `src/schema_context.py` — Prompt grounding

| Symbol | Role |
|--------|------|
| `DOMAIN_HINTS` | High-signal enums: loan `A/B/C/D`, `OWNER`/`DISPONENT`, regions, `"order"` quoting, join keys |
| `_row_desc` | Format one BIRD CSV row into a prompt bullet |
| `build_schema_context` | Merge PRAGMA columns + BIRD docs + domain hints into one system-prompt block |

**Why this matters:** Accuracy on this DB comes less from “clever agents” and
more from **exact value vocab** in the prompt. Full schema fits context (~8
tables); RAG is deferred (see `get_rag_corpus` + `DESIGN.md`).

---

## `src/llm_client.py` — Fireworks / OpenAI-compatible client

| Symbol | Role |
|--------|------|
| `get_model_name` | `FIREWORKS_MODEL` or default `kimi-k2p6` |
| `create_client` | OpenAI SDK → Fireworks base URL; requires `FIREWORKS_API_KEY` |
| `_extract_sql_candidate` | From reasoning text: last fenced block, else last SELECT/WITH |
| `_floor_max_tokens` | Enforce `MIN_MAX_TOKENS` (1024) so reasoning models aren’t starved |
| `FireworksLLM.chat` | Completion call; if `content` empty → fall back to `reasoning_content` |

**Interview note:** kimi-style reasoning models often put CoT in
`reasoning_content` and leave `content` empty when the token budget is too low.
The client keeps a **max_tokens floor** and extracts SQL from reasoning — a real
Fireworks gotcha, not a toy detail.

---

## `src/agent.py` — Core loop

| Symbol | Role |
|--------|------|
| `AgentResult` | Structured turn result; `to_answer_payload()` → eval `{sql, answer}` |
| `strip_sql_fences` | Markdown / prose cleanup → single statement with `;` |
| `_strip_sql_comments` | Remove `--` / `/* */` before safety checks |
| `is_safe_select` | Single statement; block DML/DDL; allow SELECT or WITH…SELECT |
| `format_result_summary` | CLI/eval-friendly text (scalar vs table preview) |
| `TextToSQLAgent` | Orchestrates messages → LLM → safety → execute → repair |

**Repair loop (`ask`):**

1. Build messages (`build_messages`): system schema + optional capped history + user question.
2. On retry, include **Previous SQL** and **error** (P0 hardening) so the model
   can fix concrete mistakes.
3. `generate_sql` → `is_safe_select` → `execute_sql`.
4. Success: optionally append to `history`, then `_trim_history` (default last 6 messages).
5. Failure: up to `max_retries` (default 2) extra attempts.

**Safety philosophy:** Gate stays **outside** the model — never trust the LLM
to “only generate SELECT”. Comments are stripped so `-- DROP` tricks don’t slip past.

---

## `src/cli.py` — Interactive REPL

| Symbol | Role |
|--------|------|
| `_ensure_repo_root_on_path` | `sys.path` + `chdir` so `data/` and `import src` work from any launch mode |
| `main` | Load dotenv, require API key, loop: question → `agent.ask` → print SQL/result |

Commands: `exit`/`quit`/`q`, `reset` (clears history). Deferred imports after
dotenv are intentional so the key is available before constructing the client.

---

## `scripts/run_eval.py` — 10-question harness

| Symbol | Role |
|--------|------|
| `_normalize_value` / `_normalize_rows` | Bag-of-values compare (ignore column names/order; round floats) |
| `results_match` | Gold vs agent rows |
| `main` | Fresh agent per question → write `dev_answers.json` + pass/fail |

**Interview note:** Scoring **results**, not SQL strings, matches how a customer
cares about answers and avoids false fails on equivalent queries.

---

## Design choices to defend verbally

1. **Single agent + repair** over multi-agent for Stage-1: lowest complexity that hit quality on this schema size.
2. **Full schema in prompt** over RAG: ~8 tables; retrieval miss risk > benefit.
3. **Open Fireworks model** as default: unit economics + OpenAI-compatible drop-in; proprietary as optional escalation.
4. **History cap + row cap + busy_timeout**: cheap interactive hardening without rewriting architecture.
5. **Next Stage-2 moves:** tool-calling (`execute_sql`), latency timers, schema RAG when tables grow.

Docstrings use Google sections (`Args:`, `Returns:`, `Raises:`, `Attributes:`)
throughout so an interviewer can open any public symbol and get the contract
without reading the body first.
