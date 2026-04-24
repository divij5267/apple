# CT Calculator — Design Notes & TODO

Living reference for design decisions, edge-case handling, and open items.
Last updated: 2026-04-23.

---

## 0. Where we left off / resume-here marker

**Status as of 2026-04-24 (second implementation pass):**

**Engine + modules:**
- ✅ `Calendar.py` — 5 calendar types, editable HOLIDAYS dict.
- ✅ `workforce.py` — `WorkerGroup`, `DeterministicWorkforce`, `MonthlyInput`, `Months()`, `print_summary()`, `validate()`.
- ✅ `inventory.py` — `DeterministicInventory` (**float counts**), `DemandStream` (daily / on_days_of_month / on_weekdays), `AgeDistributionStream` (NEW 2026-04-24: daily multi-age arrivals keyed by `{month: {age: count_per_day}}` for SIU's TMO-referred cases), `DeterministicDemand` (mixed stream types).
- ✅ `inventory_parser.py` — EDD schema, `inventory_from_paste()`, `print_inventory_summary()`.
- ✅ `Cycle_time_calculator.py` — `DeterministicScenario.validate()`, float burn-down, workable-age window, `reporting_percentile` per queue, capacity utilization metrics in monthly output.
- ✅ `diagnostics.py` (NEW) — `weekly_summary`, `monthly_candlestick_chart`, `daily_inventory_with_p_chart`, `render_scenario_charts`, `compare_scenarios_table`. Charts auto-render for each scenario at end of notebook run. Default Pn method: `p_from_open_ratio`. (Anomaly report + old stacked burn-down chart + compare chart removed per user 2026-04-24; capacity utilization metrics removed per user 2026-04-24.)
- ✅ `runner.py` (NEW) — `run_scenarios(scenarios_dict)` with pre-run validation + skip-if-broken.
- ❌ `export.py` — **removed** per user 2026-04-24. (Excel export + pickle save/load deemed unnecessary; sim is fast enough to re-run.)
- ✅ `__init__.py` — all new exports wired up.

**Notebook:**
- ✅ `Calculator.ipynb` — `QUEUES = "LE, EDD"` comma-separated + `SCENARIOS` dict pattern for advanced use + `build_scenario(queue, overrides=None)` as the single source of queue configs.
- ✅ Placeholder templates for SIU/AC (commented code), `raise` for Fraud/ML.
- ✅ New cells: weekly diagnostics, anomaly report, monthly burn-down chart, scenario comparison, Excel export + pickle bundle save.

**Tests + docs:**
- ✅ `test_ct_calculator.py` — 33 pytest tests covering calendar, workforce, monthly-input grammar, inventory parser, demand streams, workable-age, float burn, scenario validate. All passing.
- ✅ `README.md` — user-facing quickstart + design summary.

**Next session starts HERE:**

- ⚠️ **Cases `arrival_age = 1` is an UNVERIFIED assumption for AC** (and for SIU once its demand is designed). The combined `demand` stream lumps alerts + cases together and arrives at `arrival_age=1`. If cases actually enter the queue at a different age (e.g. pre-aged from upstream intake, or batch-ingested with mixed ages), this needs to change. Confirm with the business team whether cases truly arrive at age 1; if pre-aged, we need a higher per-stream `arrival_age` or separate intake mechanism.

- ⏭ Fill in EDD actual numbers (headcounts, non-prod factors, per-Tuesday demand volumes, open_ratio, closed_ratio). Scaffolding ready.
- ⏭ Collect AC and SIU schemas + numbers (user's next request — "move to AC and SIU").
- ⏭ Collect LE-refreshed inventory format (currently string; may want to switch to paste-based).
- ⏭ Collect Fraud + ML when ready.
- ⏭ Verify religious holidays each year (approx dates in HOLIDAYS).
- ⏭ **FINAL step** — package as pip-installable GitHub repo (§9).
- ⏭ Comments de-AI pass — user-driven rewrite of comments in their own voice.

---

## 1. Architecture overview

### Files (purpose + what lives where)

| File | What it owns | What the user edits |
|---|---|---|
| [Calendar.py](reconstructed_code/Calendar.py) | Calendar types (5), `HOLIDAYS` dict, `OperationsCalendar`, `CalendarManager`, weekend-shifting + half-day logic, `load_all_calendars()`, `print_calendar_summary()` | The `HOLIDAYS` dict at the top of the file. Everything else is engine. |
| [workforce.py](reconstructed_code/workforce.py) | `WorkerGroup` dataclass (shape), `DeterministicWorkforce`, `MonthlyInput`, `Months()` helper, `resolve_monthly()`, ramp / attrition / hires / removals math, `print_summary()`, `validate()` | Nothing — purely engine. |
| [inventory.py](reconstructed_code/inventory.py) | `DeterministicInventory` (items-by-age dict), `DeterministicDemand` (LE-shaped subpoena + 314(a) for now) | Nothing — engine. **Inventory input format being redesigned next session.** |
| [Cycle_time_calculator.py](reconstructed_code/Cycle_time_calculator.py) | `DeterministicScenario`, `DeterministicCycleTimeCalculator`, `DailyBurnDown` (per-group stats), `DeterministicResult`, `calculate_monthly_metrics()` (3 P90 methods), FIFO burn | Nothing — engine. |
| [Calculator.ipynb](reconstructed_code/Calculator.ipynb) | **All per-queue inputs** (worker groups, demand, inventory, ratios, sim period) + run + diagnostics. Queue-selector pattern. | Everything. One-line queue switch: `QUEUE = "LE"`. |
| [__init__.py](reconstructed_code/__init__.py) | Public exports | Nothing. |
| [TODO.md](reconstructed_code/TODO.md) | This document. | Update as decisions evolve. |

### Data flow (one sim run)

```
Notebook                               Engine
────────                               ──────
QUEUE = "LE"
  │
  ├─ worker_groups  ─────►  DeterministicWorkforce
  ├─ demand config  ─────►  DeterministicDemand
  ├─ inv_input      ─────►  DeterministicInventory
  ├─ ratios         ─────►  DeterministicScenario
  ├─ start/end date ─────►  DeterministicScenario
  └─ HOLIDAYS pull  ─────►  CalendarManager
                                │
                                ▼
                        DeterministicCycleTimeCalculator.calculate()
                        ── loops day by day (start_date..end_date) ──
                                │
                                ▼
                        For each day:
                            • Add demand arrivals at age 1
                            • For each WorkerGroup:
                                - look up its calendar → day_factor
                                - raw_headcount × fte_conversion → FTE
                                - FTE × day_factor × TPT → capacity
                            • Sum capacities → total_capacity
                            • FIFO burn down inventory (oldest first)
                            • Age remaining inventory +1 day
                                │
                                ▼
                        DeterministicResult
                            • daily_results: List[DailyBurnDown]
                            • monthly P90 metrics (3 methods)
```

---

## 2. Calendar — design decisions

### Calendar types (5)
| Enum | Meaning | Weekend-shift? | Auto half-days? |
|---|---|---|---|
| `CalendarType.INTERNAL` | Capital One US internal employees | ✅ Yes | ✅ **Only group with half-days** |
| `CalendarType.USA` | US RightSource (external contractors) | ✅ Yes | ❌ |
| `CalendarType.CIPH` | Philippines RightSource | ❌ No | ❌ |
| `CalendarType.CI_POLAND` | Poland RightSource | ❌ No | ❌ |
| `CalendarType.INDIA` | India RightSource | ❌ No | ❌ |

### `HOLIDAYS` dict — hand-edited, no external library
Structure: `HOLIDAYS[CalendarType][year][date] = "name"`. Each calendar type has its **own independent list** — INTERNAL and USA happen to start with the same US federal holidays, but they can diverge freely.

**Why we chose this over the `holidays` Python package:**
- Auditability — every date is visible in one file.
- No external-dependency concerns (company package mirror).
- User wants full control to add/remove specific dates per worker group.

**Religious / moving holidays** (Diwali, Easter Monday, Holy Week, Eid, etc.) are hardcoded approximations. They're flagged with `⚠️ APPROXIMATE — verify` comments in the dict. Must be re-verified each year.

### Weekend-shift rule (USA calendars only)
Saturday → observed Friday. Sunday → observed Monday. Non-USA calendars (India / PH / Poland) do NOT shift — if a holiday falls on a weekend, it's simply lost (matches real-world practice in those regions).

### Half-day auto-generation (INTERNAL only)
- Monday holiday → prior Friday is an early-release half-day.
- Friday holiday → prior Thursday is an early-release half-day.
- Half-day work factor = `work_hours / 8.0` (default 5.0/8.0 = 0.625).

### Per-queue customization
Different queues in the same country may observe different holiday subsets (e.g. RightSource India might work a day that internal-India would take off). Pattern:
1. `load_all_calendars(manager, [2026, 2027])` pulls the defaults from `HOLIDAYS`.
2. Call `calendar_manager.<X>_calendar.add_holiday(date(...), "Name")` or `.remove_holiday(date(...))` in the queue block to customize.

### `print_calendar_summary(manager, years)`
Prints every loaded holiday + half-day per calendar. Called automatically from the notebook before the sim runs. Ends with a ⚠️ reminder about moving religious dates.

---

## 3. Workforce — design decisions

### `WorkerGroup` — one entry per distinct worker population in a queue
Fields (every one was an explicit design decision):

| Field | Type | Required? | Meaning |
|---|---|---|---|
| `calendar_type` | `CalendarType` | ✅ | Drives holidays / weekends / half-days for this group. |
| `name` | `str` | optional | Display label. |
| `current_headcount` | `float` | defaults 0 | Seasoned headcount at sim start (fully productive, factor 1.0). |
| `recent_hires` | `List[float]` | defaults `[]` | `recent_hires[i]` = people hired **i months before sim start**, still ramping. |
| `ramp_period_months` | `int` | defaults 6 | How long new hires take to reach full productivity. **Per-group** (internal may be 6, RS may be 4). |
| `attrition_per_month` | `MonthlyInput` | defaults 0.0 | Absolute headcount lost per month. **Internal only**; RS is always 0. |
| `monthly_hires` | `MonthlyInput` | defaults `{}` | Mid-sim hires by calendar month (internal backfills + RS vendor adds). |
| `rightsource_removals_per_month` | `MonthlyInput` | defaults `{}` | RS de-staffing by calendar month (discrete subtraction). **RS only** — validate warns if set on INTERNAL. |
| `tpt_by_month` | `MonthlyInput` | **REQUIRED** | Per-month throughput. Unset → **hard error** (no silent default). |
| `fte_conversion_by_month` | `MonthlyInput` | conditional | **REQUIRED for INTERNAL** (per-month non-prod factor). Defaults to 1.0 for non-INTERNAL. RS Poland should explicitly set 0.875. |

### `MonthlyInput` — one type, five accepted shapes
Every monthly field accepts:

| Format | Example | Semantics |
|---|---|---|
| scalar | `2.4` | same value every month, every year |
| 12-list | `[2.4, 2.5, ..., 2.3]` | **sim-relative**: index 0 = sim-start month, cycles every 12 sim months |
| `Months()` | `Months(jan=2.4, feb=2.5)` | named-argument helper, calendar-based |
| year dict | `{2026: Months(jan=2.4), 2027: 2.5}` | per-year overrides; inner follows same rules, calendar-based |
| tuple-keyed | `{(2026, 3): 5.0}` | highest-precedence spot override |

Lookup precedence at `(year, month)`: tuple key → year key → month key → default.

### Ramp model — linear
```
ramp_factor(tenure_months) = min(tenure_months / ramp_period_months, 1.0)
```
- Tenure **0** in the month of hire (brand new — factor 0, contributes nothing).
- `recent_hires[i]` tenure at sim month 1 = `i` months (so `recent_hires[0]`, hired "this month", starts at tenure 0 → factor 0).
- Mid-sim `monthly_hires` arriving in sim month `m_arr` have tenure `m_now - m_arr` at any future `m_now`.
- **No graduation** — fully-ramped new hires never merge into `current_headcount`; they stay in their cohort at factor 1.0.

### Attrition — absolute headcount, prorated linearly across sim-visible days

**Plain-English rule:** "By the last sim-day of each month, the full month's attrition value has been subtracted from the pool. It's distributed linearly across the sim-visible days of that month."

**Example** — sim starts Jan 6, attrition = 1.5/month:
- Jan 6 (sim day 1 of 26): cumulative attrition = 1.5 × 1/26 ≈ 0.058
- Jan 15 (sim day 10 of 26): 1.5 × 10/26 ≈ 0.577
- Jan 31 (sim day 26 of 26): 1.5 × 26/26 = 1.500

**Important simplification** (deliberate — §8 / Q1d):
- Attrition subtracts from the total pool **regardless of whether the departing person was seasoned or still ramping**.
- A ramping new hire at factor 3/6 counts as 1 headcount toward attrition, same as a seasoned person.
- Rationale: we don't know who actually leaves. Simpler math; user explicitly chose this.

### Removals (RightSource de-staffing) — discrete per-month
Unlike attrition, `rightsource_removals_per_month` subtracts the **full monthly value at the first sim-day of that month**. User's mental model: "from January onward, there should be 2 fewer CIPH folks."

### FTE conversion — headcount → FTE multiplier
Applied as the **last step**: `FTE = raw_headcount_pool × fte_conversion_by_month[m]`.
- **Internal**: per-month non-prod factor (e.g. `Months(jan=0.7455, feb=0.7316, ...)`).
- **RS Poland**: 0.875 (flat, all months).
- **RS USA / India / PH**: 1.0 (default — no conversion).
- INTERNAL with unset `fte_conversion_by_month` → **hard error**.

### Capacity math (per day, per group)
```
fte_for_month[m]  = raw_headcount_pool[m] × fte_conversion_by_month[m]
effective_fte[d]  = fte_for_month[current_month] × day_factor[group.calendar_type][d]
capacity[d, group] = effective_fte[d] × tpt_by_month[current_month]
total_capacity[d]  = sum over groups
```
Where `day_factor` is 1.0 (full), 0.625 (half-day), 0.0 (weekend/holiday).

### Pre-sim verification — `workforce.print_summary(sim_start, end_date)`
Prints a per-group, per-month table: Raw HC / FTE conv / FTE / TPT / Day cap. Any config issues (missing TPT, missing INTERNAL non-prod, 0-headcount groups, removals on INTERNAL) are reported at the top. Call this before running. Missing-required-field groups are cleanly skipped rather than crashing.

---

## 4. Calculator engine — design decisions

### `simulate_day(current_date, inventory_at_start)` order of operations
1. Copy inventory. Add today's demand arrivals **at age 1**.
2. For each `WorkerGroup`:
   - Resolve its calendar's day type for today → `day_factor`.
   - Compute `raw_headcount`, `fte_for_month`, `tpt`, `capacity`.
   - Record into `group_stats`.
   - Add to `total_capacity`.
3. FIFO burn-down: `total_capacity` of items closed from **oldest age bucket first**.
4. Age remaining inventory by +1 day.
5. Return `DailyBurnDown`.

### FIFO: oldest first
`sorted(items_by_age.items(), key=lambda x: x[0], reverse=True)` — process the highest-age bucket first. Rationale: oldest items get worked first to minimize cycle time.

### Monthly P90 metrics — three methods
Each month produces three P90 estimates (cycle time in days):
- **`p90_direct`** — 90th percentile of closed-item ages in that month.
- **`p90_from_open_ratio`** — max-avg-open-age for the month × `scenario.open_inventory_ratio`.
- **`p90_from_closed_ratio`** — max-avg-closed-age × `scenario.closed_inventory_ratio`.

Ratios are queue-specific and live in the queue block.

### `DailyBurnDown.group_stats` — per-group diagnostic trail
For every day, `group_stats` is a list of dicts (one per worker group) with: `name`, `calendar_type`, `day_type`, `day_factor`, `raw_headcount`, `fte_for_month`, `effective_fte`, `tpt`, `capacity`. Enables queue-agnostic diagnostics and per-group audit tables.

---

## 5. Per-queue configuration — how the notebook works

One `QUEUE = "..."` line at the top of cell 3 selects the queue. Six `if/elif` blocks follow:

| Queue | Status | Notes |
|---|---|---|
| **LE** | ✅ Fully configured | Seeded from prior notebook. Internal + RS US. Subpoena + 314(a) demand. Inventory snapshot + ratios preserved. Attrition seeded at 1.0/mo per current guidance. |
| **SIU** | 🔨 `raise NotImplementedError` | Block scaffolded; awaiting numbers. |
| **EDD** | 🔨 `raise NotImplementedError` | Block scaffolded; awaiting numbers. |
| **AC**  | 🔨 `raise NotImplementedError` | Block scaffolded; awaiting numbers. |
| **Fraud** | 🔨 `raise NotImplementedError` | No defaults yet — user will fill later. |
| **ML**    | 🔨 `raise NotImplementedError` | No defaults yet — user will fill later. |

Adding a queue's numbers = replace the `raise` with `worker_groups = [...]`, `demand = ...`, `inv_input = "..."`, etc. Engine needs zero changes.

---

## 6. Input format design — fast to type + easy to read

Every monthly field accepts multiple shapes (§3). In the notebook, each queue block has a short comment reminding the user of options. Examples:

```python
tpt_by_month = 2.4                                    # scalar (all months)
tpt_by_month = [2.4, 2.5, 2.6, ...]                   # 12-list sim-relative
tpt_by_month = Months(jan=2.4, feb=2.5, mar=2.6)      # named-arg helper
tpt_by_month = {2026: Months(jan=2.4), 2027: 2.5}    # per-year
tpt_by_month = {(2026, 3): 5.0}                       # spot override
```

Int-keyed dicts like `{3: 2.4}` technically work (same as `Months(mar=2.4)`) but **are not used in documentation or examples** — they're confusing (is `3` March or year 3?). Always use `Months(mar=...)` for named months and `{2026: ...}` for years.

---

## 7. Deferred / open items

### Workforce
- [ ] **Non-linear ramp curves.** Ramp is linear. Consider step-function or per-tenure-month multipliers for realism.
- [ ] **Attrition targeting by tenure.** Currently flat subtraction. Could model "ramping hires leave 3x more often" if data justifies.
- [ ] **Per-queue default attrition values.** LE seeded at 1.0/mo; SIU/EDD/AC need values; Fraud/ML TBD entirely.

### Inventory (next session)
- [ ] Input format — keep `"1:2, 2:0, ..."` string, or switch to dict / Months-style?
- [ ] Max age — items age past 31 indefinitely today. Any cap?
- [ ] Arrival age — currently 1. Confirm or change?
- [ ] Is the "items-by-age" model the same for all 6 queues, or do any have severity / sub-task structures?

### Demand
- [ ] **Design deferred.** `DeterministicDemand` is still LE-shaped (subpoena + 314(a) hardcoded). Needs generalization to a `DemandStream` concept: each queue declares its own named streams with per-stream cadence (`daily`, `on_days_of_month`, maybe `weekly`, `specific_dates`). LE's existing wiring stays until then.

### Per-queue content
- [ ] **SIU / EDD / AC** — worker-group mix, numbers, demand cadences, inventory snapshot, ratios.
- [ ] **Fraud / ML** — all numbers. Currently `raise NotImplementedError`.

### Calendar
- [ ] **Verify religious / moving holidays for each year.** Flagged with `⚠️` in `HOLIDAYS` dict. Diwali / Easter / Holy Week / Eid are approximate.

### Calculator / output
- [ ] **Upfront scenario validation** — add `scenario.validate()` that checks calendar + workforce + inventory + demand before sim starts. Currently workforce has `validate()`; generalize.

---

## 8. Edge-case catalog — explicit decisions

| Edge case | What we do | Why |
|---|---|---|
| New hire attrited during ramp | Treated identically to seasoned departure (simple subtraction from pool). No special tracking. | Q1d user decision — simpler, and we don't know who actually leaves. Fully-ramped "new hires" never merge into seasoned pool (no graduation). |
| Pool goes negative (attrition exceeds pool) | Clamped to 0 via `max(pool, 0.0)`. | Can't have negative headcount. |
| Recent hire > ramp period (e.g. `recent_hires[10]` with `ramp_period=6`) | Counts at factor 1.0 (fully ramped). | `ramp_factor` caps at 1.0. |
| Hire arriving same month as sim start | Tenure 0 at sim month 1 → factor 0 → contributes nothing. Ramps from next month. | Consistent with `recent_hires[0]` convention. |
| Sim starts mid-month (e.g. Jan 6) | Attrition proration denominator = sim-visible days of that month (26 for Jan 6-31). By last sim-day of month, full monthly attrition accrued. | Prorates cleanly; user doesn't need to compute partial-month attrition themselves. |
| Holiday falls on weekend (USA calendars) | Shifted: Sat → Fri, Sun → Mon, prefixed `"(Observed)"`. | US federal practice. |
| Holiday falls on weekend (non-USA) | Not shifted — lost for that year. | Matches practice in India / PH / Poland. |
| Monday or Friday holiday (INTERNAL) | Prior Friday or Thursday becomes an early-release half-day (work_hours=5.0, factor 0.625). | Capital One practice. Auto-generated only for INTERNAL. |
| TPT unset | **Hard error** on first `.group_tpt_for_date()` call. | No silent zero — would mask bugs. |
| INTERNAL `fte_conversion_by_month` unset | **Hard error**. | Non-prod is queue-specific; no sensible default for INTERNAL. |
| Non-INTERNAL `fte_conversion_by_month` unset | Defaults to 1.0. | Matches RS reality (except Poland, which user sets explicitly to 0.875). |
| `monthly_hires = {3: 3}` in a 2-year sim | Fires in March of **every** year in sim (3 hires Mar 2026, 3 more Mar 2027). | "Calendar-month, any year" semantics. If user wants one-off: use `{(2026, 3): 3}`. |
| `rightsource_removals_per_month` set on INTERNAL group | Works mechanically but **validate() warns**. | Internal attrite — they don't get vendor-removed. Comment in notebook flags this as wrong usage. |
| WorkerGroup with 0 headcount + no hires | Validator warns: "contributes 0 capacity every day." | Usually a config mistake; not a hard error (could be intentional placeholder). |
| Moving religious holiday (Diwali, Easter Monday) | Hardcoded approximations in `HOLIDAYS`, flagged `⚠️ VERIFY`. Must be updated each year. | Chose hand-coded over external library for auditability. |
| Multi-year sim with monthly-keyed inputs | By default, keyed values broadcast to every year. Override specific year-months with `(year, month)` tuple keys. | User's Q1f + W3 decision. |
| 12-list format in multi-year sim | Cycles with period 12 from sim start. | Documented; use year-keyed dict if different years need different values. |

---

## 9. Features to build (product-level asks, beyond per-area engine work)

Captured 2026-04-23 at session wind-down.

### Visualization — monthly burn-down graph
Per-month stacked bar chart. For each month on the x-axis:
- Starting inventory (bar segment 1)
- Demand arrivals **split by stream** with distinct colors (bar segments 2..N — e.g. LE: subpoena color A, 314(a) color B)
- Burned / closed (bar segment N+1 — distinct color)
- Ending inventory = next month's starting (implicit, flows into next bar)
User needs to see the story: "Started Jan with 1500, 300 alerts + 500 cases came in, burned X, left with Y into Feb."
**Blocked on:** Demand redesign (need per-stream DemandStream structure to know stream names and totals per month). LE-only version possible today but not worth building before demand is generalized.

### Multi-scenario runs in a single notebook
Three patterns needed:
1. **Comma-separated queue list**: `QUEUES = "LE, SIU, ML"` — ✅ **SHIPPED** (2026-04-23). Loops through each, produces one `DeterministicScenario` per queue, runs each, collects `all_results` dict. Demand shapes now vary per queue via `DemandStream` lists — runner handles this correctly.
2. **Copy-paste within same file**: user duplicates the run cell with different `QUEUES` and/or overrides (e.g. "LE baseline" vs "LE with attrition=2.0"). ⏳ Requires a helper like `run_scenario(queue_name, overrides={}) -> DeterministicResult` so the run logic isn't duplicated. Not yet built.
3. **SCENARIOS dict at top** (for 10+ scenarios): `SCENARIOS = {"LE baseline": {...}, "LE high attrition": {...}}`, loop through all, produce comparison report at end. ⏳ Not yet built.

### Placeholder template for empty queue blocks
Currently SIU/EDD/AC/Fraud/ML just `raise NotImplementedError`. Upgrade: show a **commented-out template** (worker_groups skeleton, empty Months, inv_input="", etc.) above the raise, so someone opening the block can see exactly which fields to fill in. Small UX fix.

### Weekly-level diagnostics
Monthly aggregation is too coarse. Add weekly breakdown alongside monthly + daily. Per week:
- Total arrivals (and per-stream once demand is generalized)
- Total closed
- Avg daily capacity (per group + total)
- Inventory level at start/end of week
- Avg inventory age
- Capacity utilization %
Also: flag anomaly days (weekday with 0 capacity = unexpected, spike days in arrivals, etc.).

### Scenario save / load to disk
Serialize a fully-built `DeterministicScenario` to JSON (or pickle) so you don't lose 10 hand-configured scenarios when the notebook kernel restarts. Round-trip: `scenario.to_json(path)` / `DeterministicScenario.from_json(path)`.

### Results export — Excel / CSV
Monthly P90 metrics + daily breakdown + per-group audit → Excel workbook or CSV files for sharing with stakeholders who don't run the notebook. Separate tab/file per scenario when multiple are run.

### Scenario comparison view
When multiple scenarios run, produce a comparison artifact:
- Overlayed P90 line chart (one line per scenario per method)
- Side-by-side monthly metrics tables
- Delta view: "scenario B vs baseline: P90 differs by X days in March"

### Capacity utilization / headroom metric
Per month: `% of workdays running at 100% capacity`, `average daily slack (closed / available_capacity)`. Helps answer "would adding 1 FTE move the needle?" or "are we over-staffed in Q3?"

### Comments pass — de-AI the codebase
All inline comments and docstrings currently written by Claude. Next session: walk through file by file, user rewrites comments in their own voice, keeps what's useful, deletes what isn't. Preserve the Why, drop the What.

### Unit tests
Codify the sanity checks we've run (LE end-to-end, Fraud/ML raise cleanly, ramp math, attrition proration, Months helper, resolve_monthly precedence) into a pytest suite. Next refactor will have a safety net.

### README.md for the notebook
Short user-facing doc separate from TODO.md (which is design-notes). Audience: someone who opens the notebook for the first time. Covers: how to pick a queue, where to edit numbers, what the output means, how to run multiple scenarios.

### Status of §9 items (2026-04-24)

- ✅ Monthly burn-down graph → replaced with **two charts per scenario**: `monthly_candlestick_chart` (OHLC-style per month, Pn badges) + `daily_inventory_with_p_chart` (daily line with monthly Pn labels). Both render automatically for every scenario in `all_results`.
- ✅ Multi-scenario runs → `run_scenarios()` + `SCENARIOS` dict + comma-separated QUEUES
- ✅ Placeholder template for empty queue blocks → commented template in `build_scenario()` for SIU/AC
- ✅ Weekly diagnostics → `weekly_summary()` in diagnostics.py
- ✅ Scenario comparison view → `compare_scenarios_table()` (table only; chart removed)
- ✅ Unit tests → 33 tests in `test_ct_calculator.py`
- ✅ README.md → user-facing doc shipped
- ✅ Scenario-wide validation → `scenario.validate()` + `scenario.print_validate()`
- ❌ **Scenario save/load** — removed per user 2026-04-24 (sim is fast; re-run instead)
- ❌ **Results export (Excel/CSV)** — removed per user 2026-04-24 (export.py deleted)
- ❌ **Capacity utilization / headroom** — removed per user 2026-04-24 (already ~100% for LE; low signal)
- ❌ **Anomaly report** — removed per user 2026-04-24 (inventory-spike check fired on normal subpoena days; zero-capacity-weekday was marginal)
- ⏭ **Comments de-AI pass** — still to do (user-driven rewrite)
- ⏭ **⭐ Package as pip-installable GitHub repo** — still pending (FINAL STEP)

### ⭐ Package as a pip-installable GitHub repo (FINAL STEP)
**This is the last productization step — do this once all engine work is stable.**

Goal: only the `Calculator.ipynb` is the user-facing artifact. All `.py` files ship as an installable Python package from a GitHub repo. The notebook's first cell becomes:
```python
!pip install git+https://github.com/<user>/ct-calculator.git
from ct_calculator import (DeterministicScenario, WorkerGroup, Months, ...)
```

**Work involved:**
- Create a GitHub repo (name TBD — `ct-calculator` or similar).
- Add `pyproject.toml` (or `setup.py`) declaring package name, version, dependencies (`numpy`, `pandas`).
- Restructure files into a proper package layout (e.g. `src/ct_calculator/{calendar.py, workforce.py, ...}`).
- Move `HOLIDAYS` dict + per-queue config story to something user-editable post-install (or keep in-notebook — TBD).
- Version management — `__version__`, tagged releases.
- README.md (the user-facing one, §above) lives at repo root.
- CI — at least a `pytest` run on push (ties to unit-tests TODO).
- End-to-end validation: fresh clone → `pip install .` → open notebook → runs clean.

Non-goals for now: PyPI publication (pip-install-from-git is enough); semantic versioning.

**Why this matters:** anyone on the team can `pip install` and start using — no manual file copying, no "where did I put the `.py` files" questions, no Google-Drive path hacks.

### Scenario-wide validation
`scenario.validate() -> List[str]` that checks calendar + workforce + inventory + demand + ratios + sim period all together before running. Currently only workforce has this. Single call that blocks run if anything's misconfigured.

---

## 9b. Per-queue inventory + demand schemas (being collected)

Source-of-truth for each queue's inventory extract format and demand cadence.
Fill in as the user provides details. Consume when building the inventory
parser and the `DemandStream` refactor.

### EDD ✅ (collected + implemented 2026-04-23)

**Status:** engine supports everything; notebook block scaffolded with placeholder numbers awaiting user data (current_headcount, non-prod factors, per-Tuesday volumes, open_ratio, closed_ratio).



**Inventory extract**
- Source: pasted table (raw from Snowflake/Excel). Parser strips all columns
  except age.
- Age column: `Days Difference`
- Count: each row = 1 alert
- Filters: none
- Single stream (alerts only, no cases)

**Workable-age window**
- Workable: `[61, +∞)` — items ≥61 days consume capacity
- Non-workable: `[0, 60]` — items age each day but never absorb capacity
- No hard upper cap (200 is just what's typically observed)
- FIFO **within workable window**: oldest-first. A 250-day item burns before
  a 62-day item.

**Demand**
- Single stream: `alerts`
- Cadence: weekly on **Tuesday** (weekday index 1)
- Volume semantics: the monthly input value = per-Tuesday volume (NOT monthly
  total). A month with 5 Tuesdays → 5× that value total.
- Arrival age: 1
- Demand arrives on Tuesdays regardless of whether the Tuesday is a holiday
  (confirmed with user).

### AC ✅ (collected + implemented 2026-04-24)

**Status:** engine + notebook scaffolded; awaiting user data.

**Inventory extract**
- Age column: `Days Difference` (same as LE / EDD).
- Count: each row = 1 item. No filters. Single FIFO pool — alerts + cases mixed.
- Paste zone: `AC_INVENTORY_RAW` in cell 3.

**Workable-age window:** none (everything workable, like LE).

**Demand**
- Single combined stream: `demand` (alerts + cases). Cadence: `daily`.
- Monthly input = per-day volume for that month.
- Arrival age: 1.
- *Can be split into two streams (alerts vs cases) later if you want separate colors on the waterfall.*

**Worker groups:** INTERNAL + USA RightSource (same shape as LE / EDD).

**Reporting percentile:** 95.

**Ratios:** placeholders — user to provide `open_inventory_ratio` and `closed_inventory_ratio` when available (noted as "always moving").

**TODO data entry (when ready):**
- Non-prod factor per month (AC_NON_PROD) — currently placeholder 0.75 every month.
- Internal headcount.
- RS USA headcount.
- TPT (currently 2.4 placeholder).
- Daily demand volumes per month.
- Ratios.

### SIU ✅ (fully designed + implemented 2026-04-24)

**Status:** structure + demand design complete; awaiting actual numbers.

**Inventory extract** — stacked tables with two age columns (`Days Difference` for alerts, `case age` for cases). Parser merges into single FIFO pool.

**Workable-age window:** none.

**Demand — designed (NEW: `AgeDistributionStream` class):**
- **Alerts** — daily arrivals, `arrival_age=8` (user-configurable). Regular `DemandStream`.
- **Cases** — daily arrivals, `arrival_age=60` (user-configurable). Regular `DemandStream`.
- **TMO referrals** — daily arrivals spread across multiple ages. NEW `AgeDistributionStream` class supports this: `monthly_distribution={month: {age: count_per_day}}`. Example: `{1: {15: 1, 30: 2, 45: 1}}` = every day of Jan, 1 item at age 15, 2 at 30, 1 at 45.

**Worker groups:** INTERNAL + USA RightSource.

**Reporting percentile:** 90.

**TODO data entry:**
- Monthly per-day volumes for alerts and cases.
- Per-month TMO referral distributions.
- Non-prod factor per month.
- Headcounts, TPT, ratios.

### Inventory paste format ✅ (locked 2026-04-24: format B)

User confirmed format B — two stacked tables with their own header rows:
```
QUEUE   ALERT_TYPE   Days Difference   STATE
SIU     Alert        8                 Open
SIU     Alert        15                Open
QUEUE   CASE_TYPE    case age          STATE
SIU     Case         45                Open
SIU     Case         62                Open
```
Parser detects both header rows (via schema's age column names) and concat-
merges them. Rows from the alerts sub-table have `case age` = NaN; cases have
`Days Difference` = NaN. The multi-column age logic reads the first non-null
and merges everything into one FIFO pool.

Works for LE, AC, SIU (EDD stays single-header since no cases). Unit tests in
`test_ct_calculator.py::TestInventoryParser::test_stacked_tables_*`.

### LE refresh — inventory format change

Planned: LE will switch from inline string (`"1:2, 2:0, 3:0, ..."`) to paste-based extract using the new two-column schema (`Days Difference` for alerts + `case age` for cases). Schema already registered in `QUEUE_INVENTORY_SCHEMAS["LE"]` with both columns. User will paste real LE inventory when available; until then LE's `build_scenario()` keeps the inline string for continuity.

---

## 9c. Sanity-check findings (from realistic-numbers pass, 2026-04-24)

Full system tested end-to-end with plausible AML-queue values for all four
implemented queues. Outputs are mathematically consistent and AML-realistic.
**No bugs found.** Three items worth being aware of:

1. **EDD starting-backlog artifact:** when EDD's snapshot contains old items
   (ages 65–120+), January Pn spikes because those stale items burn first and
   dominate the percentile. Mid-year settles to steady-state. This is correct
   behavior but should be explained to stakeholders so they don't think it's
   a bug.

2. **`p_direct` saturates when arrival_age is high:** SIU's direct P90 is a
   flat 60 days across all months because cases arrive at age 60 and (with
   over-capacity) burn near arrival. The `p_from_open_ratio` and
   `p_from_closed_ratio` methods handle this better — they show queue
   dynamics even when direct is saturated. Charts default to
   `p_from_open_ratio`, so users see the more informative number.

3. **Pure FIFO vs stream priority — open business question:** FIFO across
   heterogeneous arrival ages means cases (arriving at age 60) always burn
   before alerts (arriving at age 1 or 8), regardless of which is higher
   priority for the business. If the real SIU / AC workflow prioritizes
   certain stream types (e.g. alerts are always worked before cases), we'd
   need a per-stream priority layer on top of age. Confirm with business.


## 10. Next-session checklist — resume here

1. **Inventory design** — walk through:
   - Input format (string vs dict vs Months-like)
   - Max-age cap or not
   - Arrival age (currently 1)
   - Per-queue variations (all 6 same shape? severity/sub-task?)
   - Multi-format support for the inventory snapshot

2. **Calculator loop order** — confirm:
   - Demand arrivals at age 1, before capacity compute
   - FIFO oldest-first
   - Aging +1/day
   - Any queue-specific deviations?

3. **Results / diagnostics** — confirm:
   - Current P90 three-method output is what you want
   - Any per-queue output differences?

4. **Demand redesign** — finally (this is the big one):
   - `DemandStream` with per-stream cadence
   - Each queue's own list of streams in its notebook block

5. **Fill in SIU / EDD / AC** numbers once available.

6. **Verify religious holiday dates** for 2026 (and 2027 if sim extends).

7. **After the inventory + demand + calculator refactors land**, build the monthly burn-down graph (§9) — demand-stream structure is a prerequisite.

8. **Once engine is stable**, add the productization items from §9:
   - Multi-scenario runs (comma-separated QUEUE + copy-paste helper)
   - Weekly diagnostics
   - Scenario save/load
   - Results export to Excel/CSV
   - Scenario comparison view
   - README.md
   - Unit tests
   - Comments audit (rewrite in user's voice)

9. **⭐ FINAL STEP — package as pip-installable GitHub repo (§9).** Only the
   notebook stays user-facing; everything else ships as `pip install git+https://...`.
   End-to-end validation: fresh clone → install → open notebook → runs clean.
