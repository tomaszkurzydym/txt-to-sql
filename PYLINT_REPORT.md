# Pylint Report

## Command

```bash
cd /workspace/homework-assignment
.venv/bin/python -m pylint src scripts
```

Config lives in `pyproject.toml` under `[tool.pylint.*]` (Google-oriented):

- **Kept / emphasized:** missing module/class/function docstrings, unused imports,
  undefined names, bare/broad except (with explicit disables only where the
  LLM/network or pandas surface requires it), dangerous defaults, line length 100.
- **Disabled as take-home noise:** `too-many-*`, `too-few-public-methods`,
  `too-many-positional-arguments`, `duplicate-code`, `import-outside-toplevel`
  (deferred imports after dotenv are intentional in CLI/eval).

## Scores

| Run | Score | Notes |
|-----|-------|-------|
| Before (no project config, pre-docstring pass) | **9.34/10** | Missing docstrings, unused `field` import, broad `Exception`, line length, unnecessary lambdas, `no-else-return` |
| After (with `[tool.pylint]` + fixes) | **10.00/10** | Clean under project config |

Final run line:

```
Your code has been rated at 10.00/10 (previous run: 9.96/10, +0.04)
```

## What we fixed

- Added Google-style docstrings on every module, class, and public/private function.
- Removed unused `dataclasses.field` import.
- Narrowed `format_result_summary` int conversion except to `TypeError`/`ValueError`.
- Prefer `sqlite3.Error` on SQL failure; remaining broad catches on LLM/pandas are
  intentional and marked `# pylint: disable=broad-exception-caught`.
- `query_db`: removed unnecessary `else` after `return`.
- `run_eval`: replaced unnecessary lambdas with named sort-key helpers.
- Wrapped / shortened lines over 100 characters.
- Added `max_tokens` floor + documented kimi `reasoning_content` fallback (behavior kept).

## Remaining warnings / disables — rationale

| Item | Status | Rationale |
|------|--------|-----------|
| `import-outside-toplevel` in CLI/eval | Disabled globally | Imports run **after** `load_dotenv()` / API-key check so config is present before client construction |
| `too-many-*` / `too-few-public-methods` | Disabled | Structural noise for a small PoC; does not indicate bugs |
| `broad-exception-caught` on LLM call | Explicit inline disable | OpenAI/Fireworks SDK can raise many transport/API types; surfaced as `LLM error: …` |
| `broad-exception-caught` on execute fallback | Explicit inline disable | Defense in depth under `sqlite3.Error` for unexpected pandas errors |
| No remaining scored warnings | — | Clean `10.00/10` under the project config |

## P0 hardening applied (from review notes)

Low-risk changes landed alongside docstring work:

1. Repair feedback includes **previous SQL + error**.
2. `PRAGMA busy_timeout` on connect; **row cap** in `query_db` (default 500).
3. Conversation **history cap** (default 6 messages).
4. Safety gate strips SQL comments; WITH must contain SELECT.

No secrets committed; `.env` was not modified.
