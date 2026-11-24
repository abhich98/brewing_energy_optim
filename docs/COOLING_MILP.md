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
| $\Delta t_h$ | Interval duration | 1 h |
| $T_0$ | Initial tank temperature | 287.65 K (14.5 °C) |
| $T_{\min}$ | Minimum tank temperature | 275.15 K (2 °C) |
| $T_{\max}$ | Maximum tank temperature | 287.65 K (14.5 °C) |
| $\epsilon_T$ | Terminal tolerance | 0.05 K |
| $V$ | Tank volume | 460 m³ |
| $\rho$ | Beer density | 1040 kg/m³ |
| $c_p$ | Beer heat capacity | 3770 J/(kg K) |
| $\mathrm{EER}$ | Chiller efficiency ratio | 4.35 |
| $p_t$ | Electricity price in interval $t$ | Configured known sequence, €/kWh |

The thermal mass used in the dynamics is $M c_p=V\rho c_p$. Absolute
temperatures and piecewise-linear breakpoints are represented in kelvin.
Temperature differences have the same numerical value in kelvin and Celsius.

## Decision variables

For each state $t$, tank temperature is

$$
T_t\in[T_{\min},T_{\max}]\quad[\mathrm K].
$$

For each interval $t$:

- $u_t\in\{0,1\}$ is the cooling ON/OFF decision.
- $q_t\in[q_{\min},q_{\max}]$ is the available thermal cooling capacity [W].
- $v_t\in[0,q_{\max}]$ is the thermal cooling power actually applied [W].
> The term “available cooling capacity” is maybe misleading, as it may imply that cooling capacity is a selectable limit. In this model, capacity is determined by the current temperature through the piecewise-linear curve; it is not an independent decision variable.
- $\lambda_{k,t}\geq0$ are the SOS2 interpolation weights for capacity. $k$ are the indices of the breakpoints.

> Effective cooling capacity is determined by the piecewise-linear interpolation of the empirical correlation at the current temperature.

The breakpoint capacity values are calculated from the empirical correlation

$$
f(T_{\mathrm C})=16450T_{\mathrm C}^{1.33}-4603\quad[\mathrm W]
$$

Where $T_{\mathrm C}$ is the temperature in Celsius. Thus, for Kelvin breakpoint $\theta_k$, its capacity value is $q_k=f(\theta_k-273.15)$. This correlation is documented for temperatures above 2°C; the 275.15 K endpoint is used as its limiting value in this model. The breakpoints can be configured in the model's input JSON file. 

## Objective

Minimize the total electricity cost over the complete horizon:

$$
\min \quad
\sum_{t=0}^{N-1}
p_t\,
\frac{v_t\Delta t_h}{1000\,\mathrm{EER}}
$$

Here, $v_t\Delta t_h/(1000\,\mathrm{EER})$ is electrical energy in kWh, so
each term is interval cost in euros.

## Constraints

### Terminal temperature tolerance

$$
T_{\min}\leq T_N\leq T_{\min}+\epsilon_T \quad [\mathrm K].
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

An SOS2 condition on $\lambda_{k,t}$ restricts nonzero weights to at most two
adjacent breakpoints, defining linear interpolation on each segment.
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
=T_t-\frac{3600\Delta t_h}{V\rho c_p}v_t,
\qquad t=0,\ldots,N-1.
$$

This is forward-Euler cooling with no ambient heat-transfer term. The model
uses the Kelvin temperature state; only temperature differences occur in the
dynamics.

## Scope and implementation notes

- There are no explicit minimum-on times, switching penalties, ambient
  temperature inputs, or FMU calls.
- The model is a single open-loop schedule, not an MPC loop.
- Configuration validation checks finite numeric values, positive tank
  properties/EER/time step, initial temperature within bounds, a nonnegative
  terminal tolerance within the temperature range, strictly increasing
  breakpoints spanning the temperature bounds, at least one price, and a
  nonempty solver name.
- The current configured solver is SCIP. The implementation accepts any
  available Pyomo solver with a name supplied in the configuration and only
  accepts an optimal termination as a successful solve.