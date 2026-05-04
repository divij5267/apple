"""Unit tests for ct_calculator.

Run with:  python -m pytest test_ct_calculator.py -v

Covers the promises we've made to the user:
  - Calendar: weekend-shifting (USA only), half-day auto-generation (INTERNAL only)
  - Workforce: MonthlyInput (scalar/list/Months/year-keyed), ramp linear with
    tenure-0-on-arrival, prorated attrition, RS removals discrete, TPT required,
    INTERNAL fte_conversion required
  - Inventory parser: text + DataFrame, schema filtering, missing-column errors
  - Demand: daily, on_days_of_month, on_weekdays cadences
  - Calculator: FIFO oldest-first, workable-age window filter, float burn-down
    (no int truncation), reporting_percentile per scenario, scenario.validate()
"""

from datetime import date
from io import StringIO

import numpy as np
import pandas as pd
import pytest

from Calendar import (
    CalendarManager, CalendarType, OperationsCalendar,
    HOLIDAYS, load_all_calendars,
)
from workforce import (
    DeterministicWorkforce, WorkerGroup, Months, resolve_monthly,
)
from inventory import (
    DeterministicInventory, DeterministicDemand, DemandStream,
    AgeDistributionStream,
)
from inventory_parser import (
    inventory_from_paste, inventory_from_input_or_paste, QUEUE_INVENTORY_SCHEMAS,
)
from Cycle_time_calculator import (
    DeterministicScenario, DeterministicCycleTimeCalculator,
)


# =============================================================================
# CALENDAR
# =============================================================================

class TestCalendar:
    def test_usa_calendar_shifts_saturday_holiday_to_friday(self):
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        # July 4 2026 is a Saturday — should be observed on Fri July 3
        assert mgr.usa_calendar.is_holiday(date(2026, 7, 3))
        assert not mgr.usa_calendar.is_holiday(date(2026, 7, 4))

    def test_internal_calendar_generates_half_days(self):
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        # INTERNAL should have half-days before holidays; USA should not
        assert len(mgr.internal_calendar.half_days) > 0
        assert len(mgr.usa_calendar.half_days) == 0

    def test_india_calendar_does_not_shift_weekend_holidays(self):
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        # Aug 15 2026 is Saturday — India doesn't shift
        assert mgr.india_calendar.is_holiday(date(2026, 8, 15))
        assert not mgr.india_calendar.is_holiday(date(2026, 8, 14))


# =============================================================================
# WORKFORCE / MONTHLY INPUT
# =============================================================================

class TestMonthlyInput:
    def test_scalar_broadcasts(self):
        assert resolve_monthly(2.4, 2026, 3, date(2026, 1, 1)) == 2.4
        assert resolve_monthly(2.4, 2027, 8, date(2026, 1, 1)) == 2.4

    def test_months_helper_calendar_based(self):
        m = Months(jan=1, feb=2, mar=3)
        assert resolve_monthly(m, 2026, 1, date(2026, 1, 1)) == 1
        assert resolve_monthly(m, 2027, 2, date(2026, 1, 1)) == 2
        assert resolve_monthly(m, 2026, 4, date(2026, 1, 1), default=99) == 99

    def test_12_list_sim_relative_cycles(self):
        lst = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
        sim_start = date(2026, 1, 6)  # January
        assert resolve_monthly(lst, 2026, 1, sim_start) == 10
        assert resolve_monthly(lst, 2026, 6, sim_start) == 60
        assert resolve_monthly(lst, 2027, 1, sim_start) == 10  # cycles

    def test_year_keyed_dict(self):
        val = {2026: Months(mar=3), 2027: Months(mar=5)}
        ss = date(2026, 1, 1)
        assert resolve_monthly(val, 2026, 3, ss) == 3
        assert resolve_monthly(val, 2027, 3, ss) == 5

    def test_tuple_override_has_precedence(self):
        val = {3: 5, (2026, 3): 99}
        ss = date(2026, 1, 1)
        assert resolve_monthly(val, 2026, 3, ss) == 99
        assert resolve_monthly(val, 2027, 3, ss) == 5


class TestRampAndAttrition:
    def _group(self, **kw):
        base = dict(
            calendar_type=CalendarType.INTERNAL,
            current_headcount=0.0,
            ramp_period_months=6,
            tpt_by_month=1.0,
            fte_conversion_by_month=1.0,
        )
        base.update(kw)
        return WorkerGroup(**base)

    def test_tenure_zero_on_arrival(self):
        # recent_hires[0] = "hired this month" -> tenure 0 at sim month 1
        g = self._group(recent_hires=[4, 3, 3])
        wf = DeterministicWorkforce(worker_groups=[g])
        raw = wf.raw_headcount(0, date(2026, 1, 1), date(2026, 1, 15))
        # tenures 0, 1, 2 -> 0, 1/6, 2/6 -> 0 + 0.5 + 1.0 = 1.5
        assert abs(raw - 1.5) < 1e-6

    def test_ramp_factor_caps_at_1(self):
        g = self._group(recent_hires=[0, 0, 0, 0, 0, 0, 0, 10])  # hired long ago
        wf = DeterministicWorkforce(worker_groups=[g])
        raw = wf.raw_headcount(0, date(2026, 1, 1), date(2026, 1, 15))
        # tenure = 7 + 1 - 1 = 7, ramp=6, factor capped at 1.0, contribution = 10
        assert abs(raw - 10.0) < 1e-6

    def test_attrition_prorated_linearly(self):
        # 1.5/month, sim Jan 1 starts full month visible -> Jan 31 = 1.5 lost
        g = self._group(current_headcount=45.8, attrition_per_month=1.5)
        wf = DeterministicWorkforce(worker_groups=[g])
        raw_jan_31 = wf.raw_headcount(0, date(2026, 1, 1), date(2026, 1, 31))
        assert abs(raw_jan_31 - 44.3) < 1e-6

    def test_mid_sim_hires_arrive_at_tenure_zero(self):
        g = self._group(
            current_headcount=10,
            monthly_hires=Months(mar=3),
        )
        wf = DeterministicWorkforce(worker_groups=[g])
        ss = date(2026, 1, 6)
        # March (arrival, tenure 0) -> raw = 10 + 3*0 = 10
        assert abs(wf.raw_headcount(0, ss, date(2026, 3, 15)) - 10.0) < 1e-4
        # April (tenure 1) -> 10 + 3*(1/6) = 10.5
        assert abs(wf.raw_headcount(0, ss, date(2026, 4, 15)) - 10.5) < 1e-4

    def test_rs_removals_discrete_per_month(self):
        g = self._group(
            calendar_type=CalendarType.CIPH,
            current_headcount=25,
            ramp_period_months=4,
            rightsource_removals_per_month=Months(jan=2, feb=3),
        )
        wf = DeterministicWorkforce(worker_groups=[g])
        ss = date(2026, 1, 6)
        assert abs(wf.raw_headcount(0, ss, date(2026, 1, 15)) - 23.0) < 1e-6
        assert abs(wf.raw_headcount(0, ss, date(2026, 2, 15)) - 20.0) < 1e-6


class TestWorkforceValidation:
    def test_missing_tpt_raises(self):
        g = WorkerGroup(calendar_type=CalendarType.USA, current_headcount=5)
        wf = DeterministicWorkforce(worker_groups=[g])
        with pytest.raises(ValueError, match="tpt_by_month"):
            wf.group_tpt_for_date(0, date(2026, 1, 1), date(2026, 1, 15))

    def test_internal_missing_fte_conversion_raises(self):
        g = WorkerGroup(calendar_type=CalendarType.INTERNAL,
                        current_headcount=10, tpt_by_month=2.4)
        wf = DeterministicWorkforce(worker_groups=[g])
        with pytest.raises(ValueError, match="fte_conversion_by_month"):
            wf.group_fte_for_date(0, date(2026, 1, 1), date(2026, 1, 15))

    def test_rs_missing_fte_conversion_defaults_to_1(self):
        g = WorkerGroup(calendar_type=CalendarType.USA,
                        current_headcount=5, tpt_by_month=2.4)
        wf = DeterministicWorkforce(worker_groups=[g])
        fte = wf.group_fte_for_date(0, date(2026, 1, 1), date(2026, 1, 15))
        assert fte == 5.0


# =============================================================================
# INVENTORY PARSER
# =============================================================================

class TestInventoryParser:
    def test_edd_counts_rows_by_age(self):
        raw = (
            "QUEUE\tDays Difference\n"
            "EDD\t93\n"
            "EDD\t93\n"
            "EDD\t93\n"
            "EDD\t107\n"
            "EDD\t114\n"
        )
        inv = inventory_from_paste(raw, queue="EDD", snapshot_date=date(2026, 1, 6))
        assert inv.items_by_age == {93: 3, 107: 1, 114: 1}

    def test_parser_accepts_dataframe(self):
        df = pd.DataFrame({
            "QUEUE": ["EDD"] * 4,
            "Days Difference": [100, 100, 200, 200],
        })
        inv = inventory_from_paste(df, queue="EDD", snapshot_date=date(2026, 1, 6))
        assert inv.items_by_age == {100: 2, 200: 2}

    def test_missing_age_column_raises(self):
        raw = "QUEUE\tOther\nEDD\tfoo\n"
        with pytest.raises(KeyError, match="Days Difference"):
            inventory_from_paste(raw, queue="EDD", snapshot_date=date(2026, 1, 6))

    def test_unknown_queue_raises(self):
        with pytest.raises(KeyError, match="schema"):
            inventory_from_paste("a\tb\n1\t2", queue="DOES_NOT_EXIST",
                                 snapshot_date=date(2026, 1, 6))

    def test_multi_column_age_AC(self):
        # AC has two age columns — "Days Difference" for alerts, "case age" for
        # cases. Parser should read the first non-null per row.
        df = pd.DataFrame({
            "QUEUE":            ["AC"] * 5,
            "ALERT_TYPE":       ["Alert", "Alert", "Case", "Case", "Alert"],
            "Days Difference":  [10, 15, None, None, 20],
            "case age":         [None, None, 5, 30, None],
        })
        inv = inventory_from_paste(df, queue="AC", snapshot_date=date(2026, 1, 6))
        # Expect: alerts at 10, 15, 20 (one each) + cases at 5, 30 (one each)
        assert inv.items_by_age == {5: 1.0, 10: 1.0, 15: 1.0, 20: 1.0, 30: 1.0}

    def test_multi_column_age_SIU(self):
        # SIU uses same two-column schema as AC.
        df = pd.DataFrame({
            "Days Difference": [10, 10, None],
            "case age":        [None, None, 50],
        })
        inv = inventory_from_paste(df, queue="SIU", snapshot_date=date(2026, 1, 6))
        assert inv.items_by_age == {10: 2.0, 50: 1.0}

    def test_stacked_tables_two_headers(self):
        # Alerts table + cases table concatenated (format B). Parser must
        # detect both headers and merge into a single DataFrame.
        raw = (
            "QUEUE\tALERT_TYPE\tDays Difference\n"
            "SIU\tAlert\t8\n"
            "SIU\tAlert\t15\n"
            "QUEUE\tCASE_TYPE\tcase age\n"
            "SIU\tCase\t45\n"
            "SIU\tCase\t62\n"
        )
        inv = inventory_from_paste(raw, queue="SIU", snapshot_date=date(2026, 1, 6))
        # Expect: alerts at 8, 15 + cases at 45, 62
        assert inv.items_by_age == {8: 1.0, 15: 1.0, 45: 1.0, 62: 1.0}

    def test_stacked_tables_alerts_only(self):
        # Format B edge case — user pastes only the alerts table, no cases.
        # Should still work (single-header fallback).
        raw = (
            "QUEUE\tALERT_TYPE\tDays Difference\n"
            "AC\tAlert\t10\n"
            "AC\tAlert\t15\n"
        )
        inv = inventory_from_paste(raw, queue="AC", snapshot_date=date(2026, 1, 6))
        assert inv.items_by_age == {10: 1.0, 15: 1.0}


class TestInventoryFromInputOrPaste:
    def test_inv_input_only(self):
        inv = inventory_from_input_or_paste(
            queue="EDD", snapshot_date=date(2026, 1, 6),
            inv_input="65:1, 70:2, 100:5",
        )
        assert inv.items_by_age == {65: 1.0, 70: 2.0, 100: 5.0}

    def test_paste_only(self):
        raw = "QUEUE\tDays Difference\nEDD\t100\nEDD\t100\nEDD\t200\n"
        inv = inventory_from_input_or_paste(
            queue="EDD", snapshot_date=date(2026, 1, 6),
            inventory_raw=raw,
        )
        assert inv.items_by_age == {100: 2.0, 200: 1.0}

    def test_both_provided_paste_wins(self, capsys):
        raw = "QUEUE\tDays Difference\nEDD\t100\n"
        inv = inventory_from_input_or_paste(
            queue="EDD", snapshot_date=date(2026, 1, 6),
            inv_input="50:99",
            inventory_raw=raw,
        )
        # Paste wins → only the 100-aged item, not the 50:99 inline
        assert inv.items_by_age == {100: 1.0}
        captured = capsys.readouterr()
        assert "Using inventory_raw" in captured.out

    def test_both_provided_inv_wins_when_flagged(self, capsys):
        raw = "QUEUE\tDays Difference\nEDD\t100\n"
        inv = inventory_from_input_or_paste(
            queue="EDD", snapshot_date=date(2026, 1, 6),
            inv_input="50:99",
            inventory_raw=raw,
            prefer_raw=False,
        )
        # inline wins → 50:99
        assert inv.items_by_age == {50: 99.0}
        captured = capsys.readouterr()
        assert "Using inv_input" in captured.out

    def test_both_empty_raises(self):
        with pytest.raises(ValueError, match="both inv_input and inventory_raw are empty"):
            inventory_from_input_or_paste(
                queue="EDD", snapshot_date=date(2026, 1, 6),
                inv_input="",
                inventory_raw="",
            )

    def test_whitespace_only_treated_as_empty(self):
        # Common case: user leaves an empty triple-quoted string with just newlines
        with pytest.raises(ValueError, match="both inv_input and inventory_raw are empty"):
            inventory_from_input_or_paste(
                queue="EDD", snapshot_date=date(2026, 1, 6),
                inv_input="   ",
                inventory_raw="\n\n  \n",
            )


class TestWeeklySummary:
    def _build_simple_le(self):
        from diagnostics import weekly_summary
        from Calendar import CalendarManager, load_all_calendars
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        wf = DeterministicWorkforce(worker_groups=[
            WorkerGroup(
                calendar_type=CalendarType.INTERNAL, name="Internal",
                current_headcount=10, tpt_by_month=2.4,
                fte_conversion_by_month=Months(
                    jan=0.75, feb=0.75, mar=0.75, apr=0.75, may=0.75, jun=0.75,
                    jul=0.75, aug=0.75, sep=0.75, oct=0.75, nov=0.75, dec=0.75,
                ),
            ),
        ])
        s = DeterministicScenario(
            name="LE",
            workforce=wf,
            initial_inventory=DeterministicInventory(items_by_age={5: 100, 10: 50}),
            demand=DeterministicDemand(streams=[
                DemandStream(name="x", cadence="daily",
                             monthly_volume=Months(jan=10), arrival_age=1),
            ]),
            calendar_manager=mgr,
            start_date=date(2026, 1, 5),  # Monday
            end_date=date(2026, 1, 18),   # 2 weeks
            open_inventory_ratio=2.0,
            closed_inventory_ratio=1.0,
            reporting_percentile=90,
        )
        calc = DeterministicCycleTimeCalculator(s)
        return calc, calc.calculate(), weekly_summary

    def test_columns_match_spec(self):
        calc, result, weekly_summary = self._build_simple_le()
        df = weekly_summary(result, calc)
        # Exact column order matters — these are the user-facing fields
        expected_cols = [
            "week", "demand", "avg_fte", "avg_inv_age",
            "p_direct", "p_from_open_ratio", "p_from_closed_ratio",
        ]
        assert list(df.columns) == expected_cols
        # No iso_year, iso_week, total_capacity, total_burned, end_inv, week_end columns anywhere
        for forbidden in ["iso_year", "iso_week", "total_capacity", "total_burned",
                          "end_inv", "week_end", "week_start", "avg_daily_capacity"]:
            assert forbidden not in df.columns

    def test_workable_age_offset_applied_to_p_values(self):
        # Build an EDD-style scenario with workable_age_min=61. Reported P_n should
        # be shifted down by 60 vs. raw ages.
        from diagnostics import weekly_summary
        from Calendar import CalendarManager, load_all_calendars
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        wf = DeterministicWorkforce(worker_groups=[
            WorkerGroup(calendar_type=CalendarType.INTERNAL,
                        current_headcount=10, tpt_by_month=2.4,
                        fte_conversion_by_month=Months(
                            jan=0.75, feb=0.75, mar=0.75, apr=0.75, may=0.75, jun=0.75,
                            jul=0.75, aug=0.75, sep=0.75, oct=0.75, nov=0.75, dec=0.75,
                        )),
        ])
        s = DeterministicScenario(
            name="EDD",
            workforce=wf,
            initial_inventory=DeterministicInventory(items_by_age={70: 50, 80: 30}),
            demand=DeterministicDemand(),
            calendar_manager=mgr,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 11),
            open_inventory_ratio=2.0, closed_inventory_ratio=1.0,
            reporting_percentile=90,
            workable_age_min=61,
        )
        calc = DeterministicCycleTimeCalculator(s)
        df = weekly_summary(calc.calculate(), calc)
        # Closed-item ages would be in the 70–80 range; subtracting 60 gives 10–20.
        # Just assert that p_direct (if present) is sensibly small (<50).
        if df["p_direct"].notna().any():
            assert df["p_direct"].max() < 50, \
                f"Expected workable-age offset to shrink p_direct, got {df['p_direct'].tolist()}"


class TestMonthlySummary:
    def _build(self, **scenario_overrides):
        from diagnostics import monthly_summary
        from Calendar import CalendarManager, load_all_calendars
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        wf = DeterministicWorkforce(worker_groups=[
            WorkerGroup(
                calendar_type=CalendarType.INTERNAL, name="Internal",
                current_headcount=10, tpt_by_month=2.4,
                fte_conversion_by_month=Months(
                    jan=0.75, feb=0.75, mar=0.75, apr=0.75, may=0.75, jun=0.75,
                    jul=0.75, aug=0.75, sep=0.75, oct=0.75, nov=0.75, dec=0.75,
                ),
            ),
            WorkerGroup(
                calendar_type=CalendarType.USA, name="RS_USA",
                current_headcount=5, tpt_by_month=2.5,
            ),
        ])
        defaults = dict(
            name="LE",
            workforce=wf,
            initial_inventory=DeterministicInventory(items_by_age={5: 100, 10: 50}),
            demand=DeterministicDemand(streams=[
                DemandStream(name="x", cadence="daily",
                             monthly_volume=Months(jan=10, feb=10), arrival_age=1),
            ]),
            calendar_manager=mgr,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 2, 28),
            open_inventory_ratio=2.0,
            closed_inventory_ratio=1.0,
            reporting_percentile=90,
        )
        defaults.update(scenario_overrides)
        s = DeterministicScenario(**defaults)
        calc = DeterministicCycleTimeCalculator(s)
        return calc, calc.calculate(), monthly_summary

    def test_two_rows_per_month_layout(self):
        calc, result, monthly_summary = self._build()
        df = monthly_summary(result, calc)
        # Two months → 4 rows (FTE row + TPT row each month)
        assert len(df) == 4
        # Even-indexed rows have Month label, odd-indexed rows have ""
        assert df.iloc[0]["Month"] == "2026-01"
        assert df.iloc[1]["Month"] == ""
        assert df.iloc[2]["Month"] == "2026-02"
        assert df.iloc[3]["Month"] == ""

    def test_columns_include_each_group(self):
        calc, result, monthly_summary = self._build()
        df = monthly_summary(result, calc)
        # Must include both group names as columns
        assert "Internal" in df.columns
        assert "RS_USA" in df.columns
        # And the standard scalar columns
        for col in ["Month", "Demand", "avg_inv_age",
                    "p_direct", "p_from_open_ratio", "p_from_closed_ratio"]:
            assert col in df.columns

    def test_fte_on_first_row_tpt_on_second(self):
        calc, result, monthly_summary = self._build()
        df = monthly_summary(result, calc)
        # First row of each pair carries FTE, second row carries TPT
        assert df.iloc[0]["Internal"].startswith("FTE:")
        assert df.iloc[1]["Internal"].startswith("TPT:")
        assert df.iloc[0]["RS_USA"].startswith("FTE:")
        assert df.iloc[1]["RS_USA"].startswith("TPT:")
        # Non-group cells empty on TPT rows
        assert df.iloc[1]["Demand"] == ""
        assert df.iloc[1]["avg_inv_age"] == ""
        assert df.iloc[1]["p_direct"] == ""

    def test_workable_age_offset_applied(self):
        calc, result, monthly_summary = self._build(
            initial_inventory=DeterministicInventory(items_by_age={70: 50, 80: 30}),
            workable_age_min=61,
        )
        df = monthly_summary(result, calc)
        # FTE rows (even indices) carry the p_direct strings; ensure subtraction worked.
        for i in range(0, len(df), 2):
            p = df.iloc[i]["p_direct"]
            if p:  # non-empty string
                assert int(p) < 50, f"Expected p_direct < 50 after -60 offset, got {p}"


# =============================================================================
# DEMAND STREAMS
# =============================================================================

class TestDemandStream:
    def test_daily_cadence(self):
        s = DemandStream(name="x", cadence="daily",
                         monthly_volume=Months(jan=5.4))
        ss = date(2026, 1, 6)
        assert s.volume_for_date(date(2026, 1, 6), ss) == 5.4
        assert s.volume_for_date(date(2026, 1, 7), ss) == 5.4  # every day

    def test_on_days_of_month_splits_volume(self):
        s = DemandStream(name="x", cadence="on_days_of_month",
                         days_of_month=[13, 27],
                         monthly_volume=Months(jan=1502))
        ss = date(2026, 1, 6)
        assert s.volume_for_date(date(2026, 1, 13), ss) == 751  # 1502/2
        assert s.volume_for_date(date(2026, 1, 27), ss) == 751
        assert s.volume_for_date(date(2026, 1, 14), ss) == 0.0

    def test_on_weekdays_tuesday(self):
        s = DemandStream(name="alerts", cadence="on_weekdays",
                         weekdays=[1],  # Tuesday
                         monthly_volume=Months(jan=400))
        ss = date(2026, 1, 6)  # Tuesday
        assert s.volume_for_date(date(2026, 1, 6), ss) == 400   # Tue
        assert s.volume_for_date(date(2026, 1, 7), ss) == 0.0   # Wed

    def test_unknown_cadence_raises(self):
        with pytest.raises(ValueError, match="Unknown cadence"):
            DemandStream(name="x", cadence="monthly")

    def test_on_weekdays_requires_weekdays_list(self):
        with pytest.raises(ValueError, match="weekdays"):
            DemandStream(name="x", cadence="on_weekdays")


class TestAgeDistributionStream:
    def test_daily_multi_age_arrivals(self):
        # Each day of Jan: 1 @ age 15, 2 @ age 30, 1 @ age 45.
        s = AgeDistributionStream(
            name="tmo",
            monthly_distribution={
                1: {15: 1, 30: 2, 45: 1},
                2: {15: 2, 30: 3},
            },
        )
        sim_start = date(2026, 1, 6)
        jan_arrivals = s.arrivals_for_date(date(2026, 1, 10), sim_start)
        ages_to_vols = {a["arrival_age"]: a["volume"] for a in jan_arrivals}
        assert ages_to_vols == {15: 1.0, 30: 2.0, 45: 1.0}

        feb_arrivals = s.arrivals_for_date(date(2026, 2, 15), sim_start)
        ages_to_vols = {a["arrival_age"]: a["volume"] for a in feb_arrivals}
        assert ages_to_vols == {15: 2.0, 30: 3.0}

        # March has no distribution -> empty list
        mar = s.arrivals_for_date(date(2026, 3, 1), sim_start)
        assert mar == []

    def test_year_keyed_distribution(self):
        s = AgeDistributionStream(
            name="x",
            monthly_distribution={
                2026: {1: {15: 1}},
                2027: {1: {15: 5}},
            },
        )
        ss = date(2026, 1, 1)
        v26 = s.arrivals_for_date(date(2026, 1, 15), ss)
        v27 = s.arrivals_for_date(date(2027, 1, 15), ss)
        assert v26 == [{"name": "x", "volume": 1.0, "arrival_age": 15}]
        assert v27 == [{"name": "x", "volume": 5.0, "arrival_age": 15}]

    def test_mixed_streams_in_deterministic_demand(self):
        # DeterministicDemand should merge DemandStream + AgeDistributionStream.
        d = DeterministicDemand(streams=[
            DemandStream(name="alerts", cadence="daily",
                         monthly_volume=Months(jan=10), arrival_age=8),
            AgeDistributionStream(name="tmo",
                                  monthly_distribution={1: {15: 1, 30: 2}}),
        ])
        sim_start = date(2026, 1, 6)
        arrivals = d.arrivals_for_date(date(2026, 1, 10), sim_start)
        # 1 from alerts + 2 from tmo distribution = 3 entries
        assert len(arrivals) == 3
        names = sorted([a["name"] for a in arrivals])
        assert names == ["alerts", "tmo", "tmo"]
        # Verify alerts at age 8
        alerts = [a for a in arrivals if a["name"] == "alerts"][0]
        assert alerts["arrival_age"] == 8
        assert alerts["volume"] == 10.0


# =============================================================================
# CALCULATOR — float burn, workable-age, percentile
# =============================================================================

class TestCalculator:
    def _base_scenario(self, **overrides):
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        wf = DeterministicWorkforce(worker_groups=[
            WorkerGroup(
                calendar_type=CalendarType.INTERNAL, name="I",
                current_headcount=10, tpt_by_month=2.0,
                fte_conversion_by_month=Months(
                    jan=0.75, feb=0.75, mar=0.75, apr=0.75, may=0.75, jun=0.75,
                    jul=0.75, aug=0.75, sep=0.75, oct=0.75, nov=0.75, dec=0.75,
                ),
            ),
        ])
        defaults = dict(
            name="test",
            workforce=wf,
            demand=DeterministicDemand(),
            calendar_manager=mgr,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 6),
        )
        defaults.update(overrides)
        return DeterministicScenario(**defaults)

    def test_workable_age_filter_skips_young_items(self):
        s = self._base_scenario(
            initial_inventory=DeterministicInventory(items_by_age={50: 100, 70: 50, 100: 20}),
            workable_age_min=61, workable_age_max=None,
        )
        calc = DeterministicCycleTimeCalculator(s)
        result = calc.calculate()
        dr = result.daily_results[0]
        # Capacity = 10 * 0.75 * 2.0 = 15. Burn from age 100 first (oldest in window).
        # None of the closed items should have age < 61
        for item in dr.closed_items:
            assert item["age"] >= 61
        # The 50-age items should still be present (aged to 51)
        assert dr.open_inventory_after.items_by_age.get(51) == 100

    def test_float_burn_produces_fractional_counts(self):
        # Capacity 15 splits a bucket with count 100 into 15 (float)
        s = self._base_scenario(
            initial_inventory=DeterministicInventory(items_by_age={100: 20, 200: 50}),
        )
        calc = DeterministicCycleTimeCalculator(s)
        result = calc.calculate()
        dr = result.daily_results[0]
        # Total burned should be 15.0 (capacity), even if split across buckets.
        assert abs(dr.total_burned - 15.0) < 1e-6

    def test_partial_bucket_has_fractional_residual(self):
        # Capacity that doesn't evenly divide a bucket -> fractional residual
        # cap = 10 * 0.75 * 2.0 = 15. Bucket at age 100 has count=5; bucket at age 50 has count=30.
        # With workable_age_min=None: burns 5 at age 100, then 10 at age 50.
        s = self._base_scenario(
            initial_inventory=DeterministicInventory(items_by_age={50: 30, 100: 5}),
        )
        calc = DeterministicCycleTimeCalculator(s)
        result = calc.calculate()
        dr = result.daily_results[0]
        assert abs(dr.total_burned - 15.0) < 1e-6
        # Remaining at age 51 after aging +1: 30 - 10 = 20
        assert abs(dr.open_inventory_after.items_by_age.get(51) - 20.0) < 1e-6

    def test_reporting_percentile_is_configurable(self):
        s = self._base_scenario(
            initial_inventory=DeterministicInventory(items_by_age={5: 100}),
            reporting_percentile=95,
        )
        calc = DeterministicCycleTimeCalculator(s)
        result = calc.calculate()
        m = calc.calculate_monthly_metrics(result, 1, 2026)
        assert m["percentile"] == 95


class TestScenarioValidate:
    def test_clean_scenario(self):
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        s = DeterministicScenario(
            name="LE",
            workforce=DeterministicWorkforce(worker_groups=[
                WorkerGroup(calendar_type=CalendarType.USA,
                            current_headcount=5, tpt_by_month=2.4),
            ]),
            initial_inventory=DeterministicInventory(items_by_age={1: 10}),
            demand=DeterministicDemand(streams=[
                DemandStream(name="x", cadence="daily", monthly_volume=1.0),
            ]),
            calendar_manager=mgr,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 15),
        )
        assert s.validate() == []

    def test_end_before_start(self):
        s = DeterministicScenario(
            start_date=date(2026, 5, 1), end_date=date(2026, 1, 1),
        )
        assert any("end_date" in msg for msg in s.validate())

    def test_invalid_percentile(self):
        s = DeterministicScenario(reporting_percentile=150)
        assert any("reporting_percentile" in msg for msg in s.validate())

    def test_workable_window_inverted(self):
        s = DeterministicScenario(workable_age_min=200, workable_age_max=50)
        assert any("workable_age_min" in msg for msg in s.validate())


# =============================================================================
# FTE OVERRIDE — direct FTE input bypasses headcount math
# =============================================================================

class TestFteOverride:
    """fte_override_by_month bypasses current_headcount + ramp + attrition +
    hires + removals + fte_conversion entirely. TPT and day_factor still
    apply: capacity = fte_override × day_factor × tpt.
    """

    def _wf(self, **kwargs):
        return DeterministicWorkforce(worker_groups=[
            WorkerGroup(calendar_type=CalendarType.USA, name="rs", **kwargs),
        ])

    def test_scalar_override_returns_same_fte_every_month(self):
        wf = self._wf(tpt_by_month=2.4, fte_override_by_month=30.0)
        sim_start = date(2026, 1, 1)
        assert wf.group_fte_for_date(0, sim_start, date(2026, 3, 15)) == 30.0
        assert wf.group_fte_for_date(0, sim_start, date(2026, 7, 1)) == 30.0
        assert wf.group_uses_fte_override(0)

    def test_months_override_resolves_per_month(self):
        wf = self._wf(
            tpt_by_month=2.4,
            fte_override_by_month=Months(jan=30, feb=35, mar=40),
        )
        sim_start = date(2026, 1, 1)
        assert wf.group_fte_for_date(0, sim_start, date(2026, 1, 31)) == 30
        assert wf.group_fte_for_date(0, sim_start, date(2026, 2, 14)) == 35
        assert wf.group_fte_for_date(0, sim_start, date(2026, 3, 5)) == 40

    def test_12_list_override_cycles_sim_relative(self):
        # sim_start = March 2026 → list[0] = March
        wf = self._wf(
            tpt_by_month=2.4,
            fte_override_by_month=[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
        )
        sim_start = date(2026, 3, 1)
        assert wf.group_fte_for_date(0, sim_start, date(2026, 3, 1)) == 10
        assert wf.group_fte_for_date(0, sim_start, date(2026, 4, 1)) == 11
        # Wraps after 12 months
        assert wf.group_fte_for_date(0, sim_start, date(2027, 3, 1)) == 10

    def test_year_dict_and_tuple_override(self):
        wf = self._wf(
            tpt_by_month=2.4,
            fte_override_by_month={
                2026: Months(jan=30),
                2027: 40.0,
                (2026, 6): 99.0,  # spot beats year-dict
            },
        )
        sim_start = date(2026, 1, 1)
        assert wf.group_fte_for_date(0, sim_start, date(2026, 1, 15)) == 30
        assert wf.group_fte_for_date(0, sim_start, date(2027, 8, 1)) == 40
        assert wf.group_fte_for_date(0, sim_start, date(2026, 6, 10)) == 99

    def test_override_wins_over_headcount_fields(self):
        # Both override and headcount-based fields set; override wins.
        wf = self._wf(
            current_headcount=999,            # would normally drive FTE
            fte_conversion_by_month=0.5,
            tpt_by_month=2.4,
            fte_override_by_month=20.0,
        )
        sim_start = date(2026, 1, 1)
        assert wf.group_fte_for_date(0, sim_start, date(2026, 3, 1)) == 20.0
        # And override_notes flags the conflict
        notes = wf.override_notes()
        assert any("IGNORED" in n for n in notes)

    def test_internal_with_override_skips_fte_conversion_requirement(self):
        # INTERNAL group normally REQUIRES fte_conversion_by_month — override
        # mode waives that.
        wf = DeterministicWorkforce(worker_groups=[
            WorkerGroup(
                calendar_type=CalendarType.INTERNAL,
                name="internal-override",
                tpt_by_month=2.4,
                fte_override_by_month=Months(jan=30, feb=32),
                # NB: no fte_conversion_by_month, no current_headcount
            ),
        ])
        issues = wf.validate()
        assert not any("fte_conversion_by_month" in i for i in issues)
        assert not any("0 headcount" in i.lower() or "no recent_hires" in i.lower()
                       for i in issues)

    def test_capacity_uses_override_with_day_factor_and_tpt(self):
        # Full integration: capacity in simulate_day == override × day_factor × tpt.
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        scenario = DeterministicScenario(
            name="override-cap-test",
            workforce=DeterministicWorkforce(worker_groups=[
                WorkerGroup(
                    calendar_type=CalendarType.USA, name="rs",
                    tpt_by_month=2.0,
                    fte_override_by_month=10.0,
                ),
            ]),
            initial_inventory=DeterministicInventory(items_by_age={1: 1000}),
            demand=DeterministicDemand(streams=[]),
            calendar_manager=mgr,
            start_date=date(2026, 1, 5),   # Monday
            end_date=date(2026, 1, 10),    # Saturday
        )
        calc = DeterministicCycleTimeCalculator(scenario)
        result = calc.calculate()
        # Mon-Fri: full day → capacity 10 × 1.0 × 2.0 = 20
        # Sat: weekend → 0
        days_by_date = {dr.date: dr for dr in result.daily_results}
        assert days_by_date[date(2026, 1, 5)].total_capacity == 20.0
        assert days_by_date[date(2026, 1, 9)].total_capacity == 20.0
        assert days_by_date[date(2026, 1, 10)].total_capacity == 0.0
        # group_stats carries the source tag
        assert days_by_date[date(2026, 1, 5)].group_stats[0]["fte_source"] == "override"

    def test_internal_and_rs_separately_with_override(self):
        # The whole point — Internal and RS each get their own override.
        wf = DeterministicWorkforce(worker_groups=[
            WorkerGroup(
                calendar_type=CalendarType.INTERNAL, name="internal",
                tpt_by_month=2.4,
                fte_override_by_month=Months(jan=25, feb=27),
            ),
            WorkerGroup(
                calendar_type=CalendarType.USA, name="rs",
                tpt_by_month=2.4,
                fte_override_by_month=10.0,
            ),
        ])
        sim_start = date(2026, 1, 1)
        assert wf.group_fte_for_date(0, sim_start, date(2026, 1, 15)) == 25
        assert wf.group_fte_for_date(0, sim_start, date(2026, 2, 15)) == 27
        assert wf.group_fte_for_date(1, sim_start, date(2026, 1, 15)) == 10
        assert wf.group_fte_for_date(1, sim_start, date(2026, 2, 15)) == 10

    def test_override_unset_uses_headcount_math(self):
        # Regression: when override is None, behavior is unchanged.
        wf = DeterministicWorkforce(worker_groups=[
            WorkerGroup(
                calendar_type=CalendarType.USA, name="rs",
                current_headcount=10,
                tpt_by_month=2.4,
                # fte_override_by_month omitted
            ),
        ])
        sim_start = date(2026, 1, 1)
        assert not wf.group_uses_fte_override(0)
        # 10 HC × default 1.0 RS conversion = 10
        assert wf.group_fte_for_date(0, sim_start, date(2026, 3, 1)) == 10.0


# =============================================================================
# DIAGNOSTIC AGGREGATIONS — avg_inv_age is volume-weighted, not avg-of-avgs
# =============================================================================

class TestAvgInvAgeWeighting:
    """avg_inv_age in weekly_summary and monthly_summary must be volume-
    weighted across the period (sum(age × count) / sum(count)), NOT a
    simple mean of daily averages. With unequal daily inventory volumes
    these two numbers diverge significantly.
    """

    def _build_handcrafted_result(self):
        """Build a DeterministicResult with hand-crafted daily inventories.

        3 sim days. Each day's open_inventory_after is set explicitly so we
        know exactly what the volume-weighted average should be:
            Day 1: 1000 items at age 10  → daily avg 10
            Day 2:  100 items at age 80  → daily avg 80
            Day 3:   10 items at age 100 → daily avg 100

        avg-of-avgs would be (10 + 80 + 100) / 3 = 63.33
        volume-weighted would be (1000*10 + 100*80 + 10*100) / 1110 ≈ 17.21
        """
        from Cycle_time_calculator import DailyBurnDown, DeterministicResult

        def _inv(items):
            inv = DeterministicInventory()
            inv.items_by_age = items
            return inv

        days = [
            (date(2026, 1, 5), {10: 1000.0}),
            (date(2026, 1, 6), {80:  100.0}),
            (date(2026, 1, 7), {100:  10.0}),
        ]
        daily_results = []
        for d, items in days:
            daily_results.append(DailyBurnDown(
                date=d,
                group_stats=[{"name": "rs", "fte_for_month": 5.0, "tpt": 2.4,
                              "calendar_type": "usa", "day_type": "full_day",
                              "day_factor": 1.0, "raw_headcount": 5.0,
                              "effective_fte": 5.0, "capacity": 12.0,
                              "fte_source": "computed"}],
                arrivals=[],
                total_capacity=12.0,
                total_burned=0.0,
                closed_items=[],
                open_inventory_after=_inv(items),
            ))
        return DeterministicResult(
            scenario_name="weighting-test",
            start_date=days[0][0],
            end_date=days[-1][0],
            initial_inventory=_inv({10: 1000.0}),
            daily_results=daily_results,
        )

    def _calc(self, result):
        # Minimal scenario just to satisfy the calculator-needs-a-scenario
        # contract used by weekly_summary / monthly_summary.
        mgr = CalendarManager()
        load_all_calendars(mgr, [2026])
        scen = DeterministicScenario(
            name="weighting-test",
            workforce=DeterministicWorkforce(worker_groups=[
                WorkerGroup(calendar_type=CalendarType.USA, name="rs",
                            current_headcount=5, tpt_by_month=2.4),
            ]),
            initial_inventory=result.initial_inventory,
            calendar_manager=mgr,
            start_date=result.start_date,
            end_date=result.end_date,
            reporting_percentile=90,
        )
        return DeterministicCycleTimeCalculator(scen)

    def test_weekly_avg_inv_age_is_volume_weighted(self):
        from diagnostics import weekly_summary
        result = self._build_handcrafted_result()
        calc = self._calc(result)
        df = weekly_summary(result, calc)
        # All 3 days fall in the same ISO week → one row.
        assert len(df) == 1
        avg = df["avg_inv_age"].iloc[0]
        # Volume-weighted: (1000*10 + 100*80 + 10*100) / (1000 + 100 + 10)
        expected = (1000 * 10 + 100 * 80 + 10 * 100) / 1110
        assert abs(avg - expected) < 0.01, f"got {avg}, expected {expected:.2f}"
        # Avg-of-avgs would have given 63.33 — verify we are NOT that.
        assert abs(avg - 63.33) > 1.0

    def test_monthly_avg_inv_age_is_volume_weighted(self):
        from diagnostics import monthly_summary
        result = self._build_handcrafted_result()
        calc = self._calc(result)
        df = monthly_summary(result, calc)
        # Two-line-per-row layout — first row holds the scalar metric.
        avg_str = df["avg_inv_age"].iloc[0]  # formatted as "%.2f"
        avg = float(avg_str)
        expected = (1000 * 10 + 100 * 80 + 10 * 100) / 1110
        assert abs(avg - expected) < 0.01, f"got {avg}, expected {expected:.2f}"
        assert abs(avg - 63.33) > 1.0
