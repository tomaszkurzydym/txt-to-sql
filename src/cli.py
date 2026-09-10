"""Interactive Text-to-SQL CLI. Run with: uv run cli (or python -m src.cli)"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _ensure_repo_root_on_path() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(root)


def main() -> None:
    _ensure_repo_root_on_path()
    load_dotenv()

    # Prefer FIREWORKS_API_KEY already in the environment (e.g. sourced .fireworks_env).
    if not os.getenv("FIREWORKS_API_KEY"):
        print(
            "Error: FIREWORKS_API_KEY is not set.\n"
            "Export it, or copy .env.example to .env and fill in your key."
        )
        sys.exit(1)

    from src.agent import TextToSQLAgent
    from src.llm_client import get_model_name
    from src.utils import load_db

    conn = load_db()
    agent = TextToSQLAgent(conn=conn)

    print("=" * 72)
    print("Fireworks Text-to-SQL CLI  (financial.sqlite)")
    print(f"Model: {get_model_name()}")
    print("Type a question, or 'exit' / 'quit' to leave. 'reset' clears history.")
    print("=" * 72)

    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not question:
            continue
        lowered = question.lower()
        if lowered in {"exit", "quit", "q"}:
            print("Bye.")
            break
        if lowered == "reset":
            agent.reset()
            print("Conversation history cleared.")
            continue

        result = agent.ask(question)
        print("\n--- SQL ---")
        print(result.sql or "(none)")
        print("\n--- Result ---")
        if result.error and not result.rows:
            print(f"Error: {result.error}")
        else:
            print(result.summary)
        if result.attempts > 1:
            print(f"\n(attempts: {result.attempts})")


if __name__ == "__main__":
    main()
