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
# Week = ISO week (Monday start). Each week summarizes arrivals, closures,
# capacity, inventory level, avg age. Finer grain than monthly — useful for
# spotting mid-month anomalies.
# =============================================================================

def weekly_summary(result: DeterministicResult) -> pd.DataFrame:
    """Return per-ISO-week rollup across all daily results."""
    rows = []
    for dr in result.daily_results:
        iso_year, iso_week, _ = dr.date.isocalendar()
        total_arrivals = float(sum(a["volume"] for a in dr.arrivals))
        rows.append({
            "iso_year":        iso_year,
            "iso_week":        iso_week,
            "date":            dr.date,
            "capacity":        dr.total_capacity,
            "burned":          dr.total_burned,
            "arrivals":        total_arrivals,
            "inv_end_of_day":  dr.open_inventory_after.get_total_items(),
            "avg_inv_age":     dr.open_inventory_after.calculate_average_age(),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    grouped = df.groupby(["iso_year", "iso_week"]).agg(
        week_start=("date", "min"),
        week_end=("date", "max"),
        total_capacity=("capacity", "sum"),
        total_burned=("burned", "sum"),
        total_arrivals=("arrivals", "sum"),
        avg_daily_capacity=("capacity", "mean"),
        end_inv=("inv_end_of_day", "last"),
        avg_inv_age=("avg_inv_age", "mean"),
    ).reset_index()
    return grouped


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
# =============================================================================

def monthly_waterfall_chart(
    result: DeterministicResult,
    calc: DeterministicCycleTimeCalculator,
    ax=None,
):
    """Plot monthly waterfall: Start + Demand (stacked by stream) − Burned = End."""
    import matplotlib.pyplot as plt

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

    ax.set_xticks(x_positions)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Month")
    ax.set_ylabel("Items (waterfall — each bar starts where previous ended)")
    ax.set_ylim(0, peaks.max() * 1.10)
    ax.set_title(f"{scenario.name} — Monthly waterfall: "
                 "Start + Demand (stacked) − Burned = End", fontsize=12)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.17), ncol=5,
              fontsize=9, frameon=False)
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
    fig3 = monthly_waterfall_chart(result, calc)
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
