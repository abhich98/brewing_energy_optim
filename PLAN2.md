## Plan: stochastic MILP-MPC

Build a **single-tank, closed-loop controller** using the existing Gärtank FMU as the plant and a stochastic Pyomo MILP as the prediction model. Cycle times, physical limits, target, horizon, solver settings, and intermediate tolerance will be configurable.

### 1. Forecasts and uncertainty

- Use XGBoost with the user-provided **2025 price datasets**.
- Treat the first configurable 24 hours as known prices and represent the following 48 hours with weighted scenarios.
- **Design gate:** decide how to generate and calibrate scenarios before implementing the forecast provider. Prevent future realized prices from leaking into forecasts.
- Provide an ambient-temperature estimate for every interval of the full prediction horizon (e.g. 1–3 days) for the heat-exchange model. A constant estimate is a simple option; a time-varying forecast is another.
- As a candidate, adapt Adrian’s day-ahead method: average temperatures at corresponding times over previous days and shift the forecast so its initial value matches the current measurement. His cycle-1 configuration uses three previous days. Extend the method carefully for multi-day horizons using only data available at the forecast time.
- Validate timestamps, units, probabilities, missing data, and daylight-saving transitions.

#### Specifics
- **Predictors**: calendar and holidays, prices already published, and recent observations from the four weather locations. Estimate future weather from past observations rather than using future actual weather.
- **Training**: expanding-window validation across 2024, followed by offline monthly model snapshots for 2025. Each snapshot uses only information available by its cutoff.
- **MPC input**: known published prices plus configurable, probability-weighted trajectories for D+2 and D+3. Confidence intervals may supplement these, but cannot replace joint scenarios.
- **Open research**: how to generate and calibrate scenarios. Validate timestamp alignment, daylight-saving transitions, and the assumed 13:00 publication time.


### 2. Stochastic cooling MILP

Extend `cooling_milp.py` into a two-stage **prediction and optimization model**. It receives the current measured temperature, time intervals, price scenarios and probabilities, ambient inputs, physical limits, target, and the applicable terminal requirement from the controller. It returns a solver status and, when a solution is accepted, predicted temperatures and cooling actions.

- Share cooling decisions across scenarios for the first configurable 24 hours; allow scenario-specific decisions afterward. Minimize expected electricity cost over the full horizon.
  - Later prices can influence the shared decisions: the optimizer accounts for the cost of subsequent scenario-specific actions when choosing the first-stage schedule.
  - The two-stage model assumes uncertainty is revealed after the first stage; this approximates gradual price revelation.
- Predict temperature using the cooling-capacity model and configurable linear ambient heat exchange, $UA(T_{\mathrm{ambient}}-T)$, following Adrian’s approach. Calibration against the FMU is not a prerequisite; this is a prediction-model assumption, not a verified description of the FMU internals.
- Enforce the **hard terminal requirement supplied by the controller**:
  - If the horizon ends before the deadline, a scenario that has not already completed must meet the intermediate upper bound calculated in section 3. Its tolerance is part of that bound, not a soft penalty or objective weight.
  - If the deadline is within the horizon, require predicted completion by the deadline within the physical minimum-to-target band, with **no additional tolerance above the target**. A scenario that completes earlier is already complete and does not require further predicted cooling.

Discrete cooling actions can still make either requirement infeasible. Meeting an intermediate milestone does **not guarantee** eventual deadline feasibility.


### 3. Controller and FMU integration

Implement the controller in `MILP_based_controller_GT.py`, using ETAx’s agent interface. It orchestrates each closed-loop cycle:

1. Observe the FMU state and, on each configurable re-optimization interval, assemble the prediction horizon, price scenarios, ambient-temperature estimates and other inputs for the MILP.
2. Select the applicable terminal requirement:
   - If the horizon ends before the deadline, calculate an intermediate milestone using Adrian’s fourth-power curve without an initial offset and with a −1 K final offset. For initial cycle temperature $T_{\mathrm{start}}$, target $T_{\mathrm{target}}$, and elapsed cycle fraction $x$ between 0 and 1:

     $$T_{\mathrm{curve}}(x) = T_{\mathrm{start}} + \left[1-(1-x)^4\right]\left(T_{\mathrm{target}} - 1\,\mathrm{K} - T_{\mathrm{start}}\right).$$

     The curve starts at the actual initial temperature but falls below the target before the deadline. Clamp the milestone no lower than the target and pass the resulting upper bound to the MILP:

     $$T_{\mathrm{intermediate}} = \max\left(T_{\mathrm{target}}, T_{\mathrm{curve}}(H)\right) + \epsilon_{\mathrm{intermediate}}.$$

     Here $H$ is the horizon endpoint and the curve is evaluated at its elapsed cycle fraction. The configurable, nonnegative tolerance can help with discrete-action feasibility; it is separate from the deadline requirement and does not guarantee feasibility.
   - If the deadline is within the horizon, pass the physical minimum-to-target completion requirement instead, without an above-target tolerance.
3. Solve the MILP and apply the relevant **shared first-stage action** to the FMU. Scenario-specific later actions are predictions, not actions selected for immediate execution; re-observe and re-solve as the episode progresses.
4. Keep the FMU at five-minute steps and check the measured temperature at each step. Follow Adrian’s distinction between **clock-hour price/action periods** and elapsed re-optimization intervals, including partial hours.
5. End when the measured temperature reaches or falls below the target, or at the deadline. Record target misses and any undershoot.
6. If the solve is infeasible or ends without an accepted solution, cool toward the target and flag the fallback. If the solver is unavailable, stop with an error.

Update `simulation_environment_Gartank.py` to support the FMU stepping, early completion and reporting without changing the existing reference-controller behavior. Five-minute checks cannot guarantee that the physical minimum is never crossed; report violations explicitly.

### 4. Configuration, outputs, and tests

Add an MPC experiment configuration and runner, plus forecast/scenario modules and tests. Log actual and predicted temperatures, actions, prices, scenario probabilities, solver status, progress milestones and tolerance, fallback events, energy, cost, and completion reason.

Tests should cover scenario non-anticipativity and the influence of later prices on shared decisions, price alignment, price and ambient forecast leakage and horizon coverage, intermediate milestones and tolerance, short and long deadlines, early target crossing, infeasibility and fallback, missing solver, and one complete FMU run. Preserve the existing legacy cost-reporting convention for comparisons.

Update `COOLING_MILP.md` and `TEST_CASES.md` with the stochastic formulation, assumptions, and run instructions.

**Open decision:** the XGBoost scenario-generation method remains a gated design choice. No implementation has started.