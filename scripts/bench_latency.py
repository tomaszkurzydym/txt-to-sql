#!/usr/bin/env python3
"""Benchmark P50/P95 latency on the 10 development questions.

Times come from ``AgentResult.llm_ms`` / ``sql_ms`` / ``total_ms``. LLM time is
**full completion wall time** (the chat-completion round-trip), not TTFT.

Example::

    export FIREWORKS_API_KEY=fw_...
    python scripts/bench_latency.py --out latency_report.json

    FIREWORKS_MODEL=accounts/fireworks/models/deepseek-v4p1-flash \\
      python scripts/bench_latency.py --out latency_report_deepseek-v4p1-flash.json

    python scripts/bench_latency.py --list-models --out fireworks_models_available.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.chdir(ROOT)

SLA_MS = 3000.0


def percentile(values: Sequence[float], p: float) -> float:
    """Return the linear-interpolated percentile of ``values``.

    Args:
        values: Sample (need not be sorted).
        p: Percentile in ``[0, 100]``.

    Returns:
        Interpolated percentile. ``0.0`` if ``values`` is empty.

    Raises:
        ValueError: If ``p`` is outside ``[0, 100]``.
    """
    if p < 0.0 or p > 100.0:
        raise ValueError(f"percentile p must be in [0, 100], got {p}")
    if not values:
        return 0.0
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * (p / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return xs[int(rank)]
    weight = rank - lo
    return xs[lo] * (1.0 - weight) + xs[hi] * weight


def _stats(values: Sequence[float]) -> Dict[str, float]:
    """Build a P50/P95/min/max/mean summary for a numeric sample.

    Args:
        values: Sample of milliseconds.

    Returns:
        Dict with ``n``, ``p50``, ``p95``, ``min``, ``max``, ``mean``.
    """
    xs = [float(v) for v in values]
    n = len(xs)
    mean = sum(xs) / n if n else 0.0
    return {
        "n": n,
        "p50": round(percentile(xs, 50), 1),
        "p95": round(percentile(xs, 95), 1),
        "min": round(min(xs), 1) if xs else 0.0,
        "max": round(max(xs), 1) if xs else 0.0,
        "mean": round(mean, 1),
    }


def list_fireworks_models(out_path: Path) -> None:
    """Fetch ``GET /models`` from Fireworks and write the JSON listing.

    Args:
        out_path: Destination path for the models JSON.

    Raises:
        SystemExit: If ``FIREWORKS_API_KEY`` is missing (exit code 1).
    """
    from src.llm_client import create_client

    client = create_client()
    page = client.models.list()
    models: List[Dict[str, Any]] = []
    for item in page:
        dump = item.model_dump() if hasattr(item, "model_dump") else {"id": str(item)}
        models.append(dump)
    payload = {
        "object": "list",
        "endpoint": "https://api.fireworks.ai/inference/v1/models",
        "count": len(models),
        "data": models,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out_path} ({len(models)} models)")


def run_bench(out_path: Path) -> Dict[str, Any]:
    """Run the 10-question latency bench and write a JSON report.

    Args:
        out_path: Destination path for the report JSON.

    Returns:
        The report dict that was written.

    Raises:
        SystemExit: If ``FIREWORKS_API_KEY`` is missing (exit code 1).
    """
    from run_eval import results_match
    from src.agent import TextToSQLAgent
    from src.llm_client import FireworksLLM, get_model_name
    from src.utils import load_db

    questions_path = ROOT / "data" / "dev_questions.json"
    gold_path = ROOT / "data" / "dev_questions_with_answers.json"

    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    gold_by_id: Dict[str, Dict[str, Any]] = {}
    if gold_path.exists():
        for item in json.loads(gold_path.read_text(encoding="utf-8")):
            gold_by_id[item["id"]] = item

    model = get_model_name()
    print(f"Model: {model}")
    print(f"Running {len(questions)} questions (latency = full LLM wall time, not TTFT)\n")

    conn = load_db()
    llm = FireworksLLM(model=model)
    per_question: List[Dict[str, Any]] = []
    passed = 0
    failed = 0

    for q in questions:
        qid = q["id"]
        question = q["question"]
        print(f"[{qid}] {question}")
        agent = TextToSQLAgent(conn=conn, llm=llm, keep_history=False)
        result = agent.ask(question)

        gold = gold_by_id.get(qid)
        status = "SKIP (no gold)"
        if gold and "expected_result" in gold:
            ok = results_match(result.rows, gold["expected_result"]) and not result.error
            if ok:
                status = "PASS"
                passed += 1
            else:
                status = "FAIL"
                failed += 1

        row = {
            "id": qid,
            "tier": q.get("tier"),
            "status": status,
            "attempts": result.attempts,
            "llm_ms": round(result.llm_ms, 1),
            "sql_ms": round(result.sql_ms, 1),
            "total_ms": round(result.total_ms, 1),
            "error": result.error,
            "sql": result.sql,
        }
        per_question.append(row)
        print(
            f"  -> {status}  "
            f"LLM {row['llm_ms']:.0f}ms | SQL {row['sql_ms']:.0f}ms | "
            f"total {row['total_ms']:.0f}ms"
        )
        if result.error:
            print(f"  Error: {result.error}")
        print()

    totals = [r["total_ms"] for r in per_question]
    llms = [r["llm_ms"] for r in per_question]
    sqls = [r["sql_ms"] for r in per_question]
    all_under_sla = bool(totals) and all(t < SLA_MS for t in totals)

    report: Dict[str, Any] = {
        "model": model,
        "timing_definition": (
            "llm_ms is full LLM wall time (chat completion round-trip), not TTFT. "
            "sql_ms is SQLite execute only. total_ms is end-to-end AgentResult.ask()."
        ),
        "n": len(per_question),
        "passed": passed,
        "failed": failed,
        "all_under_3s": all_under_sla,
        "latency_ms": {
            "total": _stats(totals),
            "llm": _stats(llms),
            "sql": _stats(sqls),
        },
        "per_question": per_question,
    }
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    tstats = report["latency_ms"]["total"]
    sstats = report["latency_ms"]["sql"]
    print("=" * 60)
    print(f"Wrote {out_path}")
    print(f"Pass: {passed}  Fail: {failed}  n={len(per_question)}")
    print(f"total P50={tstats['p50']:.1f}ms  P95={tstats['p95']:.1f}ms")
    print(f"sql   P50={sstats['p50']:.1f}ms  P95={sstats['p95']:.1f}ms")
    print(f"all under {SLA_MS:.0f}ms: {all_under_sla}")
    return report


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments for the latency bench.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed ``argparse.Namespace``.
    """
    parser = argparse.ArgumentParser(
        description="P50/P95 latency bench on the 10 development questions.",
    )
    parser.add_argument(
        "--out",
        default="latency_report.json",
        help="Output JSON path (default: latency_report.json)",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Write the Fireworks /models listing instead of running the bench.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point: latency bench or Fireworks model listing.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Raises:
        SystemExit: If ``FIREWORKS_API_KEY`` is missing (exit code 1).
    """
    load_dotenv()
    args = parse_args(argv)
    if not os.getenv("FIREWORKS_API_KEY"):
        print("FIREWORKS_API_KEY is not set", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)
    if args.list_models:
        list_fireworks_models(out_path)
        return
    run_bench(out_path)


if __name__ == "__main__":
    main()
