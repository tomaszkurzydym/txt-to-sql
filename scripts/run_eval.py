#!/usr/bin/env python3
"""Run the agent on all 10 dev questions and write dev_answers.json."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def _normalize_value(v: Any) -> Any:
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        return round(v, 2)
    return v


def _normalize_rows(rows: Optional[List[Dict[str, Any]]]) -> List[List[Any]]:
    """Compare results as sorted bags of row value tuples (ignore column names/order)."""
    if not rows:
        return []
    normalized: List[List[Any]] = []
    for row in rows:
        vals = sorted((_normalize_value(v) for v in row.values()), key=lambda x: str(x))
        normalized.append(vals)
    normalized.sort(key=lambda r: str(r))
    return normalized


def results_match(
    got: Optional[List[Dict[str, Any]]],
    expected: List[Dict[str, Any]],
) -> bool:
    return _normalize_rows(got) == _normalize_rows(expected)


def main() -> None:
    load_dotenv()
    if not os.getenv("FIREWORKS_API_KEY"):
        print("FIREWORKS_API_KEY is not set", file=sys.stderr)
        sys.exit(1)

    from src.agent import TextToSQLAgent
    from src.llm_client import get_model_name
    from src.utils import load_db

    questions_path = ROOT / "data" / "dev_questions.json"
    gold_path = ROOT / "data" / "dev_questions_with_answers.json"
    out_path = ROOT / "dev_answers.json"

    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    gold_by_id: Dict[str, Dict[str, Any]] = {}
    if gold_path.exists():
        for item in json.loads(gold_path.read_text(encoding="utf-8")):
            gold_by_id[item["id"]] = item

    print(f"Model: {get_model_name()}")
    print(f"Running {len(questions)} questions...\n")

    conn = load_db()
    # Fresh agent per question so eval turns are independent (no cross-talk).
    answers: Dict[str, Dict[str, str]] = {}
    passed = 0
    failed = 0
    details: List[str] = []

    for q in questions:
        qid = q["id"]
        question = q["question"]
        print(f"[{qid}] {question}")
        agent = TextToSQLAgent(conn=conn, keep_history=False)
        result = agent.ask(question)
        payload = result.to_answer_payload()
        answers[qid] = payload

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
        details.append(f"{qid}: {status}")
        print(f"  SQL: {result.sql}")
        print(f"  Answer: {payload['answer'][:200]}")
        if result.error:
            print(f"  Error: {result.error}")
        print(f"  -> {status}\n")

    out_path.write_text(json.dumps(answers, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("=" * 60)
    print(f"Wrote {out_path}")
    print(f"Pass: {passed}  Fail: {failed}  Total scored: {passed + failed}")
    for line in details:
        print(f"  {line}")


if __name__ == "__main__":
    main()
