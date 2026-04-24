# Cycle Time Calculator

Deterministic day-by-day simulation of work-item burn-down across 6 queues
(LE, SIU, EDD, AC, Fraud, ML). Computes monthly P90/P95 cycle-time metrics
given a workforce, starting inventory, and demand schedule.

**You only run `Calculator.ipynb`.** All inputs live in the notebook. The
`.py` files are engine — you rarely touch them.

---

## Quickstart

1. Open `Calculator.ipynb`.
2. Edit cell 3:
   ```python
   QUEUES = "LE"                     # one queue
   # or
   QUEUES = "LE, EDD"                # multiple queues in one run
   ```
3. Set your simulation period:
   ```python
   start_date = date.fromisoformat("2026-01-06")
   end_date   = date.fromisoformat("2026-12-31")
   ```
4. (For queues that use paste-in inventory): paste the raw extract into the
   paste zone, e.g. `EDD_INVENTORY_RAW = """<paste here>"""`.
5. Run all cells.

Output: monthly metrics table (P90/P95 values as integers, ceiling-rounded),
weekly diagnostics, **two auto-rendered charts per scenario** (monthly
inventory candlestick + daily open inventory with monthly Pn badges),
scenario comparison table (if ≥2 queues).

---

## Switching queues / adjusting numbers

Every queue's config lives inside `build_scenario(queue_name, overrides)` in
cell 3. To tweak a number, edit that function. Example — increase LE's TPT:

```python
if queue_name == "LE":
    ...
    DEFAULT_TPT = 2.4    # ← change this
```

To pass ad-hoc overrides without editing the function:

```python
SCENARIOS = {
    "LE baseline":        build_scenario("LE"),
    "LE high attrition":  build_scenario("LE", overrides={"attrition_per_month": 2.0}),
    "LE low TPT":         build_scenario("LE", overrides={"tpt_by_month": 1.8}),
}
```

---

## Input format grammar

Every monthly field (TPT, non-prod, attrition, demand volumes, hires,
removals) accepts any of these shapes:

| Format | Example | Semantics |
|---|---|---|
| scalar | `2.4` | same value every month, every year |
| 12-list | `[2.4, 2.5, ..., 2.3]` | sim-relative: list[0] = sim-start month, cycles |
| `Months()` | `Months(jan=2.4, feb=2.5)` | named-argument helper, calendar-based |
| year dict | `{2026: Months(jan=2.4), 2027: 2.5}` | per-year override; inner same grammar |
| tuple | `{(2026, 3): 5.0}` | spot override for one specific (year, month) |

Examples: [workforce.py](workforce.py) `MonthlyInput` docstring.

---

## Adding numbers for a new queue

Queue blocks for SIU / AC / Fraud / ML are scaffolded in `build_scenario()`
with a commented-out template above the `raise NotImplementedError`.
To activate a queue:

1. Uncomment the template.
2. Fill in worker-group headcounts, TPT, non-prod factors, demand streams,
   inventory snapshot, ratios, reporting percentile, workable-age window.
3. Remove the `raise`.
4. Add an inventory paste zone at the top of cell 3 if needed.
5. If the queue has a new extract format, add an entry to
   `QUEUE_INVENTORY_SCHEMAS` in [inventory_parser.py](inventory_parser.py).

---

## Key design decisions (see TODO.md for full catalog)

- **Float burn-down.** Counts are fractional — capacity 95.5 closes 95 + 0.5
  items; the 0.5 residual carries over. No `int()` truncation anywhere.
- **Workable-age window** (e.g. EDD `[61, ∞)`). Items outside the window
  age each day but never absorb capacity.
- **Reporting percentile per queue.** LE/SIU = 90, EDD/AC = 95, Fraud/ML TBD.
- **Ramp model.** Linear; tenure 0 in month of hire (contributes 0);
  fully ramped at `ramp_period_months`.
- **Attrition** prorated linearly across sim-visible days of each month.
  By the last sim-day of the month, full monthly attrition is subtracted.
- **Calendars.** 5 types (INTERNAL, USA, CIPH, CI_POLAND, INDIA).
  USA-based calendars shift weekend holidays; only INTERNAL gets half-days.
- **Demand streams.** Each queue has its own list. Cadences: `daily`,
  `on_days_of_month` (e.g. LE subpoena on 13/27), `on_weekdays` (e.g. EDD
  alerts every Tuesday). Per-stream `arrival_age`.

---

## What each file does

| File | Purpose |
|---|---|
| `Calendar.py` | `HOLIDAYS` dict + `CalendarManager` + weekend-shift / half-day logic |
| `workforce.py` | `WorkerGroup`, `DeterministicWorkforce`, `Months`, `MonthlyInput` |
| `inventory.py` | `DeterministicInventory` (float counts), `DemandStream`, `DeterministicDemand` |
| `inventory_parser.py` | `QUEUE_INVENTORY_SCHEMAS` + `inventory_from_paste()` |
| `Cycle_time_calculator.py` | `DeterministicScenario`, sim engine, P90/P95 metrics |
| `diagnostics.py` | Weekly summary, monthly inventory candlestick chart, daily inventory + Pn chart, scenario comparison table |
| `runner.py` | `run_scenarios()` — runs a dict of scenarios with pre-run validation |
| `Calculator.ipynb` | **User-facing.** All numbers live here. |
| `test_ct_calculator.py` | Pytest suite (run `pytest test_ct_calculator.py`) |
| `TODO.md` | Design-decisions catalog + deferred items |

---

## Running tests

```bash
python -m pytest test_ct_calculator.py -v
```

Covers calendar, workforce math, inventory parser, demand streams,
workable-age filtering, float burn-down, scenario validation. 33 tests.

---

## Tomorrow / still to do

See [TODO.md](TODO.md) §0 ("Where we left off") and §9 ("Features to
build"). Next up: collect SIU, AC, LE-refreshed, Fraud, ML schemas and
numbers; eventually package as a pip-installable GitHub repo.
