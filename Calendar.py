from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple


# =============================================================================
# CALENDAR TYPES — one per distinct worker group
# =============================================================================
# INTERNAL   — Capital One internal US employees.
#              Only group with auto-generated half-days (early release
#              before holiday weekends). US federal holiday list.
# USA        — US right-source (external contractor) workforce.
#              Its OWN independent holiday list — can differ from INTERNAL.
#              Weekend holidays shift to nearest weekday. No half-days.
# CIPH       — Philippines right-source workforce.
# CI_POLAND  — Poland right-source workforce.
# INDIA      — India right-source workforce.
# =============================================================================

class CalendarType(str, Enum):
    INTERNAL = "internal"
    USA = "usa"
    CIPH = "ciph"
    CI_POLAND = "ci_poland"
    INDIA = "india"


class DayType(str, Enum):
    FULL_DAY = "full_day"
    HALF_DAY = "half_day"
    HOLIDAY = "holiday"
    WEEKEND = "weekend"


# =============================================================================
# HOLIDAYS CONFIGURATION — EDIT HERE
# =============================================================================
# Structure: HOLIDAYS[CalendarType][year][date] = "holiday name"
#
# Each CalendarType has its OWN independent list. INTERNAL and USA start with
# the same federal holidays but can diverge freely — edit either list to add
# or remove days for that specific worker group.
#
# RULES:
#   • Enter the ACTUAL holiday date (not the pre-shifted observed date).
#   • For USA-based calendars (INTERNAL, USA), weekend observance is automatic:
#       - Saturday holiday  → observed Friday
#       - Sunday holiday    → observed Monday
#   • For INDIA / CIPH / CI_POLAND, dates are NOT shifted. If a holiday falls
#     on a weekend, it is simply lost (normal for those regions). Add an
#     explicit weekday entry if that group observes a bridge day.
#   • Only INTERNAL auto-generates half-days:
#       Monday holiday → prior Friday half-day (early release)
#       Friday holiday → prior Thursday half-day
#
# PER-QUEUE TWEAKS:
#   After calling load_all_calendars(), use calendar.add_holiday() or
#   calendar.remove_holiday() on the specific OperationsCalendar to customize
#   for a queue whose worker group deviates from the defaults.
#
# ⚠️  MOVING / RELIGIOUS HOLIDAYS — VERIFY EACH YEAR BEFORE USING:
#     India:       Holi, Diwali, Dussehra, Good Friday, Eid al-Fitr, Eid al-Adha
#     Philippines: Holy Thursday, Good Friday, Eid al-Fitr, Eid al-Adha
#     Poland:      Easter Monday, Corpus Christi, Pentecost
# =============================================================================

HOLIDAYS: Dict[CalendarType, Dict[int, Dict[date, str]]] = {

    # -------------------------------------------------------------------------
    # INTERNAL — Capital One US employees
    # -------------------------------------------------------------------------
    CalendarType.INTERNAL: {
        2026: {
            date(2026, 1, 1):   "New Year's Day",
            date(2026, 1, 19):  "Martin Luther King Jr. Day",
            date(2026, 2, 16):  "Presidents' Day",
            date(2026, 5, 25):  "Memorial Day",
            date(2026, 6, 19):  "Juneteenth",
            date(2026, 7, 4):   "Independence Day",           # Sat → observed Fri 7/3
            date(2026, 9, 7):   "Labor Day",
            date(2026, 11, 11): "Veterans Day",
            date(2026, 11, 26): "Thanksgiving",
            date(2026, 11, 27): "Day After Thanksgiving",
            date(2026, 12, 25): "Christmas Day",
        },
        2027: {
            date(2027, 1, 1):   "New Year's Day",
            date(2027, 1, 18):  "Martin Luther King Jr. Day",
            date(2027, 2, 15):  "Presidents' Day",
            date(2027, 5, 31):  "Memorial Day",
            date(2027, 6, 19):  "Juneteenth",                  # Sat → observed Fri 6/18
            date(2027, 7, 4):   "Independence Day",            # Sun → observed Mon 7/5
            date(2027, 9, 6):   "Labor Day",
            date(2027, 11, 11): "Veterans Day",
            date(2027, 11, 25): "Thanksgiving",
            date(2027, 11, 26): "Day After Thanksgiving",
            date(2027, 12, 25): "Christmas Day",               # Sat → observed Fri 12/24
        },
    },

    # -------------------------------------------------------------------------
    # USA — US right-source (external) workforce.
    # Independent list — edit freely to match the RightSource contract.
    # -------------------------------------------------------------------------
    CalendarType.USA: {
        2026: {
            date(2026, 1, 1):   "New Year's Day",
            date(2026, 1, 19):  "Martin Luther King Jr. Day",
            date(2026, 2, 16):  "Presidents' Day",
            date(2026, 5, 25):  "Memorial Day",
            date(2026, 6, 19):  "Juneteenth",
            date(2026, 7, 4):   "Independence Day",
            date(2026, 9, 7):   "Labor Day",
            date(2026, 11, 11): "Veterans Day",
            date(2026, 11, 26): "Thanksgiving",
            date(2026, 11, 27): "Day After Thanksgiving",
            date(2026, 12, 25): "Christmas Day",
        },
        2027: {
            date(2027, 1, 1):   "New Year's Day",
            date(2027, 1, 18):  "Martin Luther King Jr. Day",
            date(2027, 2, 15):  "Presidents' Day",
            date(2027, 5, 31):  "Memorial Day",
            date(2027, 6, 19):  "Juneteenth",
            date(2027, 7, 4):   "Independence Day",
            date(2027, 9, 6):   "Labor Day",
            date(2027, 11, 11): "Veterans Day",
            date(2027, 11, 25): "Thanksgiving",
            date(2027, 11, 26): "Day After Thanksgiving",
            date(2027, 12, 25): "Christmas Day",
        },
    },

    # -------------------------------------------------------------------------
    # INDIA — right-source India. Religious dates move — VERIFY yearly.
    # -------------------------------------------------------------------------
    CalendarType.INDIA: {
        2026: {
            date(2026, 1, 26):  "Republic Day",
            date(2026, 8, 15):  "Independence Day",
            date(2026, 10, 2):  "Gandhi Jayanti",
            date(2026, 12, 25): "Christmas",
            # ⚠️  APPROXIMATE — verify with official calendar:
            date(2026, 3, 4):   "Holi",
            date(2026, 4, 3):   "Good Friday",
            date(2026, 10, 20): "Diwali",
        },
        2027: {
            date(2027, 1, 26):  "Republic Day",
            date(2027, 8, 15):  "Independence Day",
            date(2027, 10, 2):  "Gandhi Jayanti",
            date(2027, 12, 25): "Christmas",
            # ⚠️  Add verified religious dates for 2027 here.
        },
    },

    # -------------------------------------------------------------------------
    # CIPH — Philippines right-source. Holy Week moves — VERIFY yearly.
    # -------------------------------------------------------------------------
    CalendarType.CIPH: {
        2026: {
            date(2026, 1, 1):   "New Year's Day",
            date(2026, 4, 9):   "Day of Valor",
            date(2026, 5, 1):   "Labor Day",
            date(2026, 6, 12):  "Independence Day",
            date(2026, 8, 31):  "National Heroes Day",
            date(2026, 11, 30): "Bonifacio Day",
            date(2026, 12, 25): "Christmas",
            date(2026, 12, 30): "Rizal Day",
            # ⚠️  APPROXIMATE — verify Holy Week each year:
            date(2026, 4, 2):   "Maundy Thursday",
            date(2026, 4, 3):   "Good Friday",
        },
        2027: {
            date(2027, 1, 1):   "New Year's Day",
            date(2027, 4, 9):   "Day of Valor",
            date(2027, 5, 1):   "Labor Day",
            date(2027, 6, 12):  "Independence Day",
            date(2027, 8, 30):  "National Heroes Day",
            date(2027, 11, 30): "Bonifacio Day",
            date(2027, 12, 25): "Christmas",
            date(2027, 12, 30): "Rizal Day",
            # ⚠️  Add verified Holy Week / Eid dates for 2027 here.
        },
    },

    # -------------------------------------------------------------------------
    # CI_POLAND — Poland right-source. Easter-based dates move — VERIFY.
    # -------------------------------------------------------------------------
    CalendarType.CI_POLAND: {
        2026: {
            date(2026, 1, 1):   "New Year's Day",
            date(2026, 1, 6):   "Epiphany",
            date(2026, 5, 1):   "Labour Day",
            date(2026, 5, 3):   "Constitution Day",
            date(2026, 8, 15):  "Assumption of Mary",
            date(2026, 11, 1):  "All Saints' Day",
            date(2026, 11, 11): "Independence Day",
            date(2026, 12, 25): "Christmas",
            date(2026, 12, 26): "Second Day of Christmas",
            # ⚠️  APPROXIMATE — verify each year (Easter = Apr 5, 2026):
            date(2026, 4, 6):   "Easter Monday",
            date(2026, 6, 4):   "Corpus Christi",
        },
        2027: {
            date(2027, 1, 1):   "New Year's Day",
            date(2027, 1, 6):   "Epiphany",
            date(2027, 5, 1):   "Labour Day",
            date(2027, 5, 3):   "Constitution Day",
            date(2027, 8, 15):  "Assumption of Mary",
            date(2027, 11, 1):  "All Saints' Day",
            date(2027, 11, 11): "Independence Day",
            date(2027, 12, 25): "Christmas",
            date(2027, 12, 26): "Second Day of Christmas",
            # ⚠️  Add verified Easter Monday / Corpus Christi for 2027 here.
        },
    },
}


# USA-based calendars shift weekend holidays onto nearest weekday.
WEEKEND_SHIFTING_CALENDARS: Set[CalendarType] = {
    CalendarType.INTERNAL,
    CalendarType.USA,
}

# Only INTERNAL auto-generates half-days (early release before long weekends).
AUTO_HALF_DAY_CALENDARS: Set[CalendarType] = {
    CalendarType.INTERNAL,
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class HolidayEntry:
    date: date
    name: str
    commentary: str = ""
    is_observed: bool = False
    actual_date: Optional[date] = None

    def to_dict(self) -> Dict:
        return {
            "date": self.date.isoformat(),
            "name": self.name,
            "commentary": self.commentary,
            "is_observed": self.is_observed,
            "actual_date": self.actual_date.isoformat() if self.actual_date else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "HolidayEntry":
        return cls(
            date=date.fromisoformat(data["date"]),
            name=data["name"],
            commentary=data.get("commentary", ""),
            is_observed=data.get("is_observed", False),
            actual_date=date.fromisoformat(data["actual_date"]) if data.get("actual_date") else None,
        )


@dataclass
class HalfDayEntry:
    date: date
    reason: str
    related_holiday: Optional[date] = None
    is_auto_generated: bool = False
    work_hours: float = 5.0

    def to_dict(self) -> Dict:
        return {
            "date": self.date.isoformat(),
            "reason": self.reason,
            "related_holiday": self.related_holiday.isoformat() if self.related_holiday else None,
            "is_auto_generated": self.is_auto_generated,
            "work_hours": self.work_hours,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "HalfDayEntry":
        return cls(
            date=date.fromisoformat(data["date"]),
            reason=data["reason"],
            related_holiday=date.fromisoformat(data["related_holiday"]) if data.get("related_holiday") else None,
            is_auto_generated=data.get("is_auto_generated", False),
            work_hours=data.get("work_hours", 5.0),
        )


@dataclass
class OperationsCalendar:
    calendar_type: CalendarType
    year: int = field(default_factory=lambda: date.today().year)
    holidays: List[HolidayEntry] = field(default_factory=list)
    half_days: List[HalfDayEntry] = field(default_factory=list)
    manual_half_days: Set[str] = field(default_factory=set)

    def add_holiday(
        self,
        holiday_date: date,
        name: str,
        commentary: str = "",
        auto_generate_half_days: Optional[bool] = None,
        shift_weekend: Optional[bool] = None,
    ) -> List[HalfDayEntry]:
        # Default behaviors derive from the calendar type unless caller overrides.
        if auto_generate_half_days is None:
            auto_generate_half_days = self.calendar_type in AUTO_HALF_DAY_CALENDARS
        if shift_weekend is None:
            shift_weekend = self.calendar_type in WEEKEND_SHIFTING_CALENDARS

        observed_date = holiday_date
        is_observed = False

        if shift_weekend:
            day_of_week = holiday_date.weekday()
            if day_of_week == 5:
                observed_date = holiday_date - timedelta(days=1)
                is_observed = True
            elif day_of_week == 6:
                observed_date = holiday_date + timedelta(days=1)
                is_observed = True

        existing_dates = {h.date for h in self.holidays}
        if observed_date not in existing_dates:
            self.holidays.append(HolidayEntry(
                date=observed_date,
                name=name + (" (Observed)" if is_observed else ""),
                commentary=commentary,
                is_observed=is_observed,
                actual_date=holiday_date if is_observed else None,
            ))

        generated: List[HalfDayEntry] = []
        if auto_generate_half_days:
            generated = self._generate_half_days(observed_date, name)
        return generated

    def _generate_half_days(self, holiday_date: date, holiday_name: str) -> List[HalfDayEntry]:
        day_of_week = holiday_date.weekday()

        if day_of_week == 0:
            half_day_date = holiday_date - timedelta(days=3)
        elif day_of_week == 4:
            half_day_date = holiday_date - timedelta(days=1)
        else:
            return []

        if half_day_date.isoformat() in self.manual_half_days:
            return []

        existing = {hd.date for hd in self.half_days}
        if half_day_date in existing:
            return []

        half_day = HalfDayEntry(
            date=half_day_date,
            reason=f"Early release before {holiday_name}",
            related_holiday=holiday_date,
            is_auto_generated=True,
            work_hours=5.0,
        )
        self.half_days.append(half_day)
        return [half_day]

    def add_half_day(self, half_day_date: date, reason: str, work_hours: float = 5.0) -> HalfDayEntry:
        self.half_days = [hd for hd in self.half_days if hd.date != half_day_date]

        half_day = HalfDayEntry(
            date=half_day_date,
            reason=reason,
            is_auto_generated=False,
            work_hours=work_hours,
        )
        self.half_days.append(half_day)
        self.manual_half_days.add(half_day_date.isoformat())
        return half_day

    def remove_holiday(self, holiday_date: date) -> bool:
        original_len = len(self.holidays)
        self.holidays = [h for h in self.holidays if h.date != holiday_date]

        if len(self.holidays) < original_len:
            self.half_days = [
                hd for hd in self.half_days
                if not (hd.related_holiday == holiday_date and hd.is_auto_generated)
            ]
            return True
        return False

    def remove_half_day(self, half_day_date: date) -> bool:
        original_len = len(self.half_days)
        self.half_days = [hd for hd in self.half_days if hd.date != half_day_date]
        self.manual_half_days.discard(half_day_date.isoformat())
        return len(self.half_days) < original_len

    def is_holiday(self, check_date: date) -> bool:
        return any(h.date == check_date for h in self.holidays)

    def is_half_day(self, check_date: date) -> bool:
        return any(hd.date == check_date for hd in self.half_days)

    def get_holiday_name(self, check_date: date) -> Optional[str]:
        for h in self.holidays:
            if h.date == check_date:
                return h.name
        return None

    def get_day_type(self, check_date: date) -> Tuple[DayType, float]:
        if check_date.weekday() >= 5:
            return DayType.WEEKEND, 0.0

        for hd in self.half_days:
            if hd.date == check_date:
                return DayType.HALF_DAY, hd.work_hours / 8.0

        if self.is_holiday(check_date):
            return DayType.HOLIDAY, 0.0

        return DayType.FULL_DAY, 1.0

    def clear_all(self) -> None:
        self.holidays = []
        self.half_days = []
        self.manual_half_days = set()

    def to_dict(self) -> Dict:
        return {
            "calendar_type": self.calendar_type.value,
            "year": self.year,
            "holidays": [h.to_dict() for h in self.holidays],
            "half_days": [hd.to_dict() for hd in self.half_days],
            "manual_half_days": list(self.manual_half_days),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "OperationsCalendar":
        cal = cls(
            calendar_type=CalendarType(data["calendar_type"]),
            year=data.get("year", date.today().year),
        )
        cal.holidays = [HolidayEntry.from_dict(h) for h in data.get("holidays", [])]
        cal.half_days = [HalfDayEntry.from_dict(hd) for hd in data.get("half_days", [])]
        cal.manual_half_days = set(data.get("manual_half_days", []))
        return cal


@dataclass
class CalendarManager:
    internal_calendar: OperationsCalendar = field(
        default_factory=lambda: OperationsCalendar(CalendarType.INTERNAL)
    )
    usa_calendar: OperationsCalendar = field(
        default_factory=lambda: OperationsCalendar(CalendarType.USA)
    )
    ciph_calendar: OperationsCalendar = field(
        default_factory=lambda: OperationsCalendar(CalendarType.CIPH)
    )
    poland_calendar: OperationsCalendar = field(
        default_factory=lambda: OperationsCalendar(CalendarType.CI_POLAND)
    )
    india_calendar: OperationsCalendar = field(
        default_factory=lambda: OperationsCalendar(CalendarType.INDIA)
    )

    def get_calendar(self, calendar_type: CalendarType) -> OperationsCalendar:
        mapping = {
            CalendarType.INTERNAL:  self.internal_calendar,
            CalendarType.USA:       self.usa_calendar,
            CalendarType.CIPH:      self.ciph_calendar,
            CalendarType.CI_POLAND: self.poland_calendar,
            CalendarType.INDIA:     self.india_calendar,
        }
        return mapping[calendar_type]

    def all_calendars(self) -> List[Tuple[CalendarType, OperationsCalendar]]:
        return [
            (CalendarType.INTERNAL,  self.internal_calendar),
            (CalendarType.USA,       self.usa_calendar),
            (CalendarType.CIPH,      self.ciph_calendar),
            (CalendarType.CI_POLAND, self.poland_calendar),
            (CalendarType.INDIA,     self.india_calendar),
        ]

    def to_dict(self) -> Dict:
        return {
            "internal_calendar": self.internal_calendar.to_dict(),
            "usa_calendar":      self.usa_calendar.to_dict(),
            "ciph_calendar":     self.ciph_calendar.to_dict(),
            "poland_calendar":   self.poland_calendar.to_dict(),
            "india_calendar":    self.india_calendar.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "CalendarManager":
        manager = cls()
        if "internal_calendar" in data:
            manager.internal_calendar = OperationsCalendar.from_dict(data["internal_calendar"])
        if "usa_calendar" in data:
            manager.usa_calendar = OperationsCalendar.from_dict(data["usa_calendar"])
        if "ciph_calendar" in data:
            manager.ciph_calendar = OperationsCalendar.from_dict(data["ciph_calendar"])
        if "poland_calendar" in data:
            manager.poland_calendar = OperationsCalendar.from_dict(data["poland_calendar"])
        if "india_calendar" in data:
            manager.india_calendar = OperationsCalendar.from_dict(data["india_calendar"])
        return manager


# =============================================================================
# LOADERS & DIAGNOSTICS
# =============================================================================

def load_holidays_from_config(calendar: OperationsCalendar, years: List[int]) -> None:
    """Populate one OperationsCalendar from the HOLIDAYS dict for given years."""
    per_year = HOLIDAYS.get(calendar.calendar_type, {})
    for year in years:
        for h_date, h_name in per_year.get(year, {}).items():
            calendar.add_holiday(h_date, h_name)


def load_all_calendars(manager: CalendarManager, years: List[int]) -> None:
    """Populate every calendar in the manager from the HOLIDAYS dict."""
    for _, calendar in manager.all_calendars():
        load_holidays_from_config(calendar, years)


def print_calendar_summary(
    manager: CalendarManager,
    years: Optional[List[int]] = None,
) -> None:
    """Print every loaded holiday + half-day per calendar.

    Call this BEFORE running a simulation. Religious/moving dates in the
    India / Philippines / Poland lists are approximate — verify and edit
    HOLIDAYS (or call add_holiday / remove_holiday on the specific calendar)
    before trusting the output.
    """
    print("=" * 72)
    print("CALENDAR SUMMARY — verify before running simulation")
    if years:
        print(f"Showing years: {years}")
    print("=" * 72)

    for cal_type, calendar in manager.all_calendars():
        holidays = sorted(calendar.holidays, key=lambda h: h.date)
        half_days = sorted(calendar.half_days, key=lambda h: h.date)
        if years:
            holidays = [h for h in holidays if h.date.year in years]
            half_days = [hd for hd in half_days if hd.date.year in years]

        print(f"\n--- {cal_type.value.upper()} "
              f"({len(holidays)} holidays, {len(half_days)} half-days) ---")

        if not holidays and not half_days:
            print("  (none)")
            continue

        for h in holidays:
            note = " [observed]" if h.is_observed else ""
            print(f"  HOLIDAY   {h.date} ({h.date.strftime('%a')})  {h.name}{note}")
        for hd in half_days:
            note = " [auto]" if hd.is_auto_generated else " [manual]"
            print(f"  HALF-DAY  {hd.date} ({hd.date.strftime('%a')})  "
                  f"{hd.reason} ({hd.work_hours}h){note}")

    print("\n" + "=" * 72)
    print("⚠️  Religious/moving holidays in India/PH/Poland are APPROXIMATE —")
    print("   verify dates and edit HOLIDAYS in Calendar.py or call")
    print("   add_holiday/remove_holiday on the relevant calendar.")
    print("=" * 72)
