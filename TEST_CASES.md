# Gärtank FMU Reliability Test - reference cycle

The `reference_cycle` feasibility baseline uses the FMU with `eta_utility` and a simple reference controller; it is not the planned MILP controller. The controller cools continuously to 275.15 K, then switches cooling off until the tank warms to 287.65 K, repeating this hysteresis.

From the project root:

```bash
uv run python experiments/MILP_MPC/run_experiment_Gartank_reference_cycle.py
uv run python -m unittest discover -s experiments/MILP_MPC/tests -v
```

The configuration in
[`reference_cycle.json`](experiments/MILP_MPC/config/reference_cycle.json) expects the data in the original `rule-based-operation-strategy` repository alongside this repository, with the original 2022 ambient and tax-inclusive price CSVs in its `experiments/rule-based-optimization/data` directory. Change
`path_scenarios` if the data is elsewhere. The bundled FMU includes a Linux binary as well as a Windows binary.

The run overwrites
`experiments/MILP_MPC/results/gartank/reference_cycle_trajectory.csv`.
The first row describes the initial state; subsequent rows are interval-end
states, with the cooling action, ambient input, and price used during the
preceding 300-second interval. Temperatures are in K, powers in W, price in
EUR/kWh, interval energy in kWh and cost in EUR. Energy is approximated using
the interval-end electric power. Cost uses the price at the **start** of each
interval; the legacy output uses the end time, so costs differ at hour
boundaries.

> The provided cycle does not reach the 275.15 K target by its deadline. Its saved legacy trajectory agrees closely with the new FMU simulation, but this does not establish identical post-cooling behavior: the simplified hysteresis is tested separately and does not reproduce every detail of the old controller's scheduling logic.