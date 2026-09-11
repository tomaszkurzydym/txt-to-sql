"""Text-to-SQL agent with schema grounding, safety checks, and SQL-error retries.

The agent turns a natural-language question into a single read-only SQLite
SELECT (or WITH … SELECT), executes it, and retries with error + failed-SQL
feedback when generation or execution fails.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
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
# Strip SQL line/block comments before safety checks.
COMMENT_RE = re.compile(r"(--[^\n]*|/\*[\s\S]*?\*/)")
# Cap conversation history (user+assistant pairs) to control token growth.
DEFAULT_HISTORY_MAX_MESSAGES = 6


@dataclass
class AgentResult:
    """Structured response from a single agent turn — easy to extend.

    Attributes:
        question: Original user question.
        sql: Final SQL attempted (may be None on LLM failure).
        rows: Result rows as list of dicts, if execution succeeded.
        summary: Human-readable result summary for CLI / eval answers.
        error: Last error string, if any.
        attempts: Number of generation/execution attempts used.
        messages_used: Length of the chat message list on the last attempt.
    """

    question: str
    sql: Optional[str] = None
    rows: Optional[List[Dict[str, Any]]] = None
    summary: str = ""
    error: Optional[str] = None
    attempts: int = 0
    messages_used: int = 0

    def to_answer_payload(self) -> Dict[str, str]:
        """Convert this result into the eval ``{sql, answer}`` payload shape.

        Returns:
            Dict with ``sql`` and ``answer`` string fields.
        """
        answer = self.summary
        if self.error and not answer:
            answer = f"Error: {self.error}"
        return {"sql": self.sql or "", "answer": answer}


SYSTEM_PROMPT_TEMPLATE = """\
You are an expert SQLite text-to-SQL assistant for a Czech retail banking database.

Your job:
1. Convert the user's natural-language question into a SINGLE SQLite SELECT query.
2. Use ONLY tables/columns from the schema below. Do not invent names.
3. Prefer exact domain values from the schema notes (loan status, OWNER vs DISPONENT, regions, gender).
4. Return ONLY the SQL query — no commentary, no markdown fences, no explanation.
5. If the user asks a follow-up, reuse conversation context and prior SQL/results as needed.

{schema}
"""


def strip_sql_fences(text: str) -> str:
    """Remove markdown code fences and trailing commentary from model output.

    Args:
        text: Raw model output that may contain fenced SQL or prose.

    Returns:
        A single SQL statement ending with a semicolon.
    """
    text = text.strip()
    match = FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()
    # Drop leading "SQL:" labels.
    text = re.sub(r"^(?:sql\s*:)\s*", "", text, flags=re.IGNORECASE).strip()
    # Keep only up to the first terminating semicolon if extra prose follows.
    if ";" in text:
        first, rest = text.split(";", 1)
        if rest.strip() and not rest.strip().lower().startswith(("with", "select")):
            text = first + ";"
    return text.strip().rstrip(";").strip() + ";"


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` and ``/* */`` comments for safer pattern checks.

    Args:
        sql: SQL string that may contain comments.

    Returns:
        SQL with comments replaced by spaces.
    """
    return COMMENT_RE.sub(" ", sql)


def is_safe_select(sql: str) -> tuple[bool, str]:
    """Reject non-SELECT / multi-statement / dangerous SQL.

    Strips comments first, blocks DML/DDL keywords, allows only a single
    statement that starts with SELECT or WITH, and requires that WITH
    queries still contain a SELECT.

    Args:
        sql: Candidate SQL string (usually already fence-stripped).

    Returns:
        Tuple ``(ok, reason)`` where ``reason`` is empty on success.
    """
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        return False, "Empty SQL"

    no_comments = _strip_sql_comments(cleaned)
    # Disallow multiple statements (ignore semicolons inside string literals).
    without_strings = re.sub(r"'([^']|'')*'", "''", no_comments)
    if ";" in without_strings:
        return False, "Multiple SQL statements are not allowed"
    if DANGEROUS_SQL_RE.search(no_comments):
        return False, "Only read-only SELECT queries are allowed"

    normalized = no_comments.lstrip().lower()
    if normalized.startswith("select"):
        return True, ""
    if normalized.startswith("with"):
        # WITH … must still include a SELECT (not just a CTE header).
        if not re.search(r"\bselect\b", normalized):
            return False, "WITH queries must include a SELECT"
        return True, ""
    return False, "Only SELECT (or WITH ... SELECT) queries are allowed"


def format_result_summary(df: pd.DataFrame, max_rows: int = 20) -> str:
    """Build a human-readable summary of query results for CLI / eval answers.

    Args:
        df: Query result DataFrame (may be empty).
        max_rows: Max rows to include in the tabular preview.

    Returns:
        Multi-line summary string.
    """
    if df is None or df.empty:
        return "No rows returned."
    n = len(df)
    # Scalar-ish single-cell answers.
    if n == 1 and df.shape[1] == 1:
        val = df.iloc[0, 0]
        col = df.columns[0]
        if isinstance(val, float):
            return f"{col}: {val:,.2f}" if abs(val) >= 100 else f"{col}: {val}"
        if isinstance(val, int) or (hasattr(val, "item") and not isinstance(val, str)):
            try:
                ival = int(val)
                return f"{col}: {ival:,}"
            except (TypeError, ValueError):
                pass
        return f"{col}: {val}"

    preview = df.head(max_rows)
    lines = [f"{n} row(s), {df.shape[1]} column(s):"]
    lines.append(preview.to_string(index=False))
    if n > max_rows:
        lines.append(f"... ({n - max_rows} more rows omitted)")
    return "\n".join(lines)


class TextToSQLAgent:
    """Schema-grounded text-to-SQL agent with safety gate and repair loop.

    Extension points:
    - swap/inject ``llm`` (tool-calling models, routers)
    - override ``build_messages`` / ``generate_sql``
    - hook on SQL errors for richer repair loops

    Attributes:
        conn: SQLite connection used for execution and schema build.
        llm: Fireworks (or compatible) chat client.
        max_retries: Extra attempts after the first on SQL/safety failure.
        schema_text: Prompt-ready schema string.
        keep_history: Whether to append successful turns to ``history``.
        history: Rolling chat turns (capped by ``history_max_messages``).
        history_max_messages: Max stored history messages (user+assistant).
    """

    def __init__(
        self,
        conn: Optional[sqlite3.Connection] = None,
        llm: Optional[FireworksLLM] = None,
        max_retries: int = 2,
        schema_text: Optional[str] = None,
        keep_history: bool = True,
        history_max_messages: int = DEFAULT_HISTORY_MAX_MESSAGES,
    ) -> None:
        """Create an agent bound to a DB connection and LLM client.

        Args:
            conn: Optional SQLite connection; loads default DB if omitted.
            llm: Optional FireworksLLM; creates a default client if omitted.
            max_retries: Number of repair retries after the first attempt.
            schema_text: Optional prebuilt schema prompt; built if omitted.
            keep_history: If True, retain successful Q/A turns for follow-ups.
            history_max_messages: Cap on stored history messages (even count
                preferred so user/assistant pairs stay intact).
        """
        self.conn = conn or load_db()
        self.llm = llm or FireworksLLM()
        self.max_retries = max_retries
        self.schema_text = schema_text or build_schema_context(self.conn)
        self.keep_history = keep_history
        self.history_max_messages = max(0, history_max_messages)
        self.history: List[Dict[str, str]] = []
        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=self.schema_text)

    def reset(self) -> None:
        """Clear conversation history used for follow-up questions."""
        self.history.clear()

    def _trim_history(self) -> None:
        """Drop oldest history messages so length stays within the cap."""
        cap = self.history_max_messages
        if cap and len(self.history) > cap:
            self.history[:] = self.history[-cap:]

    def build_messages(
        self,
        question: str,
        repair_feedback: Optional[str] = None,
        failed_sql: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Build the chat message list for one generation attempt.

        On repair turns, includes both the failed SQL and the error so the
        model can fix concrete mistakes (wrong column, syntax, etc.).

        Args:
            question: User natural-language question.
            repair_feedback: Optional error / safety rejection reason.
            failed_sql: Optional SQL that failed on the previous attempt.

        Returns:
            OpenAI-style list of ``{role, content}`` messages.
        """
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._system_prompt}
        ]
        if self.keep_history:
            messages.extend(self.history)
        user_content = question
        if repair_feedback:
            prev = failed_sql or "(none)"
            user_content = (
                f"{question}\n\n"
                f"Previous SQL:\n{prev}\n\n"
                f"It failed with:\n{repair_feedback}\n\n"
                "Return ONLY the corrected SQLite SELECT."
            )
        messages.append({"role": "user", "content": user_content})
        return messages

    def generate_sql(self, messages: List[Dict[str, str]]) -> str:
        """Call the LLM and normalize the response into a SQL string.

        Args:
            messages: Chat messages including system schema and user turn.

        Returns:
            Fence-stripped SQL ending with a semicolon.
        """
        raw = self.llm.chat(messages)
        return strip_sql_fences(raw)

    def execute_sql(self, sql: str) -> pd.DataFrame:
        """Execute a validated SELECT against the agent connection.

        Args:
            sql: Read-only SQL to run.

        Returns:
            Query results as a pandas DataFrame (row-capped by ``query_db``).
        """
        return query_db(self.conn, sql, return_as_df=True)

    def ask(self, question: str) -> AgentResult:
        """Generate SQL for a question, execute it, and retry on SQL errors.

        Args:
            question: Natural-language question about the financial DB.

        Returns:
            ``AgentResult`` with SQL, rows/summary on success, or error after
            exhausting retries.
        """
        result = AgentResult(question=question)
        repair_feedback: Optional[str] = None
        last_sql: Optional[str] = None

        for attempt in range(self.max_retries + 1):
            result.attempts = attempt + 1
            messages = self.build_messages(
                question,
                repair_feedback=repair_feedback,
                failed_sql=last_sql,
            )
            result.messages_used = len(messages)
            try:
                sql = self.generate_sql(messages)
            except Exception as exc:  # pylint: disable=broad-exception-caught
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
                # Normalize numpy/pandas scalars for JSON.
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
                                f"SQL:\n{sql}\n\n"
                                f"Result summary:\n{result.summary}"
                            ),
                        }
                    )
                    self._trim_history()
                return result
            except sqlite3.Error as exc:
                repair_feedback = str(exc)
                result.error = repair_feedback
            except Exception as exc:  # pylint: disable=broad-exception-caught
                repair_feedback = str(exc)
                result.error = repair_feedback

        result.sql = last_sql
        if not result.summary:
            result.summary = (
                f"Failed after {result.attempts} attempt(s): {result.error}"
            )
        return result
