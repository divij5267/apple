"""Scenario execution helpers.

Two entry points:

1. `run_scenarios(scenarios_dict)` — runs all in order, returns a bundle dict
   {name: (calculator, result)}. Prints brief summary per run.

2. `run_scenarios_from_builder(queues_or_list, build_fn)` — takes a list or
   comma-separated string of queue names and a builder callback; builds each
   scenario via the callback, then runs.

The SCENARIOS-dict pattern in the notebook:

    SCENARIOS = {
        "LE baseline":     build_scenario("LE"),
        "LE high attrition": build_scenario("LE", overrides={"attrition_per_month": 2.0}),
        "EDD as-is":       build_scenario("EDD"),
    }
    results = run_scenarios(SCENARIOS)

The builder function is whatever the notebook defines — engine doesn't dictate.
"""

from typing import Callable, Dict, List, Tuple, Union

from Cycle_time_calculator import (
    DeterministicCycleTimeCalculator,
    DeterministicResult,
    DeterministicScenario,
)


def run_scenarios(
    scenarios: Dict[str, DeterministicScenario],
    print_progress: bool = True,
    validate_first: bool = True,
) -> Dict[str, Tuple[DeterministicCycleTimeCalculator, DeterministicResult]]:
    """Run each scenario in `scenarios`. Returns {name: (calc, result)}.

    If `validate_first` is True, prints validation issues for each scenario
    before running and SKIPS any scenario with issues. Set False to force-run.
    """
    out: Dict[str, Tuple[DeterministicCycleTimeCalculator, DeterministicResult]] = {}

    for name, scenario in scenarios.items():
        if print_progress:
            print(f"\n{'=' * 72}\nRUN: {name}\n{'=' * 72}")

        if validate_first:
            issues = scenario.validate()
            if issues:
                print(f"  ⚠️  SKIPPED — {len(issues)} validation issue(s):")
                for msg in issues:
                    print(f"     - {msg}")
                continue

        calc = DeterministicCycleTimeCalculator(scenario)
        result = calc.calculate()
        out[name] = (calc, result)

        if print_progress:
            print(f"  days={len(result.daily_results)}  "
                  f"closed={result.total_items_closed:,.1f}  "
                  f"final_inv={result.final_inventory.get_total_items():,.0f}")
    return out


def parse_queue_selection(queues: Union[str, List[str]]) -> List[str]:
    """Accept 'LE, EDD' or ['LE', 'EDD'] → ['LE', 'EDD']. Strips whitespace."""
    if isinstance(queues, str):
        return [q.strip() for q in queues.split(",") if q.strip()]
    return [q.strip() for q in queues if q.strip()]


def run_scenarios_from_builder(
    queues: Union[str, List[str]],
    build_fn: Callable[[str], DeterministicScenario],
    print_progress: bool = True,
    validate_first: bool = True,
) -> Dict[str, Tuple[DeterministicCycleTimeCalculator, DeterministicResult]]:
    """Build + run scenarios from a list/string of queue names and a builder fn.

    The builder function takes a queue name and returns a `DeterministicScenario`.
    This is how the comma-separated `QUEUES = "LE, EDD"` pattern calls into a
    notebook-defined `build_scenario(queue_name)` function.
    """
    names = parse_queue_selection(queues)
    scenarios = {name: build_fn(name) for name in names}
    return run_scenarios(scenarios, print_progress, validate_first)
