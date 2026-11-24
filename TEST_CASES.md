# Gärtank FMU Reliability Test - reference cycle

The `reference_cycle` feasibility baseline uses the FMU with `eta_utility` and a simple reference controller; it is not the planned MILP controller. The controller cools continuously to 275.15 K, then switches cooling off until the tank warms to 287.65 K, repeating this hysteresis.

From the project root:

```bash
uv run python experiments/MILP_MPC/run_experiment_Gartank_reference_cycle.py
uv run python -m unittest discover -s experiments/MILP_MPC/tests -v
```

The configuration in
[`reference_cycle.json`](experiments/MILP_MPC/config/reference_cycle.json) expects the data in the original `rule-based-operation-strategy` repository alongside this repository, with the original 2022 ambient and tax-inclusive price CSVs in its `experiments/rule-based-optimization/data` directory. Change
`relpath_scenarios` if the data is elsewhere.

The run overwrites
`experiments/MILP_MPC/results/gartank/reference_cycle_trajectory.csv`.
The first row describes the initial state; subsequent rows are interval-end states, with the cooling action applied during the preceding time interval. As in the legacy output, ambient temperature and price are recorded at the interval-end timestamp; the ambient input used by the FMU during that interval comes from the preceding timestamp. Temperatures are in K, powers in W, price in EUR/kWh, interval energy in kWh and cost in EUR. Energy is approximated using the interval-end electric power. Cost uses the price at the **end** of each interval to match the legacy calculation, including at hourly price changes.
_This convention may differ from charging the interval at its start-time price._

> The provided cycle does not reach the 275.15 K target by its deadline. Its saved legacy trajectory agrees closely with the new FMU simulation, but this does not establish identical post-cooling behavior: the simplified hysteresis is tested separately and does not reproduce every detail of the old controller's scheduling logic.

# Open-loop MILP validation

The standalone Pyomo model solves a single 36-hour schedule with hourly
ON/OFF actions, synthetic known prices, a piecewise-linear cooling capacity
and no ambient heat exchange. It does not run the FMU or implement MPC.
The deadline range is 275.15–275.25 K; the 0.1 K tolerance is a validation
assumption and needs process confirmation.

SCIP must be installed as an executable named `scip` on `PATH`. From the
project root:

```bash
uv sync
uv run python experiments/MILP_MPC/run_cooling_milp_open_loop_validation.py
uv run python -m unittest discover -s experiments/MILP_MPC/tests -p test_cooling_milp.py -v
```

The solver must prove optimality; infeasible cases and solver failures are
reported rather than exported as schedules. The runner overwrites
`experiments/MILP_MPC/results/open_loop/schedule.csv`; use `--output PATH` to
choose another location and `--config PATH` for a different configuration.
The CSV has one row per interval, with start and end times in hours, start and
end temperatures in K, the binary action, cooling powers in W, electric
energy in kWh, price in EUR/kWh and cost in EUR. Each synthetic price applies
to the interval in its row; this is not a replay of the legacy price CSV.

The tests cover optimal variable-price and flat-price cases, an infeasible
10-hour deadline, binary actions, bounds, the terminal band, dynamics,
piecewise interpolation and independently recalculated energy and cost. The
selected breakpoints give less than 1% relative error on a 10,001-point
temperature grid against the empirical curve. Absolute temperatures and
breakpoints are in K; the empirical curve is evaluated after converting them
to °C. The curve is documented for temperatures above 2°C; the 275.15 K
endpoint is used as its limiting value.
The reduced model and its schedules have **not** yet been validated against
the FMU.