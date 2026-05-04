"""Diagnostics + charts for one or more DeterministicResult objects.

Everything here READS results — nothing mutates state. Safe to call after sims
complete, no matter how many scenarios.
"""

from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from Cycle_time_calculator import (
    DeterministicResult,
    DeterministicScenario,
    DeterministicCycleTimeCalculator,
)


# =============================================================================
# WEEKLY AGGREGATION
# =============================================================================
# Week = ISO week (Monday start). Each row summarizes one week:
#   week                  → first day of the week (Monday)
#   demand                → total arrivals across all streams that week
#   avg_fte               → avg daily total FTE (sum across worker groups, then avg)
#   avg_inv_age           → avg of daily open-inventory average age
#   p_direct              → P_n of closed-item ages this week
#   p_from_open_ratio     → max-daily-avg-open-age × open_inventory_ratio
#   p_from_closed_ratio   → max-daily-avg-closed-age × closed_inventory_ratio
#
# All three P_n values respect the scenario's `workable_age_min` offset
# (subtract `workable_age_min - 1`) — same convention as monthly metrics.
# =============================================================================

def weekly_summary(
    result: DeterministicResult,
    calc: DeterministicCycleTimeCalculator,
) -> pd.DataFrame:
    """Return per-ISO-week rollup with FTE, demand, inventory age, and P_n."""
    scenario = calc.scenario
    p = scenario.reporting_percentile
    offset = (scenario.workable_age_min - 1) if scenario.workable_age_min is not None else 0

    # Build per-day rows including everything we need to aggregate later.
    daily_rows = []
    for dr in result.daily_results:
        iso_year, iso_week, _ = dr.date.isocalendar()
        total_demand_today = float(sum(a["volume"] for a in dr.arrivals))
        total_fte_today = float(sum(g["fte_for_month"] for g in dr.group_stats))
        closed_ages_today = dr.get_closed_items_ages()
        avg_closed_age_today = float(np.mean(closed_ages_today)) if closed_ages_today else None
        daily_rows.append({
            "iso_year":             iso_year,
            "iso_week":             iso_week,
            "date":                 dr.date,
            "demand":               total_demand_today,
            "total_fte":            total_fte_today,
            "avg_inv_age":          dr.open_inventory_after.calculate_average_age(),
            "closed_ages":          closed_ages_today,
            "avg_closed_age_today": avg_closed_age_today,
        })

    if not daily_rows:
        return pd.DataFrame()

    # Group rows by (iso_year, iso_week)
    weeks: Dict[Tuple[int, int], List[Dict]] = {}
    for r in daily_rows:
        weeks.setdefault((r["iso_year"], r["iso_week"]), []).append(r)

    def _ceil_int(x):
        return int(np.ceil(x)) if x is not None else None

    out_rows = []
    for (_iy, _iw), week_rows in sorted(weeks.items()):
        week_start = min(r["date"] for r in week_rows)

        total_demand = sum(r["demand"] for r in week_rows)
        avg_fte = float(np.mean([r["total_fte"] for r in week_rows]))
        avg_inv_age = float(np.mean([r["avg_inv_age"] for r in week_rows]))

        # P_direct: percentile of all closed-item ages in the week
        all_closed: List[int] = []
        for r in week_rows:
            all_closed.extend(r["closed_ages"])
        p_direct_raw = float(np.percentile(all_closed, p)) if all_closed else None

        # P_from_open_ratio: max daily avg-open-age × open_inventory_ratio
        max_open = max((r["avg_inv_age"] for r in week_rows), default=None)
        p_from_open_raw = (
            max_open * scenario.open_inventory_ratio if max_open is not None else None
        )

        # P_from_closed_ratio: max daily avg-closed-age × closed_inventory_ratio
        max_closed_vals = [
            r["avg_closed_age_today"] for r in week_rows
            if r["avg_closed_age_today"] is not None
        ]
        max_closed = max(max_closed_vals) if max_closed_vals else None
        p_from_closed_raw = (
            max_closed * scenario.closed_inventory_ratio if max_closed is not None else None
        )

        p_direct = _ceil_int(p_direct_raw)
        p_from_open = _ceil_int(p_from_open_raw)
        p_from_closed = _ceil_int(p_from_closed_raw)

        # Subtract pre-workable offset (e.g. EDD: 60) so reported cycle time
        # reflects operational period only.
        if offset:
            if p_direct is not None:
                p_direct = p_direct - offset
            if p_from_open is not None:
                p_from_open = p_from_open - offset
            if p_from_closed is not None:
                p_from_closed = p_from_closed - offset

        out_rows.append({
            "week":                 week_start,
            "demand":               total_demand,
            "avg_fte":              round(avg_fte, 2),
            "avg_inv_age":          round(avg_inv_age, 2),
            "p_direct":             p_direct,
            "p_from_open_ratio":    p_from_open,
            "p_from_closed_ratio":  p_from_closed,
        })

    return pd.DataFrame(out_rows)


# =============================================================================
# MONTHLY SUMMARY — option C two-line-per-row layout
# =============================================================================
# Each month produces TWO rows: the first carries FTE values per group + the
# scalar metrics (Demand, avg_inv_age, P_n's). The second carries TPT values
# per group on the same group-name columns; non-group cells are blank.
#
# Group cells are formatted as "FTE: 33.40" / "TPT:  2.40" so the prefix
# survives DataFrame.to_string() alignment.
# =============================================================================

def monthly_summary(
    result: DeterministicResult,
    calc: DeterministicCycleTimeCalculator,
) -> pd.DataFrame:
    """Return monthly diagnostic table in two-line-per-row format (option C)."""
    scenario = calc.scenario
    p = scenario.reporting_percentile
    offset = (scenario.workable_age_min - 1) if scenario.workable_age_min is not None else 0

    if not result.daily_results:
        return pd.DataFrame()

    # Group names (preserve order from the scenario's worker_groups)
    group_names = [gs["name"] for gs in result.daily_results[0].group_stats]

    # Per-day data
    daily_rows: List[Dict] = []
    for dr in result.daily_results:
        total_demand_today = float(sum(a["volume"] for a in dr.arrivals))
        fte_by_group = {gs["name"]: gs["fte_for_month"] for gs in dr.group_stats}
        tpt_by_group = {gs["name"]: gs["tpt"] for gs in dr.group_stats}
        closed_ages_today = dr.get_closed_items_ages()
        avg_closed = float(np.mean(closed_ages_today)) if closed_ages_today else None
        daily_rows.append({
            "year":          dr.date.year,
            "month":         dr.date.month,
            "demand":        total_demand_today,
            "fte_by_group":  fte_by_group,
            "tpt_by_group":  tpt_by_group,
            "avg_inv_age":   dr.open_inventory_after.calculate_average_age(),
            "closed_ages":   closed_ages_today,
            "avg_closed":    avg_closed,
        })

    months: Dict[Tuple[int, int], List[Dict]] = {}
    for r in daily_rows:
        months.setdefault((r["year"], r["month"]), []).append(r)

    def _ceil_int(x):
        return int(np.ceil(x)) if x is not None else None

    out_rows: List[Dict] = []
    for (y, m), rows in sorted(months.items()):
        total_demand = sum(r["demand"] for r in rows)
        ftes = {g: float(np.mean([r["fte_by_group"][g] for r in rows])) for g in group_names}
        tpts = {g: float(np.mean([r["tpt_by_group"][g] for r in rows])) for g in group_names}
        avg_inv_age = float(np.mean([r["avg_inv_age"] for r in rows]))

        # P_n metrics (same logic as monthly metrics + workable-age offset)
        all_closed: List[int] = []
        for r in rows:
            all_closed.extend(r["closed_ages"])
        p_direct_raw = float(np.percentile(all_closed, p)) if all_closed else None
        max_open = max((r["avg_inv_age"] for r in rows), default=None)
        p_from_open_raw = (
            max_open * scenario.open_inventory_ratio if max_open is not None else None
        )
        max_closed_vals = [r["avg_closed"] for r in rows if r["avg_closed"] is not None]
        max_closed = max(max_closed_vals) if max_closed_vals else None
        p_from_closed_raw = (
            max_closed * scenario.closed_inventory_ratio if max_closed is not None else None
        )

        p_direct = _ceil_int(p_direct_raw)
        p_from_open = _ceil_int(p_from_open_raw)
        p_from_closed = _ceil_int(p_from_closed_raw)
        if offset:
            if p_direct is not None:
                p_direct = p_direct - offset
            if p_from_open is not None:
                p_from_open = p_from_open - offset
            if p_from_closed is not None:
                p_from_closed = p_from_closed - offset

        # Row 1 — FTE values + scalar metrics
        row_fte = {
            "Month":  f"{y}-{m:02d}",
            "Demand": f"{total_demand:.0f}",
        }
        for g in group_names:
            row_fte[g] = f"FTE: {ftes[g]:>6.2f}"
        row_fte["avg_inv_age"]         = f"{avg_inv_age:.2f}"
        row_fte["p_direct"]            = "" if p_direct is None else str(p_direct)
        row_fte["p_from_open_ratio"]   = "" if p_from_open is None else str(p_from_open)
        row_fte["p_from_closed_ratio"] = "" if p_from_closed is None else str(p_from_closed)
        out_rows.append(row_fte)

        # Row 2 — TPT values, blank for non-group cells
        row_tpt = {
            "Month":  "",
            "Demand": "",
        }
        for g in group_names:
            row_tpt[g] = f"TPT: {tpts[g]:>6.2f}"
        row_tpt["avg_inv_age"]         = ""
        row_tpt["p_direct"]            = ""
        row_tpt["p_from_open_ratio"]   = ""
        row_tpt["p_from_closed_ratio"] = ""
        out_rows.append(row_tpt)

    return pd.DataFrame(out_rows)


# =============================================================================
# MONTHLY P VALUES helper — used by both charts
# =============================================================================

def _monthly_p_values(
    result: DeterministicResult,
    calc: DeterministicCycleTimeCalculator,
    method: str = "p_from_open_ratio",
) -> Dict[Tuple[int, int], int]:
    """Return {(year, month): p_value} for the configured percentile and method.

    `method` is one of "p_direct", "p_from_open_ratio", "p_from_closed_ratio".
    """
    out: Dict[Tuple[int, int], int] = {}
    months = []
    d = result.start_date
    while d <= result.end_date:
        k = (d.year, d.month)
        if k not in months:
            months.append(k)
        d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    for y, m in months:
        mm = calc.calculate_monthly_metrics(result, m, y)
        out[(y, m)] = mm.get(method)
    return out


def _pn_label(scenario: DeterministicScenario) -> str:
    """e.g. 'P90' or 'P95' depending on the scenario's reporting_percentile."""
    return f"P{scenario.reporting_percentile}"


# =============================================================================
# CHART 1 — MONTHLY CANDLESTICK CHART
# =============================================================================
# Each month gets a candlestick showing:
#   Open  = inventory level at start of the month (prev month's close)
#   High  = peak inventory level reached during the month
#   Low   = trough inventory level reached during the month
#   Close = inventory level at end of the month
# Body is green if close <= open (inventory dropped = progress),
# red if close > open (fell behind). Wicks extend to high and low.
# Pn (reporting percentile) badge labeled at the top of each candle.
# =============================================================================

def monthly_candlestick_chart(
    result: DeterministicResult,
    calc: DeterministicCycleTimeCalculator,
    method: str = "p_from_open_ratio",
    ax=None,
):
    """Plot per-month inventory candlesticks with Pn badges.

    Returns the matplotlib Figure.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Patch

    scenario = calc.scenario

    # Collect month-level OHLC from daily results
    month_stats: Dict[Tuple[int, int], Dict[str, float]] = {}
    initial_total = scenario.initial_inventory.get_total_items()
    prev_close = initial_total
    for dr in result.daily_results:
        k = (dr.date.year, dr.date.month)
        inv_today = dr.open_inventory_after.get_total_items()
        if k not in month_stats:
            month_stats[k] = {
                "open":  prev_close,
                "high":  inv_today,
                "low":   inv_today,
                "close": inv_today,
            }
        month_stats[k]["high"]  = max(month_stats[k]["high"],  inv_today)
        month_stats[k]["low"]   = min(month_stats[k]["low"],   inv_today)
        month_stats[k]["close"] = inv_today
        prev_close = inv_today

    sorted_keys = sorted(month_stats.keys())
    labels = [f"{m:02d}" for _, m in sorted_keys]
    p_vals = _monthly_p_values(result, calc, method)

    if ax is None:
        fig, ax = plt.subplots(figsize=(max(12, 1.1 * len(sorted_keys)), 7))
    else:
        fig = ax.figure

    x = np.arange(len(sorted_keys))
    body_width = 0.6
    wick_width = 2

    # Common height for Pn badges — above the highest wick across all months
    max_high = max(s["high"] for s in month_stats.values())
    p_label_y = max_high * 1.14
    pn_label = _pn_label(scenario)

    for i, k in enumerate(sorted_keys):
        s = month_stats[k]
        o, h, l, c = s["open"], s["high"], s["low"], s["close"]
        going_down = c <= o
        body_color = "#4CAF50" if going_down else "#E57373"
        edge_color = "#1B5E20" if going_down else "#B71C1C"

        # Wick
        ax.plot([x[i], x[i]], [l, h], color=edge_color, linewidth=wick_width, zorder=1)

        # Body rect from open to close
        body_bottom = min(o, c)
        body_top    = max(o, c)
        body_height = max(body_top - body_bottom, 1)
        rect = Rectangle(
            (x[i] - body_width/2, body_bottom),
            body_width, body_height,
            facecolor=body_color, edgecolor=edge_color, linewidth=1.5, zorder=2,
        )
        ax.add_patch(rect)

        # Labels at high / low
        ax.text(x[i], h + max_high * 0.02, f"{h:.0f}", ha="center", fontsize=8, color=edge_color)
        ax.text(x[i], l - max_high * 0.04, f"{l:.0f}", ha="center", fontsize=8, color=edge_color)

        # Pn badge at top
        p_val = p_vals.get(k)
        p_text = f"{pn_label}\n{p_val}d" if p_val is not None else f"{pn_label}\n—"
        ax.text(x[i], p_label_y, p_text, ha="center", fontsize=9,
                fontweight="bold", color="#37474F",
                bbox=dict(boxstyle="round,pad=0.25", fc="lemonchiffon", ec="#37474F", lw=0.8))

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Month")
    ax.set_ylabel("Open inventory (items)")
    ax.set_ylim(min(s["low"] for s in month_stats.values()) - max_high * 0.08,
                p_label_y * 1.08)
    ax.set_title(f"{scenario.name} — Monthly inventory candlesticks  "
                 f"(body = open→close; wicks = peak/trough)")
    ax.grid(axis="y", alpha=0.3)

    legend_elems = [
        Patch(facecolor="#4CAF50", edgecolor="#1B5E20", label="Close ≤ Open (progress)"),
        Patch(facecolor="#E57373", edgecolor="#B71C1C", label="Close > Open (fell behind)"),
        Patch(facecolor="lemonchiffon",                 label=f"Monthly {pn_label} (days)"),
    ]
    ax.legend(handles=legend_elems, loc="lower left", framealpha=0.9)

    fig.tight_layout()
    return fig


# =============================================================================
# CHART 2 — DAILY OPEN INVENTORY + MONTHLY Pn LABELS
# =============================================================================
# Daily line plot of open inventory, with a Pn badge labeled above each month.
# Shows the day-by-day rhythm (e.g. subpoena spikes on the 13th/27th) and the
# monthly cycle-time at a glance.
# =============================================================================

def daily_inventory_with_p_chart(
    result: DeterministicResult,
    calc: DeterministicCycleTimeCalculator,
    method: str = "p_from_open_ratio",
    ax=None,
):
    """Plot daily open inventory with monthly Pn (cycle-time days) labels."""
    import matplotlib.pyplot as plt

    scenario = calc.scenario

    daily_rows = []
    for dr in result.daily_results:
        daily_rows.append({
            "date": dr.date,
            "inv":  dr.open_inventory_after.get_total_items(),
        })
    dd = pd.DataFrame(daily_rows)
    if dd.empty:
        return None

    # Collect months in sim and their Pn values
    months = []
    d = result.start_date
    while d <= result.end_date:
        k = (d.year, d.month)
        if k not in months:
            months.append(k)
        d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    p_vals = _monthly_p_values(result, calc, method)
    pn_label = _pn_label(scenario)

    if ax is None:
        fig, ax = plt.subplots(figsize=(max(14, 1.1 * len(months)), 7))
    else:
        fig = ax.figure

    ax.plot(dd["date"], dd["inv"], color="#1976D2", linewidth=1.4, label="Open inventory")
    ax.fill_between(dd["date"], 0, dd["inv"], color="#1976D2", alpha=0.12)

    y_max = dd["inv"].max()
    label_y = y_max * 1.10 if y_max > 0 else 1.0

    for y, m in months:
        month_dates = [d for d in dd["date"] if d.year == y and d.month == m]
        if not month_dates:
            continue
        mid_date = month_dates[len(month_dates) // 2]
        ax.axvline(mid_date, color="gray", alpha=0.15, linewidth=0.6, zorder=0)
        p_val = p_vals.get((y, m))
        p_text = f"{pn_label}\n{p_val}d" if p_val is not None else f"{pn_label}\n—"
        ax.text(mid_date, label_y, p_text, ha="center", va="center", fontsize=9,
                fontweight="bold", color="#37474F",
                bbox=dict(boxstyle="round,pad=0.3", fc="lemonchiffon", ec="#37474F", lw=0.8))

    ax.set_ylim(0, label_y * 1.12)
    ax.set_ylabel("Open inventory (items)")
    ax.set_xlabel("Date")
    ax.set_title(f"{scenario.name} — Daily open inventory with monthly {pn_label} cycle-time")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right")
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


# =============================================================================
# CHART 3 — MONTHLY WATERFALL CHART
# =============================================================================
# Waterfall semantics — each bar starts where the previous one ended:
#   Bar 1 (Start):  0 → start_inv                        (gray)
#   Bar 2 (Demand): start_inv → start_inv + demand_total (stacked by stream)
#   Bar 3 (Burn):   end_inv → peak                       (red; bottom = end,
#                                                         top = peak)
# Connecting dashed lines show the flow at each step; dotted line carries the
# month's ending inventory across to the next month's starting position.
# Only the first month shows a "start:" badge (subsequent months start where
# the previous ended — shown by "end: XXXX" badge + dotted line).
# Pn (reporting percentile) badge sits above each month's peak, same source-
# method default as the candlestick (`p_from_open_ratio`).
# =============================================================================

def monthly_waterfall_chart(
    result: DeterministicResult,
    calc: DeterministicCycleTimeCalculator,
    method: str = "p_from_open_ratio",
    ax=None,
):
    """Plot monthly waterfall: Start + Demand (stacked by stream) − Burned = End,
    with monthly Pn cycle-time badge above each month's peak.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    scenario = calc.scenario

    # --- Aggregate per-month arrivals + burns ---
    rows = []
    for dr in result.daily_results:
        entry = {"year": dr.date.year, "month": dr.date.month, "burned": dr.total_burned}
        for a in dr.arrivals:
            key = f"arr__{a['name']}"
            entry[key] = entry.get(key, 0.0) + a["volume"]
        rows.append(entry)
    df = pd.DataFrame(rows).fillna(0.0)
    if df.empty:
        return None

    arr_cols = [c for c in df.columns if c.startswith("arr__")]
    monthly = df.groupby(["year", "month"]).agg(
        {**{"burned": "sum"}, **{c: "sum" for c in arr_cols}},
    ).reset_index()

    # --- Per-month start/end ---
    month_ends = {}
    for dr in result.daily_results:
        k = (dr.date.year, dr.date.month)
        month_ends[k] = dr.open_inventory_after.get_total_items()
    sorted_keys = sorted(month_ends.keys())

    starts = []
    prev = scenario.initial_inventory.get_total_items()
    for k in sorted_keys:
        starts.append(prev)
        prev = month_ends[k]
    starts = np.array(starts)
    ends = np.array([month_ends[k] for k in sorted_keys])
    burned = np.array([
        monthly.loc[(monthly.year == y) & (monthly.month == m), "burned"].values[0]
        for y, m in sorted_keys
    ])
    stream_names = [c.replace("arr__", "") for c in arr_cols]
    arrivals = np.array([
        [monthly.loc[(monthly.year == y) & (monthly.month == m), c].values[0]
         for c in arr_cols]
        for y, m in sorted_keys
    ])
    demand_totals = arrivals.sum(axis=1)
    peaks = starts + demand_totals

    labels = [f"{m:02d}" for _, m in sorted_keys]

    # Monthly Pn values + label position above the highest month-peak
    p_vals = _monthly_p_values(result, calc, method)
    pn_label = _pn_label(scenario)
    p_label_y = peaks.max() * 1.14

    if ax is None:
        fig, ax = plt.subplots(figsize=(max(16, 1.3 * len(labels)), 8))
    else:
        fig = ax.figure

    x_positions = np.arange(len(labels)) * 1.0
    bar_width = 0.22
    offset = 0.27
    stream_colors = ["#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#00BCD4", "#FFC107"]

    for i, k in enumerate(sorted_keys):
        x_center = x_positions[i]
        x_start  = x_center - offset
        x_demand = x_center
        x_burn   = x_center + offset

        start = starts[i]; demand_total = demand_totals[i]
        peak = peaks[i]; burn = burned[i]; end = ends[i]

        # Bar 1 — start
        ax.bar(x_start, start, bar_width, bottom=0, color="#B0BEC5",
               edgecolor="black", linewidth=0.8,
               label="Starting inventory" if i == 0 else None)

        # Bar 2 — demand stacked
        bottom = start
        for j, vals in enumerate(arrivals[i]):
            ax.bar(x_demand, vals, bar_width, bottom=bottom,
                   color=stream_colors[j % len(stream_colors)],
                   edgecolor="black", linewidth=0.8,
                   label=f"Demand: {stream_names[j]}" if i == 0 else None)
            bottom += vals

        # Bar 3 — burn (bottom = end, top = peak)
        ax.bar(x_burn, burn, bar_width, bottom=end, color="#E57373",
               edgecolor="darkred", linewidth=0.8,
               label="Burned" if i == 0 else None)

        # Flow connectors
        ax.plot([x_start + bar_width/2, x_demand - bar_width/2],
                [start, start], ls="--", color="gray", linewidth=1, alpha=0.7)
        ax.plot([x_demand + bar_width/2, x_burn - bar_width/2],
                [peak, peak], ls="--", color="gray", linewidth=1, alpha=0.7)
        if i < len(labels) - 1:
            next_start_x = x_positions[i + 1] - offset - bar_width / 2
            ax.plot([x_burn + bar_width/2, next_start_x],
                    [end, end], ls=":", color="#37474F", linewidth=1, alpha=0.5)

        # Labels — start badge only on first month (thereafter = prev month's end)
        if i == 0:
            ax.text(x_start, start + 30, f"start: {start:.0f}", ha="center",
                    fontsize=8, color="#37474F",
                    bbox=dict(boxstyle="round,pad=0.15", fc="#ECEFF1",
                              ec="#455A64", lw=0.6))

        ax.text(x_demand, peak + 30, f"+{demand_total:.0f}", ha="center", fontsize=7,
                fontweight="bold", color="#1565C0")
        ax.text(x_burn, (end + peak) / 2, f"-{burn:.0f}", ha="center", va="center",
                fontsize=8, fontweight="bold", color="white")
        ax.text(x_burn + 0.02, end, f"end: {end:.0f}", ha="left", va="center",
                fontsize=8, color="#37474F",
                bbox=dict(boxstyle="round,pad=0.15", fc="#E8F5E9",
                          ec="#2E7D32", lw=0.6))

        # Pn badge — same lemonchiffon style as the other two charts.
        p_val = p_vals.get(k)
        p_text = f"{pn_label}\n{p_val}d" if p_val is not None else f"{pn_label}\n—"
        ax.text(x_center, p_label_y, p_text, ha="center", va="center",
                fontsize=9, fontweight="bold", color="#37474F",
                bbox=dict(boxstyle="round,pad=0.25", fc="lemonchiffon",
                          ec="#37474F", lw=0.8))

    ax.set_xticks(x_positions)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Month")
    ax.set_ylabel("Items (waterfall — each bar starts where previous ended)")
    ax.set_ylim(0, p_label_y * 1.08)
    ax.set_title(f"{scenario.name} — Monthly waterfall: "
                 "Start + Demand (stacked) − Burned = End", fontsize=12)
    # Legend: implicit handles from bar labels + manual Pn patch.
    handles, _ = ax.get_legend_handles_labels()
    handles.append(Patch(facecolor="lemonchiffon", edgecolor="#37474F",
                         label=f"Monthly {pn_label} (days)"))
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, -0.17),
              ncol=6, fontsize=9, frameon=False)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return fig


# =============================================================================
# render_scenario_charts — convenience: produces ALL charts for one scenario
# =============================================================================

def render_scenario_charts(
    result: DeterministicResult,
    calc: DeterministicCycleTimeCalculator,
    method: str = "p_from_open_ratio",
    show: bool = True,
):
    """Render the three standard charts for a single scenario:
       1) monthly inventory candlestick
       2) daily open inventory with monthly Pn
       3) monthly waterfall (start + demand − burned = end)
    """
    import matplotlib.pyplot as plt
    fig1 = monthly_candlestick_chart(result, calc, method=method)
    if show:
        plt.show()
    fig2 = daily_inventory_with_p_chart(result, calc, method=method)
    if show:
        plt.show()
    fig3 = monthly_waterfall_chart(result, calc, method=method)
    if show:
        plt.show()
    return fig1, fig2, fig3


# =============================================================================
# SCENARIO COMPARISON TABLE
# =============================================================================

def compare_scenarios_table(
    results: Dict[str, Tuple[DeterministicCycleTimeCalculator, DeterministicResult]],
) -> pd.DataFrame:
    """One row per (queue, year, month) with each scenario's monthly metrics.

    Useful side-by-side view when running multiple scenarios.
    """
    rows = []
    for queue_name, (calc, result) in results.items():
        months_seen = set()
        d = result.start_date
        while d <= result.end_date:
            months_seen.add((d.year, d.month))
            d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
        for y, m in sorted(months_seen):
            mm = calc.calculate_monthly_metrics(result, m, y)
            rows.append({
                "scenario":             queue_name,
                "year":                 y,
                "month":                m,
                "percentile":           mm["percentile"],
                "p_direct":             mm["p_direct"],
                "p_from_open_ratio":    mm["p_from_open_ratio"],
                "p_from_closed_ratio":  mm["p_from_closed_ratio"],
            })
    return pd.DataFrame(rows)
