# Brewery Cooling Optimization

This project is building a **model-predictive control** (MPC) system for scheduling
cooling in an industrial brewery fermentation tank. The aim is to coordinate
the tank’s thermal behavior with electricity prices while respecting a
production deadline. The Gärtank FMU is used as the simulated plant; a separate
Pyomo model predicts temperature changes and chooses whether cooling should be
on or off.

## What is implemented

The current deterministic prototype connects the FMU to an ETAx controller and
a Pyomo mixed-integer linear program (MILP). The controller receives FMU
measurements at five-minute intervals, plans over hourly electricity-price
periods, and applies the selected cooling action to the simulator. The model
includes an empirical, piecewise-linear cooling-capacity curve and optional
linear heat exchange with ambient air. It can split its horizon into
variable-duration intervals, so a simulation may start or finish between
clock hours while remaining aligned to the FMU sampling grid.

The current price input is historical data treated as perfect foresight; a
realistic price forecast is not yet integrated. Episodes produce trajectory
data, plots, and logs. Focused tests cover the optimization and controller
behavior, but long horizons remain computationally challenging: a solve that
does not prove optimality is not currently accepted as a usable plan.

## What is planned

The next major step is stochastic MPC. The MILP will represent uncertain
electricity prices with weighted future scenarios, share near-term actions
across scenarios, and account for deadline progress through hard intermediate
temperature requirements. XGBoost price forecasting is planned, with
chronological training and validation and models updated through offline
snapshots during the 2025 evaluation. The MPC-facing forecast should provide
complete, timestamp-aligned price trajectories and scenario probabilities;
the method for generating and calibrating those scenarios remains under
research. Weather estimates must use only information available at forecast
time, and the controller will continue to use measured FMU temperatures as
feedback. Solver performance, acceptable optimality gaps, and scenario counts
also need evaluation before the full stochastic workflow is considered ready.

![Conceptual brewery cooling MPC workflow](docs/mermaid-plan-diagram.png)

On Linux x86-64, `uv` installs the CPU build of PyTorch. The project pins `marshmallow` and `polars` to versions compatible with the weather dependencies used by the current `eta_utility` installation.

The bundled FMU includes a Linux binary as well as a Windows binary.

# Considerations and Gotchas

- The project uses exact SI unit casing for naming variables and configuration fields, such as `temperature_initial_K`, `beer_heat_capacity_J_per_kg_K`, `ambient_temperatures_K`, and `heat_transfer_UA_W_per_K`.

- Following the legacy output, when the fmu temperature is recorded, ambient temperature and price are recorded at the interval-end timestamp; the ambient input used by the FMU during that interval comes from the preceding timestamp. Temperatures are in K, powers in W, price in EUR/kWh, interval energy in kWh and cost in EUR. Energy is approximated using the interval-end electric power. Cost uses the price at the **end** of each interval to match the legacy calculation, including at hourly price changes.

- The reference-cycle configuration uses ETAx's `relpath_scenarios` key for the scenario data directory. Despite the key's name, it can contain either a path relative to the experiment root or an absolute path.

- I used `RuleBased` in MILP-based controller as an ETAx integration interface, not because your optimizer is rule-based. Its name is misleading in this context. ETAx calls the parent class’s `predict()` at each FMU step; that method calls our controller’s control_rules() (RuleBased). In our implementation, that callback:

    - Checks the measured temperature every five minutes.
    - Calls the Pyomo MILP every hour and applies its first action.
    - Holds that action between solves, unless the target is reached.
    - So the scheduling decision is optimization-based; the surrounding logic includes rules for timing, stopping and fallback (MILPController).

- I could use `MathSolver`, a Pyomo-oriented MathSolver. But, I chose RuleBased because the current MathSolver expects its model from the environment, solves on every `predict()` call, and exits on a non-optimal solve. Our integration instead needs hourly solves, five-minute FMU steps and an explicit fallback. It’s a practical choice, not a requirement. I may consider adapting MathSolver in the future if needed.