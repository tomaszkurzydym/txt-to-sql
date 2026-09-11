"""Build prompt-ready schema context from SQLite + BIRD column docs.

Combines live ``PRAGMA table_info`` with BIRD CSV descriptions and a short
domain-hint block (loan status letters, OWNER/DISPONENT, regions, etc.) so
the LLM does not invent column names or enum values.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from src.utils import get_schema, load_column_descriptions

# High-signal enum / domain notes that models frequently miss.
DOMAIN_HINTS = """
Domain value reference (use exact stored values):
- loan.status: 'A' = contract finished, no problems; 'B' = contract finished, loan not paid;
  'C' = running contract, OK so far; 'D' = running contract, client in debt.
- disp.type: 'OWNER' = account owner; 'DISPONENT' = authorized user (not owner).
  Only OWNER can issue permanent orders or apply for loans.
- client.gender: 'F' = female, 'M' = male.
- district.A2 = district name; district.A3 = region name.
  Known regions: Prague, central Bohemia, south Bohemia, west Bohemia,
  north Bohemia, east Bohemia, south Moravia, north Moravia.
- district.A11 = average salary.
- Dates in this SQLite DB are ISO strings (YYYY-MM-DD). Prefer strftime('%Y', date) for years.
- The table name "order" is a SQL reserved word — always quote it as "order".
- Join keys: account.district_id -> district.district_id; client.district_id -> district.district_id;
  disp links client <-> account; loan.account_id -> account.account_id;
  card.disp_id -> disp.disp_id; trans.account_id -> account.account_id;
  order.account_id -> account.account_id.
""".strip()


def _row_desc(row: Dict[str, str]) -> str:
    """Format one BIRD column-documentation row for the prompt.

    Args:
        row: Dict from a BIRD description CSV (column names vary slightly).

    Returns:
        Indented bullet string describing the column.
    """
    parts: List[str] = []
    col = row.get("original_column_name") or row.get("column_name") or ""
    nice = (row.get("column_name") or "").strip()
    desc = (row.get("column_description") or "").strip()
    fmt = (row.get("data_format") or "").strip()
    values = (row.get("value_description") or "").strip().replace("\n", " ")
    if nice and nice != col:
        parts.append(f"aka {nice}")
    if desc:
        parts.append(desc)
    if fmt:
        parts.append(f"format={fmt}")
    if values:
        parts.append(f"values: {values}")
    detail = "; ".join(parts)
    return f"  - {col}: {detail}" if detail else f"  - {col}"


def build_schema_context(
    conn: sqlite3.Connection,
    description_dir: Optional[str] = None,
) -> str:
    """Return a compact, prompt-ready schema string.

    Combines PRAGMA schema and BIRD column documentation plus domain hints.

    Args:
        conn: Open SQLite connection to ``financial.sqlite``.
        description_dir: Optional override for BIRD CSV directory. When None,
            uses the default under ``data/database_description``.

    Returns:
        Multi-section schema text suitable for the agent system prompt.
    """
    schema = get_schema(conn)
    descriptions = (
        load_column_descriptions(description_dir)
        if description_dir is not None
        else load_column_descriptions()
    )

    sections: List[str] = [
        "SQLite database schema for Czech retail banking (financial.sqlite).",
        "Generate ONLY valid SQLite SELECT statements using these tables/columns.",
        "",
        DOMAIN_HINTS,
        "",
        "Tables and columns:",
    ]

    for table_name in sorted(schema.keys()):
        columns = schema[table_name]
        sections.append(f"\nTABLE {table_name} (columns from PRAGMA):")
        for col in columns:
            nullable = "NULL" if col["notnull"] == 0 else "NOT NULL"
            pk = " PK" if col["pk"] else ""
            sections.append(f"  - {col['name']} {col['type']} {nullable}{pk}")

        docs = descriptions.get(table_name, [])
        if docs:
            sections.append(f"  Documentation for {table_name}:")
            for row in docs:
                sections.append(_row_desc(row))

    return "\n".join(sections)
