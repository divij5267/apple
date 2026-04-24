from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

from workforce import MonthlyInput, resolve_monthly


# =============================================================================
# DETERMINISTIC INVENTORY
# =============================================================================
# Items bucketed by age in days. add_items / age_inventory / FIFO-friendly.
# The workable-age window (if any) is enforced at burn-down time by the
# calculator, not here — this dataclass tracks every item regardless.
# =============================================================================

@dataclass
class DeterministicInventory:
    # Counts are float: items can be partially processed across days (capacity
    # 95.5 → processes 95 + 0.5 items; the 0.5 remains as a fractional count
    # until the next day completes it). No int() truncation anywhere.
    items_by_age: Dict[int, float] = field(default_factory=dict)
    snapshot_date: date = field(default_factory=date.today)

    def add_items(self, age_days: int, count: float) -> None:
        if count <= 0:
            return
        self.items_by_age[age_days] = self.items_by_age.get(age_days, 0.0) + float(count)

    def get_total_items(self) -> float:
        return float(sum(self.items_by_age.values()))

    def get_items_by_age_range(self, min_age: int, max_age: int) -> float:
        return float(sum(
            count for age, count in self.items_by_age.items()
            if min_age <= age <= max_age
        ))

    def age_inventory(self, days: int = 1) -> "DeterministicInventory":
        aged = DeterministicInventory(snapshot_date=self.snapshot_date + timedelta(days=days))
        for age, count in self.items_by_age.items():
            aged.items_by_age[age + days] = count
        return aged

    def calculate_average_age(self) -> float:
        if not self.items_by_age:
            return 0.0
        total = 0
        weighted = 0.0
        for age, count in self.items_by_age.items():
            total += count
            weighted += age * count
        return weighted / total if total else 0.0

    def copy(self) -> "DeterministicInventory":
        new_inv = DeterministicInventory(snapshot_date=self.snapshot_date)
        new_inv.items_by_age = self.items_by_age.copy()
        return new_inv


# =============================================================================
# DEMAND STREAMS
# =============================================================================
# A queue's demand is a list of independently-arriving streams. Each stream
# has its own cadence, volume, and arrival age. Examples:
#
#   LE:   [subpoena on days 13 & 27, 314(a) daily]
#   EDD:  [alerts every Tuesday at age 1]
#   SIU:  TBD
#
# Cadences:
#   "daily"            — monthly_volume[m] is the per-DAY rate (every cal day)
#   "on_days_of_month" — monthly_volume[m] is the TOTAL monthly volume, split
#                         equally across days_of_month (e.g. [13, 27])
#   "on_weekdays"      — monthly_volume[m] is per-OCCURRENCE (per Tuesday, etc.)
#                         arrives on each occurrence of weekdays[]
#
# Weekday integers follow Python convention: 0=Mon, 1=Tue, 2=Wed, 3=Thu,
# 4=Fri, 5=Sat, 6=Sun.
# =============================================================================

VALID_CADENCES = {"daily", "on_days_of_month", "on_weekdays"}


@dataclass
class DemandStream:
    name: str
    cadence: str
    monthly_volume: MonthlyInput = 0.0
    days_of_month: List[int] = field(default_factory=list)
    weekdays: List[int] = field(default_factory=list)
    arrival_age: int = 1

    def __post_init__(self) -> None:
        if self.cadence not in VALID_CADENCES:
            raise ValueError(
                f"Unknown cadence {self.cadence!r} on DemandStream {self.name!r}. "
                f"Valid: {sorted(VALID_CADENCES)}"
            )
        if self.cadence == "on_days_of_month" and not self.days_of_month:
            raise ValueError(
                f"DemandStream {self.name!r} cadence=on_days_of_month requires "
                "days_of_month (e.g. [13, 27])."
            )
        if self.cadence == "on_weekdays" and not self.weekdays:
            raise ValueError(
                f"DemandStream {self.name!r} cadence=on_weekdays requires "
                "weekdays (e.g. [1] for Tuesday; 0=Mon..6=Sun)."
            )

    def volume_for_date(self, target_date: date, sim_start: date) -> float:
        """Volume arriving on `target_date` from this stream (0 if no arrival)."""
        if self.cadence == "daily":
            return resolve_monthly(
                self.monthly_volume, target_date.year, target_date.month,
                sim_start, default=0.0,
            )

        if self.cadence == "on_days_of_month":
            if target_date.day not in self.days_of_month:
                return 0.0
            monthly_total = resolve_monthly(
                self.monthly_volume, target_date.year, target_date.month,
                sim_start, default=0.0,
            )
            return monthly_total / len(self.days_of_month)

        if self.cadence == "on_weekdays":
            if target_date.weekday() not in self.weekdays:
                return 0.0
            # Per-occurrence volume (not monthly total).
            return resolve_monthly(
                self.monthly_volume, target_date.year, target_date.month,
                sim_start, default=0.0,
            )

        return 0.0


    # ---- Unified interface used by DeterministicDemand -----------------------
    def arrivals_for_date(self, target_date: date, sim_start: date) -> List[Dict]:
        """Return a list (0 or 1 entries) of today's arrival records.

        Unified interface so DeterministicDemand can mix DemandStream and
        AgeDistributionStream in its `streams` list without caring which is which.
        """
        vol = self.volume_for_date(target_date, sim_start)
        if vol <= 0:
            return []
        return [{"name": self.name, "volume": vol, "arrival_age": self.arrival_age}]


# =============================================================================
# AGE-DISTRIBUTION STREAM
# =============================================================================
# Demand that arrives every day spread across multiple ages. Each day in a
# given calendar month, a distribution of items at various ages is added to
# inventory.
#
# Example — SIU TMO-referred cases:
#   monthly_distribution = {
#       1: {15: 1, 30: 2, 45: 1},   # Jan: every day — 1 item @ age 15, 2 @ 30, 1 @ 45
#       2: {15: 2, 30: 3},           # Feb: every day — 2 @ 15, 3 @ 30
#       ...
#   }
#
# `monthly_distribution` supports the same year/month/tuple keying as MonthlyInput:
#   {month:       {age: count_per_day}}        — calendar-month keys, every year
#   {year:        {month: {age: count}}}       — year-keyed
#   {(year, mo):  {age: count}}                — (year, month) tuple overrides
# =============================================================================

@dataclass
class AgeDistributionStream:
    name: str
    monthly_distribution: Dict = field(default_factory=dict)

    def arrivals_for_date(self, target_date: date, sim_start: date) -> List[Dict]:
        dist = self._resolve(target_date.year, target_date.month)
        if not dist:
            return []
        out: List[Dict] = []
        for age, count in dist.items():
            if count is None or count <= 0:
                continue
            out.append({
                "name": self.name,
                "volume": float(count),
                "arrival_age": int(age),
            })
        return out

    def _resolve(self, year: int, month: int) -> Dict[int, float]:
        v = self.monthly_distribution
        if not isinstance(v, dict):
            return {}
        # Highest precedence: (year, month) tuple override
        if (year, month) in v:
            return v[(year, month)] or {}
        # Year-keyed dict where inner is a month->distribution map
        if year in v and isinstance(v[year], dict):
            inner = v[year]
            if month in inner and isinstance(inner[month], dict):
                return inner[month]
        # Calendar-month keyed (applies every year)
        if month in v and isinstance(v[month], dict):
            return v[month]
        return {}


@dataclass
class DeterministicDemand:
    streams: List = field(default_factory=list)
    # Mixed list of DemandStream and AgeDistributionStream (and any future
    # stream types that implement arrivals_for_date(date, sim_start) -> List[Dict]).

    def arrivals_for_date(self, target_date: date, sim_start: date) -> List[Dict]:
        """Return [{'name', 'volume', 'arrival_age'}, ...] across all streams."""
        out: List[Dict] = []
        for s in self.streams:
            out.extend(s.arrivals_for_date(target_date, sim_start))
        return out
