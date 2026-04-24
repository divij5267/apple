from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple
import math
import numpy as np

from workforce import DeterministicWorkforce, WorkerGroup
from inventory import DeterministicInventory, DeterministicDemand
from Calendar import CalendarManager, CalendarType


# =============================================================================
# DAILY BURN-DOWN — one entry per simulated day
# =============================================================================

@dataclass
class DailyBurnDown:
    date: date
    group_stats: List[Dict[str, Any]] = field(default_factory=list)
    arrivals: List[Dict[str, Any]] = field(default_factory=list)
    total_capacity: float = 0.0
    total_burned: float = 0.0
    closed_items: List[Dict[str, float]] = field(default_factory=list)
    open_inventory_after: DeterministicInventory = field(default_factory=DeterministicInventory)

    def get_closed_items_ages(self) -> List[int]:
        """Return list of ages (one per closed item) for percentile computation.

        Since item counts are float (fractional residuals from partial-day work),
        we round to nearest int when expanding. Acceptable approximation: over a
        month, fractional residuals amount to at most a handful of rounded units.
        """
        ages: List[int] = []
        for item in self.closed_items:
            n = int(round(float(item["count"])))
            if n > 0:
                ages.extend([item["age"]] * n)
        return ages


@dataclass
class DeterministicResult:
    scenario_name: str
    start_date: date
    end_date: date
    initial_inventory: DeterministicInventory
    daily_results: List[DailyBurnDown] = field(default_factory=list)

    def get_all_closed_ages(self) -> List[int]:
        all_ages: List[int] = []
        for daily in self.daily_results:
            all_ages.extend(daily.get_closed_items_ages())
        return all_ages

    @property
    def total_items_closed(self) -> int:
        return sum(dr.total_burned for dr in self.daily_results)

    @property
    def final_inventory(self) -> Optional[DeterministicInventory]:
        if self.daily_results:
            return self.daily_results[-1].open_inventory_after
        return None

    def get_open_inventory_by_date(self, target_date: date) -> Optional[DeterministicInventory]:
        for r in self.daily_results:
            if r.date == target_date:
                return r.open_inventory_after
        return None


# =============================================================================
# SCENARIO — now carries workable-age window + reporting percentile per queue
# =============================================================================

@dataclass
class DeterministicScenario:
    name: str = ""
    description: str = ""
    workforce: DeterministicWorkforce = field(default_factory=DeterministicWorkforce)
    initial_inventory: DeterministicInventory = field(default_factory=DeterministicInventory)
    demand: DeterministicDemand = field(default_factory=DeterministicDemand)
    calendar_manager: CalendarManager = field(default_factory=CalendarManager)
    start_date: date = field(default_factory=date.today)
    end_date: date = field(default_factory=lambda: date.today() + timedelta(days=365))

    # Reporting
    open_inventory_ratio: float = 1.919
    closed_inventory_ratio: float = 0.961
    reporting_percentile: int = 90
    # LE, SIU = 90; EDD, AC = 95; Fraud, ML TBD.

    # Workable-age window — items outside this range age each day but never
    # absorb capacity. None on either side = unbounded.
    #   LE:    min=None, max=None  (everything workable)
    #   EDD:   min=61,   max=None  (items <61 sit until they age past 60)
    workable_age_min: Optional[int] = None
    workable_age_max: Optional[int] = None

    # -------------------------------------------------------------------------
    # Pre-run validation — checks calendar + workforce + inventory + demand +
    # reporting config are internally consistent. Returns list of issue
    # strings (empty = good to run). Call before calculate().
    # -------------------------------------------------------------------------

    def validate(self) -> List[str]:
        issues: List[str] = []

        # Sim period
        if self.end_date < self.start_date:
            issues.append(
                f"end_date {self.end_date} is before start_date {self.start_date}."
            )

        # Workforce
        issues.extend(self.workforce.validate())
        if not self.workforce.worker_groups:
            issues.append("workforce has no worker_groups — sim will produce 0 capacity.")

        # Inventory
        if not self.initial_inventory.items_by_age:
            issues.append(
                "initial_inventory is empty — nothing to burn down (demand "
                "arrivals during the sim will be the only inventory)."
            )
        for age, count in self.initial_inventory.items_by_age.items():
            if age < 0:
                issues.append(f"initial_inventory has negative age bucket {age}.")
            if count < 0:
                issues.append(f"initial_inventory has negative count at age {age}: {count}.")

        # Demand
        if not self.demand.streams:
            issues.append(
                "demand has no streams — sim will run with zero arrivals "
                "(capacity will only burn the initial inventory)."
            )

        # Workable-age window
        if (
            self.workable_age_min is not None
            and self.workable_age_max is not None
            and self.workable_age_min > self.workable_age_max
        ):
            issues.append(
                f"workable_age_min ({self.workable_age_min}) > workable_age_max "
                f"({self.workable_age_max}) — no items would ever be workable."
            )

        # Reporting
        if not (0 < self.reporting_percentile <= 100):
            issues.append(
                f"reporting_percentile must be in (0, 100]; got {self.reporting_percentile}."
            )

        # Calendar — sanity that all worker groups' calendars are loaded
        for g in self.workforce.worker_groups:
            cal = self.scenario_calendar(g.calendar_type)
            if cal is None:
                issues.append(
                    f"Calendar {g.calendar_type.value!r} (used by group "
                    f"{g.name or '(unnamed)'}) is not loaded in calendar_manager."
                )

        return issues

    def scenario_calendar(self, cal_type) -> Optional["OperationsCalendar"]:
        try:
            return self.calendar_manager.get_calendar(cal_type)
        except Exception:
            return None

    def print_validate(self) -> bool:
        """Print validation results. Returns True if clean, False if issues."""
        issues = self.validate()
        if not issues:
            print(f"✅ Scenario {self.name!r} validates clean.")
            return True
        print(f"⚠️  Scenario {self.name!r} has {len(issues)} issue(s):")
        for i, msg in enumerate(issues, 1):
            print(f"   {i}. {msg}")
        return False


# =============================================================================
# CALCULATOR
# =============================================================================

class DeterministicCycleTimeCalculator:
    def __init__(self, scenario: DeterministicScenario):
        self.scenario = scenario

    # -------------------------------------------------------------------------
    # Burn-down — FIFO within the workable-age window
    # -------------------------------------------------------------------------

    def burn_down_inventory_fifo(
        self,
        inventory: DeterministicInventory,
        capacity: float,
    ) -> Tuple[List[Dict[str, float]], DeterministicInventory]:
        """Burn oldest items first within the workable-age window.

        Counts are FLOAT throughout — items can be partially processed across
        days (capacity 95.5 closes 95 + 0.5; the 0.5 remains a fractional count
        until completed by later capacity).
        """
        closed_items: List[Dict[str, float]] = []
        remaining_capacity = float(capacity)
        remaining_inventory = DeterministicInventory(snapshot_date=inventory.snapshot_date)

        age_min = self.scenario.workable_age_min
        age_max = self.scenario.workable_age_max

        def in_window(age: int) -> bool:
            if age_min is not None and age < age_min:
                return False
            if age_max is not None and age > age_max:
                return False
            return True

        # Sort oldest-first — FIFO within workable window.
        items_sorted = sorted(inventory.items_by_age.items(), key=lambda x: x[0], reverse=True)

        for age, count in items_sorted:
            count = float(count)
            if not in_window(age):
                remaining_inventory.items_by_age[age] = count
                continue
            if remaining_capacity <= 0:
                remaining_inventory.items_by_age[age] = count
                continue
            if count <= remaining_capacity:
                # Bucket fully processed.
                closed_items.append({"age": age, "count": count})
                remaining_capacity -= count
            else:
                # Partial: close `remaining_capacity` float items; leftover stays.
                processed = remaining_capacity           # float, no truncation
                closed_items.append({"age": age, "count": processed})
                remaining_inventory.items_by_age[age] = count - processed
                remaining_capacity = 0.0

        return closed_items, remaining_inventory

    # -------------------------------------------------------------------------
    # Per-day simulation
    # -------------------------------------------------------------------------

    def simulate_day(
        self,
        current_date: date,
        inventory_at_start: DeterministicInventory,
    ) -> DailyBurnDown:
        # 1) Add today's demand arrivals (each stream uses its own arrival_age)
        #    Volumes are float — no int() cast (fractional daily demand like
        #    314(a) at 5.4/day should accumulate as 5.4, not be truncated to 5).
        inventory_with_demand = inventory_at_start.copy()
        arrivals = self.scenario.demand.arrivals_for_date(
            current_date, self.scenario.start_date,
        )
        for a in arrivals:
            inventory_with_demand.add_items(age_days=a["arrival_age"], count=a["volume"])

        # 2) Per-worker-group capacity
        total_capacity = 0.0
        group_stats: List[Dict[str, Any]] = []
        workforce = self.scenario.workforce
        sim_start = self.scenario.start_date

        for i, group in enumerate(workforce.worker_groups):
            cal = self.scenario.calendar_manager.get_calendar(group.calendar_type)
            day_type, day_factor = cal.get_day_type(current_date)

            raw_headcount = workforce.raw_headcount(i, sim_start, current_date)
            fte_for_month = workforce.group_fte_for_date(i, sim_start, current_date)
            effective_fte = fte_for_month * day_factor
            tpt = workforce.group_tpt_for_date(i, sim_start, current_date)
            capacity = effective_fte * tpt

            total_capacity += capacity
            group_stats.append({
                "name": group.name or group.calendar_type.value,
                "calendar_type": group.calendar_type.value,
                "day_type": day_type.value,
                "day_factor": day_factor,
                "raw_headcount": raw_headcount,
                "fte_for_month": fte_for_month,
                "effective_fte": effective_fte,
                "tpt": tpt,
                "capacity": capacity,
            })

        # 3) FIFO burn-down (workable-window filtered) then age +1
        closed_items, open_inventory = self.burn_down_inventory_fifo(
            inventory_with_demand, total_capacity,
        )
        open_inventory_aged = open_inventory.age_inventory(1)
        open_inventory_aged.snapshot_date = current_date

        return DailyBurnDown(
            date=current_date,
            group_stats=group_stats,
            arrivals=arrivals,
            total_capacity=total_capacity,
            total_burned=sum(item["count"] for item in closed_items),
            closed_items=closed_items,
            open_inventory_after=open_inventory_aged,
        )

    # -------------------------------------------------------------------------
    # Full sim
    # -------------------------------------------------------------------------

    def calculate(self) -> DeterministicResult:
        result = DeterministicResult(
            scenario_name=self.scenario.name,
            start_date=self.scenario.start_date,
            end_date=self.scenario.end_date,
            initial_inventory=self.scenario.initial_inventory.copy(),
        )
        current_inventory = self.scenario.initial_inventory.copy()
        current_date = self.scenario.start_date
        while current_date <= self.scenario.end_date:
            dr = self.simulate_day(current_date, current_inventory)
            result.daily_results.append(dr)
            current_inventory = dr.open_inventory_after
            current_date += timedelta(days=1)
        return result

    # -------------------------------------------------------------------------
    # Monthly P_n metrics (three methods) — n = scenario.reporting_percentile
    # -------------------------------------------------------------------------

    def calculate_monthly_metrics(
        self,
        result: DeterministicResult,
        target_month: int,
        target_year: int,
    ) -> Dict[str, Optional[float]]:
        p = self.scenario.reporting_percentile
        month_daily = [
            dr for dr in result.daily_results
            if dr.date.month == target_month and dr.date.year == target_year
        ]
        if not month_daily:
            return {
                "p_direct": None,
                "max_avg_open_age": None,
                "max_avg_closed_age": None,
                "p_from_open_ratio": None,
                "p_from_closed_ratio": None,
                "percentile": p,
            }

        all_closed_ages: List[int] = []
        daily_avg_open: List[float] = []
        daily_avg_closed: List[float] = []

        for dr in month_daily:
            all_closed_ages.extend(dr.get_closed_items_ages())
            daily_avg_open.append(dr.open_inventory_after.calculate_average_age())
            closed_ages = dr.get_closed_items_ages()
            daily_avg_closed.append(float(np.mean(closed_ages)) if closed_ages else 0.0)

        # Reported cycle-time days are always whole days, rounded UP (ceiling).
        # e.g. 70.5666 -> 71. Underlying averages (max_open, max_closed) stay
        # as floats for diagnostic visibility.
        def _ceil_int(x):
            return int(math.ceil(x)) if x is not None else None

        p_direct_raw = (
            float(np.percentile(all_closed_ages, p)) if all_closed_ages else None
        )
        max_open = max(daily_avg_open) if daily_avg_open else None
        p_from_open_raw = (
            max_open * self.scenario.open_inventory_ratio if max_open is not None else None
        )
        max_closed = max(daily_avg_closed) if daily_avg_closed else None
        p_from_closed_raw = (
            max_closed * self.scenario.closed_inventory_ratio if max_closed is not None else None
        )

        p_direct = _ceil_int(p_direct_raw)
        p_from_open = _ceil_int(p_from_open_raw)
        p_from_closed = _ceil_int(p_from_closed_raw)

        return {
            "p_direct": p_direct,
            "max_avg_open_age": max_open,
            "max_avg_closed_age": max_closed,
            "p_from_open_ratio": p_from_open,
            "p_from_closed_ratio": p_from_closed,
            "percentile": p,
        }
