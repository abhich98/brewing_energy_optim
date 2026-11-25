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

- Could we use `MathSolver` instead? Yes, ETAx provides a Pyomo-oriented MathSolver. I chose RuleBased because the current MathSolver expects its model from the environment, solves on every `predict()` call, and exits on a non-optimal solve. Our integration instead needs hourly solves, five-minute FMU steps and an explicit fallback. It’s a practical choice, not a requirement. I may consider adapting MathSolver in the future if needed.