"""SQLite helpers: load DB, run queries, introspect schema, load BIRD docs.

These utilities are shared by the agent, schema prompt builder, CLI, and
eval harness. They intentionally stay free of LLM concerns.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

DEFAULT_DB_PATH = "data/financial.sqlite"
DEFAULT_DESCRIPTION_DIR = "data/database_description"
# Soft caps for interactive / eval use (large tables like ``trans``).
DEFAULT_BUSY_TIMEOUT_MS = 5000
DEFAULT_MAX_RESULT_ROWS = 500


def load_db(
    db_path: str = DEFAULT_DB_PATH,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Load the SQLite database and return a connection.

    Enables foreign keys and a busy timeout so long locks fail loudly
    instead of hanging the CLI indefinitely.

    Args:
        db_path: Path to the SQLite database file.
        busy_timeout_ms: ``PRAGMA busy_timeout`` in milliseconds.

    Returns:
        An open ``sqlite3.Connection`` with ``Row`` factory enabled.

    Raises:
        FileNotFoundError: If the database file does not exist.
        sqlite3.Error: If connecting to the database fails.
    """
    db_file = Path(db_path)
    if not db_file.exists():
        raise FileNotFoundError(
            f"Database file not found: {db_path}\n"
            "Please run setup.sh first to download the database."
        )

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        raise sqlite3.Error(f"Error connecting to database: {exc}") from exc


def query_db(
    conn: sqlite3.Connection,
    query: str,
    params: Optional[tuple] = None,
    return_as_df: bool = True,
    max_rows: Optional[int] = DEFAULT_MAX_RESULT_ROWS,
) -> List[Dict[str, Any]] | pd.DataFrame:
    """Execute a SQL query and return results as a DataFrame or list of dicts.

    When ``max_rows`` is set, result sets longer than that limit are truncated
    (DataFrame via ``.head``, list via slicing) so a runaway ``SELECT *``
    cannot flood memory in the interactive CLI.

    Args:
        conn: Active SQLite database connection.
        query: SQL query string to execute.
        params: Optional tuple of parameters for parameterized queries.
        return_as_df: If True, return a pandas DataFrame; else list of dicts.
        max_rows: Optional row cap after fetch. ``None`` disables capping.

    Returns:
        Query results as ``pd.DataFrame`` or ``List[Dict[str, Any]]``.

    Raises:
        sqlite3.Error: If executing the query fails.
    """
    try:
        if return_as_df:
            df = pd.read_sql_query(query, conn, params=params)
            if max_rows is not None and len(df) > max_rows:
                return df.head(max_rows).copy()
            return df

        cursor = conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        columns = [description[0] for description in cursor.description]
        results: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            results.append(dict(zip(columns, row)))
            if max_rows is not None and len(results) >= max_rows:
                break
        return results
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise sqlite3.Error(f"Error executing query: {exc}") from exc


def get_schema(conn: sqlite3.Connection) -> Dict[str, List[Dict[str, str]]]:
    """Get the database schema including all tables and their columns.

    Args:
        conn: Active SQLite database connection.

    Returns:
        Dict mapping table names to column info dicts from
        ``PRAGMA table_info``.
    """
    schema: Dict[str, List[Dict[str, str]]] = {}

    tables = query_db(
        conn,
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
        return_as_df=False,
        max_rows=None,
    )

    for table in tables:
        table_name = table["name"]
        columns = query_db(
            conn,
            f'PRAGMA table_info("{table_name}")',
            return_as_df=False,
            max_rows=None,
        )
        schema[table_name] = columns

    return schema


def load_column_descriptions(
    description_dir: str = DEFAULT_DESCRIPTION_DIR,
) -> Dict[str, List[Dict[str, str]]]:
    """Load BIRD column documentation CSVs shipped with the dataset.

    Args:
        description_dir: Directory containing per-table ``*.csv`` files.

    Returns:
        Dict mapping table name (from filename stem) to rows of column
        metadata.

    Raises:
        FileNotFoundError: If ``description_dir`` is missing.
    """
    root = Path(description_dir)
    if not root.is_dir():
        raise FileNotFoundError(
            f"Column description directory not found: {description_dir}\n"
            "Please run setup.sh first."
        )

    descriptions: Dict[str, List[Dict[str, str]]] = {}
    for csv_path in sorted(root.glob("*.csv")):
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            descriptions[csv_path.stem] = [dict(row) for row in reader]
    return descriptions


def get_rag_corpus(
    description_dir: str = DEFAULT_DESCRIPTION_DIR,
) -> List[Dict[str, str]]:
    """Return a flat list of column documentation records for indexing/search.

    Each record includes table, original_column_name, column_name,
    column_description, data_format, and value_description fields when
    present in the CSV. Useful as a future RAG corpus; unused by the PoC
    prompt path.

    Args:
        description_dir: Directory containing per-table ``*.csv`` files.

    Returns:
        Flat list of column documentation dicts.
    """
    corpus: List[Dict[str, str]] = []
    for table_name, rows in load_column_descriptions(description_dir).items():
        for row in rows:
            corpus.append(
                {
                    "table": table_name,
                    "original_column_name": row.get("original_column_name", ""),
                    "column_name": row.get("column_name", ""),
                    "column_description": row.get("column_description", ""),
                    "data_format": row.get("data_format", ""),
                    "value_description": row.get("value_description", ""),
                }
            )
    return corpus


def print_table_schema(
    conn: sqlite3.Connection,
    table_name: Optional[str] = None,
) -> None:
    """Print a formatted view of the database schema.

    Args:
        conn: Active SQLite database connection.
        table_name: Optional specific table to display. If None, shows all.
    """
    schema = get_schema(conn)

    if table_name:
        if table_name not in schema:
            print(f"Error: Table '{table_name}' not found in database.")
            print(f"Available tables: {', '.join(schema.keys())}")
            return
        tables_to_print = {table_name: schema[table_name]}
    else:
        tables_to_print = schema

    print("\n" + "=" * 100)
    print(f"DATABASE SCHEMA - {len(schema)} tables")
    print("=" * 100)

    if not table_name:
        print("\nTables:")
        for i, tbl in enumerate(schema.keys(), 1):
            print(f"  {i}. {tbl}")
        print()

    for tbl_name, columns in tables_to_print.items():
        print("\n" + "-" * 100)
        print(f"Table: {tbl_name} ({len(columns)} columns)")
        print("-" * 100)
        print(
            f"{'Column':<30} {'Type':<20} {'Nullable':<12} "
            f"{'PK':<5} {'Default':<15}"
        )
        print("-" * 100)

        for col in columns:
            nullable = "NULL" if col["notnull"] == 0 else "NOT NULL"
            pk = "Y" if col["pk"] > 0 else ""
            default = (
                str(col["dflt_value"]) if col["dflt_value"] is not None else ""
            )
            print(
                f"{col['name']:<30} {col['type']:<20} {nullable:<12} "
                f"{pk:<5} {default:<15}"
            )

    print("=" * 100 + "\n")
