# Text-to-SQL CLI - Home Assignment

Interactive text-to-SQL agent for the Czech retail banking sample DB (`financial.sqlite`), running on **Fireworks** open models via the OpenAI-compatible API.

Dataset license: see [BIRD_LICENSE.md](BIRD_LICENSE.md).

## Quick start

```bash
# From repo root
./setup.sh          # if financial.sqlite is missing
uv sync

export FIREWORKS_API_KEY=fw_...
# optional: export FIREWORKS_MODEL=accounts/fireworks/models/kimi-k2p6
# optional (fast): export FIREWORKS_MODEL=accounts/fireworks/models/deepseek-v4p1-flash

uv run cli
# or: python -m src.cli
```

Type a natural-language question to see generated SQL + results. Commands: `exit` / `quit`, `reset` (clear conversation history).

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FIREWORKS_API_KEY` | yes | Fireworks API key (never commit) |
| `FIREWORKS_MODEL` | no | Default: `accounts/fireworks/models/kimi-k2p6`. Fast alternative: `accounts/fireworks/models/deepseek-v4p1-flash` |
| `FIREWORKS_BASE_URL` | no | Default: `https://api.fireworks.ai/inference/v1` |

Copy `.env.example` to `.env` if you prefer dotenv loading.

## Evaluate on the 10 dev questions

```bash
export FIREWORKS_API_KEY=fw_...
python scripts/run_eval.py
```

Writes `dev_answers.json` at the repo root and prints pass/fail vs `data/dev_questions_with_answers.json`.

## Latency bench (P50 / P95)

```bash
export FIREWORKS_API_KEY=fw_...
python scripts/bench_latency.py --out latency_report.json

# Fast model (measured ≈0.85s P50 / ≈2.5s P95, 10/10, all under 3s)
FIREWORKS_MODEL=accounts/fireworks/models/deepseek-v4p1-flash \
  python scripts/bench_latency.py --out latency_report_deepseek-v4p1-flash.json

# Optional: snapshot the Fireworks /models listing
python scripts/bench_latency.py --list-models --out fireworks_models_available.json
```

Times are **full LLM wall time** (complete chat-completion round-trip), **not** time-to-first-token. The CLI also prints `LLM …ms | SQL …ms | total …ms` after each query.

Checked-in bake-off: [latency_comparison.md](latency_comparison.md) (`kimi-k2p6` ≈6.1s / 54s P50/P95 vs `deepseek-v4p1-flash` ≈0.85s / 2.5s).

## Project layout

```
.
├── README.md
├── DESIGN.md                 # architecture + Things to Consider
├── EMAIL_TO_RAUL.md          # customer email
├── BIRD_LICENSE.md
├── dev_answers.json          # live model outputs (after eval)
├── pyproject.toml
├── src/
│   ├── cli.py                # interactive REPL
│   ├── agent.py              # TextToSQLAgent
│   ├── llm_client.py         # Fireworks OpenAI-compatible client
│   ├── schema_context.py     # schema + enum prompt builder
│   └── utils.py              # DB helpers (starter)
├── scripts/
│   ├── run_eval.py
│   └── bench_latency.py      # P50/P95 on the 10 dev questions
├── latency_comparison.md     # kimi vs deepseek bake-off
├── latency_report.json
├── latency_report_kimi-k2p6.json
├── latency_report_deepseek-v4p1-flash.json
├── fireworks_models_available.json
└── data/
    ├── financial.sqlite
    ├── database_description/
    ├── dev_questions.json
    ├── dev_questions_with_answers.json
    └── dev_answers_example.json
```

## Design highlights

- Schema-grounded system prompt (PRAGMA + BIRD column CSVs + domain value enums)
- SQL safety gate (SELECT / WITH only)
- Automatic repair retries on SQLite errors
- Conversation history for follow-ups
- Structured `AgentResult` for later tool-calling / telemetry (`llm_ms`, `sql_ms`, `total_ms`)

See [DESIGN.md](DESIGN.md) for trade-offs (architecture, OSS vs proprietary, latency/cost, post-training).

## Customer scenario (context)

This PoC responds to a GitLab request for an agentic BI CLI: natural-language queries from the terminal, open-model economics, and a path to &lt;3s P50 latency. Measured bake-off: `kimi-k2p6` P50 ≈6.1s / P95 ≈54s vs `deepseek-v4p1-flash` P50 ≈0.85s / P95 ≈2.5s (10/10, all under 3s). Full customer email and response: [EMAIL_TO_RAUL.md](EMAIL_TO_RAUL.md).
