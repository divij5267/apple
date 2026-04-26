"""Queue-aware inventory parser.

Each queue exports its inventory from a source system (Snowflake, Excel, etc.)
with its own table schema. The parser knows which columns matter per queue and
ignores the rest. Input accepted as raw pasted text OR a pandas DataFrame.

Usage from the notebook:
    EDD_INVENTORY_RAW = '''<paste the table here>'''
    initial_inventory = inventory_from_paste(
        EDD_INVENTORY_RAW, queue="EDD", snapshot_date=start_date,
    )
"""

from dataclasses import dataclass, field
from datetime import date
from io import StringIO
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from inventory import DeterministicInventory


AgeColumns = Union[str, List[str]]


# =============================================================================
# PER-QUEUE EXTRACT SCHEMAS — edit as new queues are onboarded
# =============================================================================
# Each queue's raw inventory table has its own column names. This config tells
# the parser which column is "age in days," whether each row is one item or
# aggregates multiple (via a count column), and any row filters to apply.
# =============================================================================

@dataclass
class InventorySchema:
    """Describes how to extract {age: count} from a queue's raw inventory table."""

    age_columns: AgeColumns
    # Column name(s) that hold the age-in-days value for each row. Accepts:
    #   - A single string: "Days Difference" — one column for all rows.
    #   - A list of strings: ["Days Difference", "case age"] — checked in order;
    #     for each row the parser uses the FIRST non-null value found.
    #     Use this when alerts and cases have separate age columns in the same
    #     pasted table.

    count_column: Optional[str] = None
    # If None, each row counts as 1 item (row-count aggregation).
    # If set, sum this column per age bucket.

    filters: Optional[Dict[str, Any]] = None
    # Row filters — only rows matching all key/value pairs are kept.
    # Value can be a single value (==) or a list (in).
    # Example: {"STATE": "Open", "DELETED": False}
    # None or {} = no filters.


QUEUE_INVENTORY_SCHEMAS: Dict[str, InventorySchema] = {
    # LE / AC / SIU pastes contain BOTH alerts and cases. Alerts have age in
    # "Days Difference"; cases have age in "case age". The parser reads the
    # first non-null value across those columns for each row → single FIFO pool.
    # EDD has only alerts, so single column only.
    "LE": InventorySchema(
        age_columns=["Days Difference", "case age"],
        count_column=None,
        filters=None,
    ),
    "EDD": InventorySchema(
        age_columns="Days Difference",
        count_column=None,
        filters=None,
    ),
    "AC": InventorySchema(
        age_columns=["Days Difference", "case age"],
        count_column=None,
        filters=None,
    ),
    "SIU": InventorySchema(
        age_columns=["Days Difference", "case age"],
        count_column=None,
        filters=None,
    ),
    # "Fraud": TBD.
    # "ML":    TBD.
}


# =============================================================================
# PARSER
# =============================================================================

def inventory_from_paste(
    raw: Union[str, pd.DataFrame],
    queue: str,
    snapshot_date: Optional[date] = None,
) -> DeterministicInventory:
    """Build a DeterministicInventory from a pasted table (text or DataFrame).

    Parameters
    ----------
    raw : str or DataFrame
        Raw extract. If str, auto-detects separator (tab first, then whitespace).
        If DataFrame, used directly.
    queue : str
        Queue name — selects the schema from QUEUE_INVENTORY_SCHEMAS.
    snapshot_date : date, optional
        Date the inventory was snapshotted. Stored as metadata.

    Returns
    -------
    DeterministicInventory with items_by_age populated.
    """
    if queue not in QUEUE_INVENTORY_SCHEMAS:
        raise KeyError(
            f"No inventory schema for queue {queue!r}. "
            f"Add one to QUEUE_INVENTORY_SCHEMAS in inventory_parser.py. "
            f"Known: {sorted(QUEUE_INVENTORY_SCHEMAS.keys())}."
        )
    schema = QUEUE_INVENTORY_SCHEMAS[queue]

    # Resolve expected age column names up front — used as hints when the
    # pasted text contains MULTIPLE stacked tables (e.g. alerts table followed
    # by a cases table, each with its own header row).
    age_cols_hint = (
        [schema.age_columns]
        if isinstance(schema.age_columns, str)
        else list(schema.age_columns)
    )

    # Convert input to DataFrame
    if isinstance(raw, pd.DataFrame):
        df = raw.copy()
    elif isinstance(raw, str):
        df = _parse_pasted_text(raw, age_cols_hint=age_cols_hint)
    else:
        raise TypeError(
            f"raw must be str or DataFrame; got {type(raw).__name__}."
        )

    # Normalize age_columns → list of candidate column names (priority order)
    age_cols = (
        [schema.age_columns]
        if isinstance(schema.age_columns, str)
        else list(schema.age_columns)
    )

    # Validate at least one of the expected age columns is present
    present_cols = [c for c in age_cols if c in df.columns]
    if not present_cols:
        raise KeyError(
            f"Queue {queue!r} expects one of {age_cols!r} in the paste, "
            f"but none were found. Columns present: {list(df.columns)}"
        )

    # Apply row filters (if any)
    if schema.filters:
        for col, expected in schema.filters.items():
            if col not in df.columns:
                raise KeyError(
                    f"Filter column {col!r} not present in paste. "
                    f"Columns: {list(df.columns)}"
                )
            if isinstance(expected, (list, tuple, set)):
                df = df[df[col].isin(expected)]
            else:
                df = df[df[col] == expected]

    # Collapse multiple age columns into a single `_age_` value per row:
    # use the first non-null across `present_cols` (priority = order in schema).
    def _first_non_null(row):
        for c in present_cols:
            v = row[c]
            if pd.notna(v):
                return v
        return None

    if len(present_cols) == 1:
        df["_age_"] = df[present_cols[0]]
    else:
        df["_age_"] = df.apply(_first_non_null, axis=1)

    df = df.dropna(subset=["_age_"])
    df["_age_"] = pd.to_numeric(df["_age_"], errors="coerce")
    df = df.dropna(subset=["_age_"])
    df["_age_"] = df["_age_"].astype(int)

    # Aggregate
    if schema.count_column is None:
        grouped = df.groupby("_age_").size()
    else:
        if schema.count_column not in df.columns:
            raise KeyError(
                f"count_column {schema.count_column!r} not present in paste. "
                f"Columns: {list(df.columns)}"
            )
        grouped = df.groupby("_age_")[schema.count_column].sum()

    items_by_age: Dict[int, float] = {int(age): float(count) for age, count in grouped.items()}

    inv = DeterministicInventory(snapshot_date=snapshot_date or date.today())
    inv.items_by_age = items_by_age
    return inv


def _parse_pasted_text(
    raw: str,
    age_cols_hint: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Parse a pasted inventory table.

    Supports two workflows:
      (A) ONE table with a single header row → parsed as-is.
      (B) MULTIPLE tables concatenated (e.g. alerts header + alert rows,
          followed by cases header + case rows). Headers are detected by the
          presence of any column name in `age_cols_hint`. Each sub-table is
          parsed independently and then concatenated (outer join — columns
          that exist in one sub-table but not the other become NaN for
          rows from the other).
    """
    raw = raw.strip()
    if not raw:
        raise ValueError("Empty paste.")

    lines = [ln for ln in raw.split("\n") if ln.strip()]

    # Try to detect multiple header rows by scanning for lines that contain
    # any of the schema's age column names.
    header_indices: List[int] = []
    if age_cols_hint:
        for i, line in enumerate(lines):
            if any(col in line for col in age_cols_hint):
                header_indices.append(i)

    # If no headers match (or no hints given), fall back to single-table parse.
    if len(header_indices) <= 1:
        return _parse_single_table("\n".join(lines))

    # Multiple headers detected — split into sub-tables at each header
    sub_tables: List[pd.DataFrame] = []
    for j, start_idx in enumerate(header_indices):
        end_idx = header_indices[j + 1] if j + 1 < len(header_indices) else len(lines)
        sub_text = "\n".join(lines[start_idx:end_idx])
        try:
            sub_df = _parse_single_table(sub_text)
            if not sub_df.empty:
                sub_tables.append(sub_df)
        except Exception:
            # Skip sub-tables that fail to parse — keep going with the rest
            pass

    if not sub_tables:
        raise ValueError(
            "Could not parse any sub-table from the paste. "
            f"Detected {len(header_indices)} potential headers."
        )
    if len(sub_tables) == 1:
        return sub_tables[0]
    # Outer concat — columns present in some tables but not others become NaN.
    # The calling code's multi-column age logic handles NaN naturally.
    return pd.concat(sub_tables, ignore_index=True, sort=False)


def _parse_single_table(raw: str) -> pd.DataFrame:
    """Parse one table. Tries tab, comma, then whitespace separators."""
    raw = raw.strip()
    if not raw:
        raise ValueError("Empty sub-table.")

    try:
        df = pd.read_csv(StringIO(raw), sep="\t")
        if df.shape[1] >= 2:
            return df
    except Exception:
        pass

    try:
        df = pd.read_csv(StringIO(raw))
        if df.shape[1] >= 2:
            return df
    except Exception:
        pass

    try:
        df = pd.read_csv(StringIO(raw), sep=r"\s+", engine="python")
        return df
    except Exception as e:
        raise ValueError(
            "Could not parse table. Tried tab, comma, whitespace separators. "
            f"Last error: {e}"
        )


# =============================================================================
# DUAL-FORMAT INVENTORY BUILDER
# =============================================================================
# Each queue can provide its initial inventory in TWO formats:
#
#   (1) inv_input  — comma-separated string "age:count, age:count, ..."
#                     Quick to type, useful for tests / manual edits.
#                     Example:  "1:2, 5:10, 23:699"
#
#   (2) inventory_raw — paste of the actual extract (string or DataFrame).
#                       Production format for real Snowflake/Excel data.
#                       Parsed via the queue's QUEUE_INVENTORY_SCHEMAS entry.
#
# Precedence when BOTH non-empty:
#   prefer_raw=True  (default) → paste wins; warning printed.
#   prefer_raw=False           → inv_input wins; warning printed.
# Both empty → ValueError.
# =============================================================================

def inventory_from_input_or_paste(
    queue: str,
    snapshot_date: date,
    inv_input: str = "",
    inventory_raw: Union[str, pd.DataFrame] = "",
    prefer_raw: bool = True,
) -> DeterministicInventory:
    """Build a DeterministicInventory from either format. See module docstring."""
    has_inv = bool(inv_input and isinstance(inv_input, str) and inv_input.strip())
    has_raw = bool(
        (isinstance(inventory_raw, str) and inventory_raw.strip())
        or (isinstance(inventory_raw, pd.DataFrame) and not inventory_raw.empty)
    )

    if not has_inv and not has_raw:
        raise ValueError(
            f"Queue {queue!r}: both inv_input and inventory_raw are empty. "
            "Provide at least one."
        )

    if has_inv and has_raw:
        winner = "inventory_raw (paste)" if prefer_raw else "inv_input (inline)"
        print(
            f"\u26a0\ufe0f  Queue {queue!r}: both inv_input and inventory_raw "
            f"provided. Using {winner}. Set prefer_raw=False/True to flip."
        )
        if prefer_raw:
            return inventory_from_paste(inventory_raw, queue=queue, snapshot_date=snapshot_date)
        return _parse_inv_input_string(inv_input, snapshot_date)

    if has_raw:
        return inventory_from_paste(inventory_raw, queue=queue, snapshot_date=snapshot_date)

    return _parse_inv_input_string(inv_input, snapshot_date)


def _parse_inv_input_string(inv_input: str, snapshot_date: date) -> DeterministicInventory:
    """Parse a 'age:count, age:count, ...' string into a DeterministicInventory."""
    inv = DeterministicInventory(snapshot_date=snapshot_date)
    for item in inv_input.split(","):
        item = item.strip()
        if ":" not in item:
            continue
        age_str, count_str = item.split(":", 1)
        age_str, count_str = age_str.strip(), count_str.strip()
        if not age_str or not count_str:
            continue
        try:
            inv.add_items(int(age_str), float(count_str))
        except (ValueError, TypeError):
            # Skip malformed entries silently — same behaviour as before.
            pass
    return inv


# =============================================================================
# DIAGNOSTIC
# =============================================================================

def print_inventory_summary(
    inventory: DeterministicInventory,
    workable_age_min: Optional[int] = None,
    workable_age_max: Optional[int] = None,
) -> None:
    """Print the loaded inventory's age distribution. Call after parsing to verify."""
    print("=" * 60)
    print("INVENTORY SUMMARY")
    print("=" * 60)

    if not inventory.items_by_age:
        print("  (empty)")
        return

    total = inventory.get_total_items()
    ages = sorted(inventory.items_by_age.keys())

    print(f"  Snapshot date: {inventory.snapshot_date}")
    print(f"  Total items:   {total:,}")
    print(f"  Age range:     {ages[0]} .. {ages[-1]} days")
    print(f"  Avg age:       {inventory.calculate_average_age():.1f} days")

    if workable_age_min is not None or workable_age_max is not None:
        lo = workable_age_min if workable_age_min is not None else 0
        hi = workable_age_max if workable_age_max is not None else 10_000
        workable = sum(c for a, c in inventory.items_by_age.items() if lo <= a <= hi)
        non_workable = total - workable
        lbl = f"[{lo}..{'∞' if workable_age_max is None else workable_age_max}]"
        print(f"  Workable window {lbl}: {workable:,} items ({100*workable/total:.1f}%)")
        print(f"  Non-workable:              {non_workable:,} items ({100*non_workable/total:.1f}%)")

    print("\n  Age bucket distribution (first 20 buckets):")
    for age in ages[:20]:
        count = inventory.items_by_age[age]
        bar = "#" * min(40, int(count))
        print(f"    age {age:>4}: {count:>8,.1f}  {bar}")
    if len(ages) > 20:
        remaining = sum(inventory.items_by_age[a] for a in ages[20:])
        print(f"    ... {len(ages) - 20} more buckets, {remaining:,.1f} items total")
    print("=" * 60)
