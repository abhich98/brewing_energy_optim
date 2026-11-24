"""Solve the configured open-loop cooling schedule and save its intervals."""

import argparse
import csv
from pathlib import Path

import pyomo.environ as pyo
from pyomo.opt import TerminationCondition

from model.cooling_milp import CoolingConfig, build_model, schedule_rows, solve_model


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "config/cooling_milp_open_loop_validation.json")
    parser.add_argument("--output", type=Path, default=root / "results/cooling_milp/open_loop_schedule.csv")
    args = parser.parse_args()
    config = CoolingConfig.from_json(args.config)
    model = build_model(config)
    result = solve_model(model, config.solver)
    print(f"Solver status: {result.solver.status}; termination: {result.solver.termination_condition}")
    if result.solver.termination_condition == TerminationCondition.infeasible:
        raise SystemExit("No feasible schedule: check the deadline, bounds, and tolerance.")
    rows = schedule_rows(model, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Schedule: {args.output}")
    print(f"Cooling intervals: {sum(int(row['cooling_on']) for row in rows)}/{len(rows)}")
    print(f"Final temperature: {pyo.value(model.temperature[len(rows)]):.4f} K")
    print(f"Electricity cost: {pyo.value(model.cost):.4f} EUR")


if __name__ == "__main__":
    main()
