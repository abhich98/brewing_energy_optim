# Implemented open-loop cooling MILP

This document describes only the model implemented in
[`cooling_milp.py`](../experiments/MILP_MPC/model/cooling_milp.py).
It is a single open-loop solve over a known price sequence.

## Indices and parameters

Let $t\in\{0,\ldots,N-1\}$ index time intervals, and let
$t\in\{0,\ldots,N\}$ index temperature states. The horizon length $N$ is the
number of prices in the configuration (below are the standard values for that represent a Fermentation cooling tank from Adrian's thesis).

| Parameter | Meaning | Configured value |
|---|---|---:|
| $\Delta t_t$ | Duration of interval $t$ | Configured per interval; 3600 s for uniform open-loop cases |
| $T_0$ | Initial tank temperature | 287.65 K (14.5 °C) |
| $T_{\mathrm{target}}$ | Target tank temperature, also the model's lower bound | 275.15 K (2 °C) |
| $T_{\max}$ | Upper model bound, derived from the last capacity breakpoint | 287.65 K (14.5 °C) |
| $\epsilon_T$ | Terminal tolerance | 0.05 K |
| $V$ | Tank volume | 460 m³ |
| $\rho$ | Beer density | 1040 kg/m³ |
| $c_p$ | Beer heat capacity | 3770 J/(kg K) |
| $\mathrm{EER}$ | Chiller efficiency ratio | 4.35 |
| $p_t$ | Electricity price in interval $t$ | Configured known sequence, €/kWh |
| $T_{\mathrm{amb},t}$ | Ambient temperature estimate in interval $t$ | Optional, K |
| $UA$ | Overall ambient heat-transfer coefficient times area | Optional, W/K; defaults to 0 |

The thermal mass used in the dynamics is $M c_p=V\rho c_p$. Absolute
temperatures and piecewise-linear breakpoints are represented in kelvin.
Temperature differences have the same numerical value in kelvin and Celsius.

_For better numerical scaling, the solver's cooling-power variables are in kW;_
the model exposes watt-valued expressions and the output remains in W.
The corresponding configuration fields use SI-cased suffixes, for example
`temperature_initial_K`, `beer_heat_capacity_J_per_kg_K`,
`interval_durations_s`, `ambient_temperatures_K`, and
`heat_transfer_UA_W_per_K`. `step_s` remains the uniform-interval duration
for configurations that do not provide `interval_durations_s`.

## Decision variables

For each state $t$, tank temperature is

$$
T_t\in[T_{\mathrm{target}},T_{\max}]\quad[\mathrm K].
$$

For each interval $t$:

- $u_t\in\{0,1\}$ is the cooling ON(1)/OFF(0) decision (`cooling_on`).
- $q_t\in[q_{\min},q_{\max}]$ is the available thermal cooling capacity [W]
  (`cooling_capacity_available_W`).
- $v_t\in[0,q_{\max}]$ is the thermal cooling power actually applied [W]
  (`cooling_power_applied_W`).
> The term “available cooling capacity” is maybe misleading, as it may imply that cooling capacity is a selectable limit. In this model, capacity is determined by the current temperature through the piecewise-linear curve; it is not an independent decision variable.

> $q_t$ - ITS PURPOSE IS STILL NOT SO CLEAR TO ME!
- $\lambda_{k,t}\geq0$ are the interpolation weights for capacity. $k$ are the indices of the breakpoints.

> Effective cooling capacity is determined by the piecewise-linear interpolation of the empirical correlation at the current temperature.

The breakpoint capacity values are calculated from the empirical correlation

$$
f(T_{\mathrm C})=16450T_{\mathrm C}^{1.33}-4603\quad[\mathrm W]
$$

Where $T_{\mathrm C}$ is the temperature in Celsius. Thus, for Kelvin breakpoint $\theta_k$, its capacity value is $q_k=f(\theta_k-273.15)$. This correlation is documented for temperatures above 2°C; the 275.15 K endpoint is used as its limiting value in this model. The breakpoints can be configured in the model's input JSON file. 

## Objective

Minimize the total electricity cost over the complete horizon:

$$
\min \quad \sum_{t=0}^{N-1}
p_t\,\frac{v_t\Delta t_t}{3.6\times10^6\,\mathrm{EER}}
$$

Here, $v_t\Delta t_s/\mathrm{EER}$ is electrical energy in joules; dividing
by $3.6\times10^6$ converts it to kWh for the configured €/kWh price.

## Constraints

### Terminal temperature tolerance

$$
T_{\mathrm{target}}\leq T_N\leq T_{\mathrm{target}}+\epsilon_T \quad [\mathrm K].
$$

The lower bound comes from the state temperature bounds; the model explicitly adds the upper terminal constraint. The tolerance is a configuration assumption and has not been confirmed as a process-accepted deviation.

### Piecewise-linear cooling capacity

For every interval, interpolation weights satisfy

$$
\sum_{k=1}^{K}\lambda_{k,t}=1,
\qquad
T_t=\sum_{k=1}^{K}\lambda_{k,t}\theta_k,
\qquad
q_t=\sum_{k=1}^{K}\lambda_{k,t}q_k,
\qquad
\lambda_{k,t}\geq0.
$$

The configured piecewise representation restricts nonzero weights to at most
two adjacent breakpoints, defining linear interpolation on each segment.
The default uses SOS2; the equivalent convex-combination (CC) MILP
representation is also supported.
The implemented formulation applies this curve to $T_t$ for interval
indices $t=0,\ldots,N-1$; it does not define a capacity variable at terminal
state $T_N$.

### Binary cooling action

The applied cooling is the product $v_t=u_tq_t$. The implementation encodes
this binary-continuous product linearly:

$$
\begin{aligned}
v_t &\leq q_{\max}u_t,\\
v_t &\geq q_{\min}u_t,\\
v_t &\leq q_t-q_{\min}(1-u_t),\\
v_t &\geq q_t-q_{\max}(1-u_t).
\end{aligned}
$$

Consequently, $v_t=q_t$ when cooling is on and $v_t=0$ when it is off.
The capacity bounds are the empirical curve values at the minimum and maximum
configured temperatures.

### Temperature dynamics

For every interval:

$$
T_{t+1}
=T_t+\frac{\Delta t_t}{V\rho c_p}
\left[UA(T_{\mathrm{amb},t}-T_t)-v_t\right],
\qquad t=0,\ldots,N-1.
$$

This is forward-Euler cooling with linear ambient heat exchange. The term is
positive when the ambient is warmer than the tank. Set $UA=0$ to recover
the original no-ambient model; otherwise an ambient estimate is required for
every interval. The model uses Kelvin states and ambient temperatures.

## Scope and implementation notes

- There are no explicit minimum-on times, switching penalties, or FMU calls
  inside the model. The same model can be used for an open-loop schedule or
  repeatedly by the deterministic MPC controller.
- Configuration validation checks finite numeric values, positive tank
  properties/EER/time step, interval durations matching price intervals,
  initial temperature above the target and no higher
  than the last breakpoint, a nonnegative terminal tolerance,
  strictly increasing breakpoints starting at the target, at least one price,
  and a nonempty solver name.
- The current configured solver is SCIP. The implementation accepts any
  available Pyomo solver with a name supplied in the configuration and only
  accepts an optimal termination as a successful solve.

## Deterministic FMU integration

Run `python experiments/MILP_MPC/run_deterministic_mpc.py` from the repository
root.

- The FMU is checked every five minutes and stops when the measured temperature
  reaches the target or the simulation deadline.
- At hour boundaries, the controller reuses the latest proven-optimal plan. It
  re-optimizes after `reoptimization_interval_hours` (2 hours), or sooner if
  the measured temperature differs from the plan by more than
  `temperature_deviation_threshold_K` (0.2 K). Reusing a plan after a deviation
  is an approximation; it may no longer be optimal for the observed state.
- If a solve is infeasible, times out, or cannot prove optimality, cooling
  fallback is logged and a new attempt is made at the configured interval. An
  SOS2 solver failure is retried using the equivalent CC formulation within
  the remaining `solver_time_limit_s` (300 seconds). No three-day solve is
  guaranteed to prove optimality within this limit.
- The model uses historical prices as **perfect foresight**, not a deployable
  forecast. It holds measured ambient temperature constant across each horizon
  and refreshes it at each solve. The heat-transfer coefficient is
  `UA=84.0505 W/K`, matching Adrian's configured values.
- Simulation start and end times may fall within a clock hour, but must align
  to the FMU sampling grid. The controller splits its optimization horizon at
  clock-hour boundaries, resulting in a partial first interval when the run
  starts off-hour and a partial final interval when its deadline is off-hour.
  It averages the environment's five-minute interval-end prices separately
  over each model interval, preserving the environment's cost-reporting
  convention.
- Controller MILP settings are under `agent_specific.cooling_milp` in
  `deterministic_mpc.json`; the open-loop validation JSON is not used. The FMU
  stopping target is `environment_specific.min_temp_storage`; the MILP target
  is adjusted by `target_temperature_offset_K`. The configured -0.1 K offset
  and 0.1 K terminal tolerance place the MILP terminal upper bound at the FMU
  target, but do not guarantee the FMU reaches it by the deadline.
- Breakpoints span the MILP target to the observed temperature. If ambient is
  warmer, the upper endpoint extends toward ambient, up to the environment's
  maximum temperature. Adjacent points are no farther apart than
  `max_temperature_breakpoints_res_K`. A MILP target below 2 °C extrapolates the
  cooling-capacity correlation beyond its documented range and raises a
  warning. Terminal tolerance is a model assumption, not the separate
  physical-minimum and strict-target formulation in the MPC plan.
- The reduced model and FMU may predict different trajectories. The
  environment saves a trajectory CSV and an episode plot.

**Some insights:**
 - **−1.0 K, two days:** the tested MILP was infeasible. **Three days:** a feasible model schedule exists, but the solver didn’t find one within the 90-second test.
  - **−0.1 K, three days:** a feasible schedule exists, and the FMU run reached its target. The **initial** solve found no solution within 60 seconds; I did **not** test this exact case for 300 seconds. Later, shorter-horizon solves did prove optimal.

Yes, computational complexity is a likely reason for the slow solves: a three-day horizon has 72 hourly on/off decisions, coupled temperature dynamics, piecewise cooling capacity, and a narrow terminal-temperature band. Finding a feasible schedule and *proving it is cheapest* are different tasks. These results don’t establish that optimality is impossible—only that it wasn’t reached within the tested limits.

**Future work:**
  - Adding an optimality tolerance to allow near-optimal solutions to be accepted. Currently, because of the complexity of the problem, the solver is not able to always prove strict optimality within the time limit (~200-300 seconds).

**The stochastic approach is still conceptually useful, but a 50-scenario MILP is not a good next step with the current formulation.**

The deterministic model already struggled to find a feasible solution for the initial three-day horizon. With 50 scenarios, much of the later-horizon model—including scenario-specific temperatures and cooling decisions—would be replicated. That would likely make both finding feasibility and proving near-optimality substantially harder.

I would first make the **single-scenario model reliably produce feasible solutions within an acceptable time**, then test a small number of scenarios and scale gradually. A practical optimality gap and time limit may help, but only after the solver can find a feasible incumbent. Based on the investigation so far, we **cannot predict** whether 50 scenarios would solve successfully.