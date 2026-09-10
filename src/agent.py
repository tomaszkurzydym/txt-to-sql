"""Text-to-SQL agent with schema grounding, safety checks, and SQL-error retries."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from src.llm_client import FireworksLLM
from src.schema_context import build_schema_context
from src.utils import load_db, query_db

DANGEROUS_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|"
    r"DETACH|PRAGMA|VACUUM|REINDEX|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
FENCE_RE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.IGNORECASE)


@dataclass
class AgentResult:
    """Structured response from a single agent turn — easy to extend."""

    question: str
    sql: Optional[str] = None
    rows: Optional[List[Dict[str, Any]]] = None
    summary: str = ""
    error: Optional[str] = None
    attempts: int = 0
    messages_used: int = 0

    def to_answer_payload(self) -> Dict[str, str]:
        answer = self.summary
        if self.error and not answer:
            answer = f"Error: {self.error}"
        return {"sql": self.sql or "", "answer": answer}


SYSTEM_PROMPT_TEMPLATE = """You are an expert SQLite text-to-SQL assistant for a Czech retail banking database.

Your job:
1. Convert the user's natural-language question into a SINGLE SQLite SELECT query.
2. Use ONLY tables/columns from the schema below. Do not invent names.
3. Prefer exact domain values from the schema notes (loan status letters, OWNER vs DISPONENT, region names, gender codes).
4. Return ONLY the SQL query — no commentary, no markdown fences, no explanation.
5. If the user asks a follow-up, reuse conversation context and prior SQL/results as needed.

{schema}
"""


def strip_sql_fences(text: str) -> str:
    """Remove markdown code fences and trailing commentary from model output."""
    text = text.strip()
    match = FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()
    # Drop leading "SQL:" labels
    text = re.sub(r"^(?:sql\s*:)\s*", "", text, flags=re.IGNORECASE).strip()
    # Keep only up to the first terminating semicolon if extra prose follows
    if ";" in text:
        first, rest = text.split(";", 1)
        # If rest looks like prose, drop it
        if rest.strip() and not rest.strip().lower().startswith(("with", "select")):
            text = first + ";"
    return text.strip().rstrip(";").strip() + ";"


def is_safe_select(sql: str) -> tuple[bool, str]:
    """Reject non-SELECT / multi-statement / dangerous SQL."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        return False, "Empty SQL"
    # Disallow multiple statements
    without_strings = re.sub(r"'([^']|'')*'", "''", cleaned)
    if ";" in without_strings:
        return False, "Multiple SQL statements are not allowed"
    if DANGEROUS_SQL_RE.search(cleaned):
        return False, "Only read-only SELECT queries are allowed"
    # Allow WITH ... SELECT and plain SELECT
    normalized = cleaned.lstrip().lower()
    if not (normalized.startswith("select") or normalized.startswith("with")):
        return False, "Only SELECT (or WITH ... SELECT) queries are allowed"
    return True, ""


def format_result_summary(df: pd.DataFrame, max_rows: int = 20) -> str:
    """Human-readable summary of query results for CLI / eval answers."""
    if df is None or df.empty:
        return "No rows returned."
    n = len(df)
    # Scalar-ish single-cell answers
    if n == 1 and df.shape[1] == 1:
        val = df.iloc[0, 0]
        col = df.columns[0]
        if isinstance(val, float):
            return f"{col}: {val:,.2f}" if abs(val) >= 100 else f"{col}: {val}"
        if isinstance(val, (int,)) or (hasattr(val, "item") and not isinstance(val, str)):
            try:
                ival = int(val)
                return f"{col}: {ival:,}"
            except Exception:
                pass
        return f"{col}: {val}"

    preview = df.head(max_rows)
    lines = [f"{n} row(s), {df.shape[1]} column(s):"]
    lines.append(preview.to_string(index=False))
    if n > max_rows:
        lines.append(f"... ({n - max_rows} more rows omitted)")
    return "\n".join(lines)


class TextToSQLAgent:
    """
    Schema-grounded text-to-SQL agent.

    Extension points:
    - swap/inject llm (tool-calling models, routers)
    - override build_messages / generate_sql
    - hook on_sql_error for richer repair loops
    """

    def __init__(
        self,
        conn: Optional[sqlite3.Connection] = None,
        llm: Optional[FireworksLLM] = None,
        max_retries: int = 2,
        schema_text: Optional[str] = None,
        keep_history: bool = True,
    ) -> None:
        self.conn = conn or load_db()
        self.llm = llm or FireworksLLM()
        self.max_retries = max_retries
        self.schema_text = schema_text or build_schema_context(self.conn)
        self.keep_history = keep_history
        self.history: List[Dict[str, str]] = []
        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=self.schema_text)

    def reset(self) -> None:
        self.history.clear()

    def build_messages(
        self,
        question: str,
        repair_feedback: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": self._system_prompt}]
        if self.keep_history:
            messages.extend(self.history)
        user_content = question
        if repair_feedback:
            user_content = (
                f"{question}\n\n"
                f"The previous SQL failed with this error. Fix the SQL and return ONLY the corrected query.\n"
                f"Error: {repair_feedback}"
            )
        messages.append({"role": "user", "content": user_content})
        return messages

    def generate_sql(self, messages: List[Dict[str, str]]) -> str:
        raw = self.llm.chat(messages)
        return strip_sql_fences(raw)

    def execute_sql(self, sql: str) -> pd.DataFrame:
        return query_db(self.conn, sql, return_as_df=True)

    def ask(self, question: str) -> AgentResult:
        """Generate SQL for a question, execute it, retry on SQL errors."""
        result = AgentResult(question=question)
        repair_feedback: Optional[str] = None
        last_sql: Optional[str] = None

        for attempt in range(self.max_retries + 1):
            result.attempts = attempt + 1
            messages = self.build_messages(question, repair_feedback=repair_feedback)
            result.messages_used = len(messages)
            try:
                sql = self.generate_sql(messages)
            except Exception as exc:  # network / API
                result.error = f"LLM error: {exc}"
                return result

            last_sql = sql
            result.sql = sql
            ok, reason = is_safe_select(sql)
            if not ok:
                repair_feedback = reason
                result.error = reason
                continue

            try:
                df = self.execute_sql(sql)
                rows = df.to_dict(orient="records")
                # Normalize numpy/pandas scalars for JSON
                clean_rows: List[Dict[str, Any]] = []
                for row in rows:
                    clean_rows.append(
                        {
                            k: (v.item() if hasattr(v, "item") else v)
                            for k, v in row.items()
                        }
                    )
                result.rows = clean_rows
                result.summary = format_result_summary(df)
                result.error = None

                if self.keep_history:
                    self.history.append({"role": "user", "content": question})
                    self.history.append(
                        {
                            "role": "assistant",
                            "content": (
                                f"SQL:\n{sql}\n\nResult summary:\n{result.summary}"
                            ),
                        }
                    )
                return result
            except Exception as exc:
                repair_feedback = str(exc)
                result.error = repair_feedback

        result.sql = last_sql
        if not result.summary:
            result.summary = f"Failed after {result.attempts} attempt(s): {result.error}"
        return result
