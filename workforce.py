from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Union
import calendar as _calendar

from Calendar import CalendarType


# =============================================================================
# MONTHLY INPUT — flexible shape for any per-month field
# =============================================================================
# Wherever a WorkerGroup field is typed `MonthlyInput`, pass any of:
#
#   (A) scalar        → same value every month, every year
#                         e.g. tpt_by_month = 2.4
#
#   (B) 12-list       → sim-relative: list[0] = value for sim-start month,
#                       list[1] = next month, ..., cycles every 12 sim months
#                         e.g. tpt_by_month = [2.4, 2.5, 2.6, ...]   # 12 values
#
#   (C) Months(...)   → named-argument helper — calendar-based (Jan..Dec)
#                         e.g. tpt_by_month = Months(jan=2.4, feb=2.5, mar=2.6)
#
#   (D) year dict     → per-year overrides; inner follows same rules (calendar)
#                         e.g. tpt_by_month = {2026: Months(jan=2.4),
#                                              2027: Months(jan=2.5)}
#
#   (E) (year, month) → highest-precedence spot override
#                         e.g. tpt_by_month = {(2026, 3): 3.0}
#
# Lookup order at (year, month): tuple key > year key > month key > default.
# =============================================================================

MonthlyInput = Union[int, float, List[float], Dict[Any, Any]]


_MONTH_ABBR_TO_INT = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def Months(**kwargs: float) -> Dict[int, float]:
    """Readable month-named helper: Months(jan=2.4, feb=2.5) -> {1: 2.4, 2: 2.5}.

    Use this instead of bare int-keyed dicts for readability.
    """
    out: Dict[int, float] = {}
    for name, val in kwargs.items():
        key = name.lower()
        if key not in _MONTH_ABBR_TO_INT:
            raise ValueError(
                f"Unknown month abbreviation {name!r}. Use jan/feb/.../dec."
            )
        out[_MONTH_ABBR_TO_INT[key]] = float(val)
    return out


def resolve_monthly(
    value: MonthlyInput,
    year: int,
    month: int,
    sim_start: date,
    default: Optional[float] = None,
) -> float:
    """Look up a MonthlyInput value for a specific (year, month).

    `sim_start` is used for 12-list sim-relative indexing.
    If no value is found and `default` is None, raises KeyError.
    """
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, list):
        if len(value) != 12:
            raise ValueError(
                f"12-element list required (sim-relative Jan..Dec from sim start); got {len(value)}."
            )
        sim_month_idx = (year - sim_start.year) * 12 + (month - sim_start.month) + 1
        return float(value[(sim_month_idx - 1) % 12])

    if isinstance(value, dict):
        # (year, month) tuple takes precedence
        if (year, month) in value:
            return float(value[(year, month)])
        # year key (>12 ints, year-specific override)
        if year in value:
            return _resolve_year_inner(value[year], month, default)
        # month key (calendar-based, applies every year)
        if month in value:
            return float(value[month])
        if default is None:
            raise KeyError(
                f"No value found for {year}-{month:02d} in monthly input and no default provided."
            )
        return float(default)

    raise TypeError(f"Unsupported MonthlyInput type: {type(value).__name__}")


def _resolve_year_inner(inner: Any, month: int, default: Optional[float]) -> float:
    """Resolve value under a year key. Inner is calendar-based (Jan..Dec)."""
    if isinstance(inner, (int, float)):
        return float(inner)
    if isinstance(inner, list):
        if len(inner) != 12:
            raise ValueError(
                f"12-list under a year key must be Jan..Dec (length 12); got {len(inner)}."
            )
        return float(inner[month - 1])
    if isinstance(inner, dict):
        if month in inner:
            return float(inner[month])
        if default is None:
            raise KeyError(f"No value for month {month} in year-keyed inner dict.")
        return float(default)
    raise TypeError(f"Unsupported year-inner MonthlyInput type: {type(inner).__name__}")


# =============================================================================
# WORKER GROUP — one entry per distinct worker population in a queue
# =============================================================================
# A queue is a list of WorkerGroups. LE = [Internal, RS_USA]; TMO can be
# [Internal, RS_USA, RS_CIPH, RS_Poland, RS_India]. Each group:
#
#   • uses its own calendar (holidays/weekends/half-days)
#   • has its own ramp, TPT, FTE conversion, attrition, hires, removals
#   • contributes independently; total capacity = sum across groups
#
# Convention: newly-hired people count as tenure 0 in their arrival month
# (factor 0 — brand new). They ramp from the following month onward.
# =============================================================================

@dataclass
class WorkerGroup:
    calendar_type: CalendarType
    name: str = ""

    # Starting state at sim start
    current_headcount: float = 0.0
    recent_hires: List[float] = field(default_factory=list)
    # recent_hires[i] = people hired i months before sim start, still ramping.
    # At sim start their tenure is i months, so:
    #   [4, 3, 3] = 4 hired this month (tenure 0 -> factor 0),
    #               3 hired 1 mo ago (tenure 1 -> factor 1/ramp),
    #               3 hired 2 mo ago (tenure 2 -> factor 2/ramp).

    # Ramp (linear for v1 — see TODO.md)
    ramp_period_months: int = 6

    # Attrition — absolute headcount lost per month. Prorated linearly across
    # the sim-visible portion of each month. Internal only; RS leaves at 0.
    attrition_per_month: MonthlyInput = 0.0

    # Mid-sim hires (calendar-month keyed). Internal backfills + RS vendor adds.
    # Tenure 0 on arrival -> factor 0 -> ramps from next month onward.
    monthly_hires: MonthlyInput = field(default_factory=dict)

    # RS de-staffing ONLY. Discrete subtraction at start of the calendar month.
    # Internal folks are not "removed" — they attrite. Setting this on an
    # INTERNAL group technically works but is flagged as wrong usage.
    rightsource_removals_per_month: MonthlyInput = field(default_factory=dict)

    # REQUIRED — per-group throughput. No implicit default; unset -> error.
    tpt_by_month: Optional[MonthlyInput] = None

    # Headcount -> FTE conversion.
    #   INTERNAL  : MUST be set (per-month non-prod); unset -> error
    #   CI_POLAND : set to 0.875 (flat) in the queue block
    #   other RS  : leave None -> defaults to 1.0
    fte_conversion_by_month: Optional[MonthlyInput] = None

    # ---- DIRECT FTE OVERRIDE -------------------------------------------------
    # When set, the group's monthly FTE is taken DIRECTLY from this field and
    # the entire headcount-based math (current_headcount + ramp + hires -
    # attrition - removals) × fte_conversion_by_month is BYPASSED. TPT is
    # still required; day_factor (weekends/holidays) is still applied.
    # Same MonthlyInput grammar as everywhere else (scalar / 12-list /
    # Months() / year-dict / (year, month) tuple). Unset (None) keeps the
    # default headcount-derived behavior.
    #   e.g. fte_override_by_month=30.0
    #        fte_override_by_month=Months(jan=30, feb=32, mar=34)
    #        fte_override_by_month={2026: Months(jan=30), 2027: 35}
    # ------------------------------------------------------------------------
    fte_override_by_month: Optional[MonthlyInput] = None

    def ramp_factor(self, tenure_months: int) -> float:
        """Linear ramp: tenure/ramp_period, capped at 1.0. Tenure 0 -> 0."""
        if tenure_months <= 0:
            return 0.0
        if self.ramp_period_months <= 0:
            return 1.0
        if tenure_months >= self.ramp_period_months:
            return 1.0
        return tenure_months / self.ramp_period_months


# =============================================================================
# DETERMINISTIC WORKFORCE — list of WorkerGroups + math
# =============================================================================

@dataclass
class DeterministicWorkforce:
    worker_groups: List[WorkerGroup] = field(default_factory=list)

    # ---- Core per-group math -----------------------------------------------

    def raw_headcount(
        self,
        group_idx: int,
        sim_start: date,
        current_date: date,
    ) -> float:
        """Raw headcount pool for a group at `current_date`.

        Math: current_headcount + ramp contributions - cumulative attrition
        (prorated) - cumulative removals (discrete). Clamped to >= 0.
        Per Q1d: attrition subtracts flat from total pool; no graduation.
        """
        group = self.worker_groups[group_idx]
        if current_date < sim_start:
            return float(group.current_headcount)

        sim_month_idx = _months_between(sim_start, current_date) + 1
        pool = float(group.current_headcount)

        # Pre-sim cohorts still ramping (tenure = i + sim_month_idx - 1)
        for i, count in enumerate(group.recent_hires):
            tenure = i + sim_month_idx - 1
            pool += count * group.ramp_factor(tenure)

        # Mid-sim hires (tenure at arrival = 0, ramps from month after arrival)
        year, month = sim_start.year, sim_start.month
        for m_idx in range(1, sim_month_idx + 1):
            arriving = resolve_monthly(
                group.monthly_hires, year, month, sim_start, default=0.0,
            )
            if arriving:
                tenure = sim_month_idx - m_idx
                pool += arriving * group.ramp_factor(tenure)
            month += 1
            if month > 12:
                month = 1
                year += 1

        # Cumulative attrition (prorated) + removals (discrete)
        pool -= self._attrition_accrued(group, sim_start, current_date)
        pool -= self._removals_accrued(group, sim_start, current_date)

        return max(pool, 0.0)

    def group_fte_for_date(
        self,
        group_idx: int,
        sim_start: date,
        current_date: date,
    ) -> float:
        """FTE for (year, month) of current_date.

        If `fte_override_by_month` is set on the group, that value wins
        outright — raw_headcount, ramp, attrition, hires, removals, and
        fte_conversion are ALL bypassed. Otherwise:
        raw_headcount x fte_conversion_by_month.
        """
        group = self.worker_groups[group_idx]
        if group.fte_override_by_month is not None:
            return resolve_monthly(
                group.fte_override_by_month,
                current_date.year,
                current_date.month,
                sim_start,
                default=None,
            )
        fte_conv = self._get_fte_conversion(group, sim_start, current_date)
        raw = self.raw_headcount(group_idx, sim_start, current_date)
        return raw * fte_conv

    def group_uses_fte_override(self, group_idx: int) -> bool:
        """True if this group bypasses headcount math via fte_override_by_month."""
        return self.worker_groups[group_idx].fte_override_by_month is not None

    def group_tpt_for_date(
        self,
        group_idx: int,
        sim_start: date,
        current_date: date,
    ) -> float:
        group = self.worker_groups[group_idx]
        if group.tpt_by_month is None:
            raise ValueError(
                f"WorkerGroup '{group.name or group.calendar_type.value}' has no "
                "tpt_by_month set. Provide one explicitly — no implicit default."
            )
        return resolve_monthly(
            group.tpt_by_month,
            current_date.year,
            current_date.month,
            sim_start,
            default=None,
        )

    # ---- Validation guards --------------------------------------------------

    def _get_fte_conversion(
        self,
        group: WorkerGroup,
        sim_start: date,
        current_date: date,
    ) -> float:
        if group.fte_conversion_by_month is None:
            if group.calendar_type == CalendarType.INTERNAL:
                raise ValueError(
                    f"INTERNAL WorkerGroup '{group.name}' must set "
                    "fte_conversion_by_month (per-month non-prod factor). "
                    "No default for INTERNAL."
                )
            return 1.0
        return resolve_monthly(
            group.fte_conversion_by_month,
            current_date.year,
            current_date.month,
            sim_start,
            default=1.0,
        )

    # ---- Cumulative attrition / removals -----------------------------------

    def _attrition_accrued(
        self,
        group: WorkerGroup,
        sim_start: date,
        current_date: date,
    ) -> float:
        """Prorated cumulative attrition from sim_start through current_date.

        For each calendar month (y, m) overlapping [sim_start, current_date]:
          first_day_in_sim = sim_start.day if (y, m) == sim_start month else 1
          last_day_in_sim  = current_date.day if (y, m) == current_date month
                             else days_in_month
          portion = (last_day_in_sim - first_day_in_sim + 1)
                    / (days_in_month - first_day_in_sim + 1)
          accrued += monthly_attrition * portion
        """
        total = 0.0
        year, month = sim_start.year, sim_start.month

        while (year, month) <= (current_date.year, current_date.month):
            monthly_attr = resolve_monthly(
                group.attrition_per_month, year, month, sim_start, default=0.0,
            )
            if monthly_attr != 0.0:
                first_day = sim_start.day if (year, month) == (sim_start.year, sim_start.month) else 1
                days_in_month = _calendar.monthrange(year, month)[1]
                last_day = (
                    current_date.day
                    if (year, month) == (current_date.year, current_date.month)
                    else days_in_month
                )
                numerator = last_day - first_day + 1
                denominator = days_in_month - first_day + 1
                portion = numerator / denominator if denominator > 0 else 1.0
                total += monthly_attr * portion

            month += 1
            if month > 12:
                month = 1
                year += 1

        return total

    def _removals_accrued(
        self,
        group: WorkerGroup,
        sim_start: date,
        current_date: date,
    ) -> float:
        """Cumulative RS removals, discrete per month (full value on/after day 1)."""
        total = 0.0
        year, month = sim_start.year, sim_start.month
        while (year, month) <= (current_date.year, current_date.month):
            total += resolve_monthly(
                group.rightsource_removals_per_month, year, month, sim_start, default=0.0,
            )
            month += 1
            if month > 12:
                month = 1
                year += 1
        return total

    # ---- Pre-sim validation + summary ---------------------------------------

    def validate(self) -> List[str]:
        """Check every WorkerGroup for missing required fields / obvious issues.

        Returns a list of human-readable issue strings. Empty list = all good.
        Call this BEFORE running the simulation to catch config bugs early —
        a cleaner experience than letting them raise mid-sim.
        """
        issues: List[str] = []
        for i, g in enumerate(self.worker_groups):
            label = g.name or f"WorkerGroup[{i}] ({g.calendar_type.value})"
            override_set = g.fte_override_by_month is not None

            if g.tpt_by_month is None:
                issues.append(f"{label}: tpt_by_month is NOT SET (required for every group).")

            # INTERNAL fte_conversion is required ONLY when override is not used.
            if (
                not override_set
                and g.fte_conversion_by_month is None
                and g.calendar_type == CalendarType.INTERNAL
            ):
                issues.append(
                    f"{label}: INTERNAL group must set fte_conversion_by_month "
                    "(per-month non-prod factor)."
                )

            # The "0 headcount + no hires" warning only applies in the
            # headcount-based mode. Override mode supplies FTE directly.
            if (
                not override_set
                and g.current_headcount == 0
                and not g.recent_hires
                and not g.monthly_hires
            ):
                issues.append(
                    f"{label}: starts at 0 headcount with no recent_hires and no "
                    "monthly_hires — this group contributes 0 capacity every day."
                )

            if g.rightsource_removals_per_month and g.calendar_type == CalendarType.INTERNAL:
                issues.append(
                    f"{label}: rightsource_removals_per_month set on INTERNAL group. "
                    "Internal folks attrite (use attrition_per_month) — removals are "
                    "intended for RS de-staffing."
                )

        return issues

    def override_notes(self) -> List[str]:
        """Non-blocking informational notes about FTE-override usage.

        Distinct from `validate()` — these never cause a scenario to be
        skipped. Returned as a list of human-readable strings; printed by
        `print_summary()` and by the calculator at construction time so the
        user is reminded which groups bypass the headcount math.
        """
        notes: List[str] = []
        for i, g in enumerate(self.worker_groups):
            if g.fte_override_by_month is None:
                continue
            label = g.name or f"WorkerGroup[{i}] ({g.calendar_type.value})"
            notes.append(
                f"{label}: using fte_override_by_month — headcount/ramp/"
                "attrition/hires/conversion math is BYPASSED. Capacity = "
                "fte_override x day_factor x tpt."
            )
            conflicts: List[str] = []
            if g.current_headcount:
                conflicts.append("current_headcount")
            if g.recent_hires:
                conflicts.append("recent_hires")
            if g.monthly_hires:
                conflicts.append("monthly_hires")
            if g.attrition_per_month:
                conflicts.append("attrition_per_month")
            if g.rightsource_removals_per_month:
                conflicts.append("rightsource_removals_per_month")
            if g.fte_conversion_by_month is not None:
                conflicts.append("fte_conversion_by_month")
            if conflicts:
                notes.append(
                    f"{label}: also set (and IGNORED, override wins): "
                    + ", ".join(conflicts)
                )
        return notes

    def print_summary(self, sim_start: date, end_date: date) -> None:
        """Print a monthly per-group breakdown of raw_headcount / FTE / capacity.

        Use this BEFORE running the sim to verify your config resolves to the
        numbers you expect. Reports issues (missing TPT, unset INTERNAL
        fte_conversion, etc.) at the top instead of raising.
        """
        print("=" * 72)
        print("WORKFORCE SUMMARY — verify before running simulation")
        print(f"Sim span: {sim_start} -> {end_date}")
        print("=" * 72)

        issues = self.validate()
        if issues:
            print("\n!! CONFIG ISSUES — fix before running:")
            for msg in issues:
                print(f"   - {msg}")
            print()

        notes = self.override_notes()
        if notes:
            print("\n** FTE OVERRIDE NOTES (non-blocking):")
            for msg in notes:
                print(f"   - {msg}")
            print()

        months_in_sim = _iter_sim_months(sim_start, end_date)

        for i, g in enumerate(self.worker_groups):
            label = g.name or g.calendar_type.value
            override_set = g.fte_override_by_month is not None
            tag = "  (FTE OVERRIDE)" if override_set else ""
            print(f"\n--- {label}  [{g.calendar_type.value}]{tag} ---")

            if g.tpt_by_month is None or (
                not override_set
                and g.fte_conversion_by_month is None
                and g.calendar_type == CalendarType.INTERNAL
            ):
                print("  (skipped — required fields missing; see issues above)")
                continue

            if override_set:
                # Compact 3-column layout: FTE comes from override, no
                # headcount / FTE-conversion to display.
                print(f"  {'Y-M':<8} {'FTE':>9} {'TPT':>7} {'Day cap':>9}")
                for year, month in months_in_sim:
                    ref_day = _calendar.monthrange(year, month)[1]
                    if (year, month) == (end_date.year, end_date.month):
                        ref_day = end_date.day
                    ref_date = date(year, month, ref_day)
                    fte = self.group_fte_for_date(i, sim_start, ref_date)
                    tpt = resolve_monthly(
                        g.tpt_by_month, year, month, sim_start, default=None,
                    )
                    day_cap = fte * tpt
                    print(
                        f"  {year}-{month:02d}  {fte:>9.2f} "
                        f"{tpt:>7.2f} {day_cap:>9.2f}"
                    )
                continue

            print(
                f"  {'Y-M':<8} {'Raw HC':>9} {'FTE conv':>10} "
                f"{'FTE':>9} {'TPT':>7} {'Day cap':>9}"
            )
            for year, month in months_in_sim:
                ref_day = _calendar.monthrange(year, month)[1]
                # Clamp ref day to actual sim end if we're in the last sim month.
                if (year, month) == (end_date.year, end_date.month):
                    ref_day = end_date.day
                ref_date = date(year, month, ref_day)

                raw = self.raw_headcount(i, sim_start, ref_date)
                conv = self._get_fte_conversion(g, sim_start, ref_date)
                fte = raw * conv
                tpt = resolve_monthly(
                    g.tpt_by_month, year, month, sim_start, default=None,
                )
                day_cap = fte * tpt
                print(
                    f"  {year}-{month:02d}  {raw:>9.2f} {conv:>10.4f} "
                    f"{fte:>9.2f} {tpt:>7.2f} {day_cap:>9.2f}"
                )

        print("\n" + "=" * 72)
        print("Columns: Raw HC = headcount pool after ramp/attrition/removals;")
        print("         FTE conv = fte_conversion_by_month factor for that month;")
        print("         FTE = Raw HC x FTE conv; Day cap = FTE x TPT on a full workday.")
        print("=" * 72)


# =============================================================================
# Module-private helpers
# =============================================================================

def _months_between(start: date, end: date) -> int:
    """Full calendar-months from start to end (0 same month; negative if end<start)."""
    return (end.year - start.year) * 12 + (end.month - start.month)


def _iter_sim_months(sim_start: date, end_date: date) -> List:
    """List of (year, month) pairs spanning sim_start..end_date inclusive."""
    out = []
    year, month = sim_start.year, sim_start.month
    while (year, month) <= (end_date.year, end_date.month):
        out.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return out
