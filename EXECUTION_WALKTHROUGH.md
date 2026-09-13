# Execution walkthrough — Text-to-SQL PoC

User-friendly path of one question through the code, with a concrete example.
For architecture trade-offs see [DESIGN.md](DESIGN.md).

## How a question runs (step by step)

Think of the CLI as a short assembly line: your English question goes in one end; SQL + a readable answer come out the other.

**Example question**

> How many bank accounts are located in the Prague region?

### 0. Startup (once per CLI session)

When you run `uv run cli`:

1. `cli.py` loads `FIREWORKS_API_KEY` and opens `data/financial.sqlite`.
2. `TextToSQLAgent` builds a **schema card** once via `build_schema_context()`:
   - live column names/types from SQLite (`PRAGMA`)
   - human descriptions from BIRD CSVs (e.g. what `district.A3` means)
   - domain hints (loan status `A/B/C/D`, `OWNER` vs `DISPONENT`, region names, …)
3. That card becomes the **system prompt** for every later question.

You don't see this step in the REPL — it happens before the first `>`.

### 1. You type a question

```text
> How many bank accounts are located in the Prague region?
```

`cli.py` calls `agent.ask(question)`.

### 2. Build the model messages

`build_messages()` assembles:

| Role | Content |
|------|---------|
| **system** | Schema card (tables, columns, enums, join keys) |
| **history** (optional) | Prior Q → SQL → result summaries, for follow-ups |
| **user** | Your question (or question + failed SQL + error on a retry) |

For this first question there is no history yet — just system + user.

### 3. Generate SQL (Fireworks)

`generate_sql()` → `FireworksLLM.chat()`:

- Sends the messages to the configured model (`FIREWORKS_MODEL`, e.g. DeepSeek Flash or Kimi).
- Model returns text that *should* be a SELECT.
- `strip_sql_fences()` cleans markdown fences into plain SQL.

**Example output after cleanup:**

```sql
SELECT COUNT(*)
FROM account
JOIN district ON account.district_id = district.district_id
WHERE district.A3 = 'Prague';
```

Why `A3` and `'Prague'`? Because the schema card told the model that `district.A3` is the region name and the stored value is exactly `Prague` — not `prague` or `Praha`.

### 4. Safety gate (before touching the DB)

`is_safe_select()` rejects anything that isn't read-only:

- must be `SELECT` or `WITH … SELECT`
- no `INSERT` / `UPDATE` / `DELETE` / `DROP` / …
- no multiple statements

If this fails, the agent **does not execute**; it retries with the rejection reason (up to 2 repairs).

### 5. Execute on SQLite

`execute_sql()` → `utils.query_db()` runs the SQL on `financial.sqlite`.

**Example result:** one row, `COUNT(*) = 554`.

### 6. Format and return

`format_result_summary()` turns the table into a short answer, e.g.:

```text
COUNT(*): 554
```

Everything is packaged in `AgentResult`: `sql`, `rows`, `summary`, `attempts`, plus `llm_ms` / `sql_ms` / `total_ms`.

The CLI prints:

```text
--- SQL ---
SELECT COUNT(*) FROM account JOIN district ... WHERE district.A3 = 'Prague';

--- Result ---
COUNT(*): 554

latency: total=0.85s llm=0.85s sql=0.00s (attempts=1)
```

### 7. If SQL fails — repair loop (example)

Suppose the model hallucinated a column:

```sql
SELECT COUNT(*) FROM account WHERE region = 'Prague';
```

SQLite errors (`no such column: region`). Then:

1. Catch the error.
2. Re-prompt with **question + previous SQL + error**.
3. Model returns a corrected query (using `district.A3` via a join).
4. Safety gate → execute again.
5. Stop after success, or after **3 total attempts** (1 + 2 retries).

### 8. Follow-up questions

If you then ask:

> Break that down by district name

…the agent includes the previous SQL/result in **history**, so the model can refine instead of starting from scratch. Type `reset` to clear that memory.

### Happy path vs repair (picture)

```text
Question
   │
   ▼
Schema-grounded prompt ──► Fireworks model ──► SQL text
                                                 │
                                                 ▼
                                         Safety gate (SELECT only)
                                            │ pass          │ fail
                                            ▼               └──► repair ──┐
                                         SQLite execute                   │
                                            │ ok        │ error           │
                                            ▼           └──── repair ◄────┘
                                         Result summary
                                              │
                                              ▼
                                         AgentResult → CLI
```
