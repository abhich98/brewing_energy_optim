# Mathematical Formulation: Brewery Fermentation Cooling Optimisation

## 1. Problem definition

The objective is to minimise the electricity cost of fermentation-tank cooling by scheduling cooling operation around variable electricity prices, while meeting the required beer temperature and production deadline.

The project focuses on the **main cooling phase** of the secondary fermentation process at Veltins:

- Initial beer temperature: $14.5^\circ\mathrm C$
- Target beer temperature: $2^\circ\mathrm C$
- All five cooling zones are active during this phase.
- Cooling can be interrupted below $14.5^\circ\mathrm C$ without affecting product quality, according to the brewery information reported in the dissertation.

The formulation is based on Adrian von Hayn's 2025 dissertation. It distinguishes the dissertation's physical parameters and empirical relationships from the reduced-order model and MILP-MPC extensions proposed for this project.

### 1.1 System parameters

| Parameter | Symbol | Value |
|---|---|---:|
| Tank volume | $V_{\mathrm{GT}}$ | $460\mathrm{m^3}$ |
| Tank height | $h_{\mathrm{GT}}$ | $22.75\mathrm m$ |
| Beer density | $\rho_{\mathrm{beer}}$ | $1040\mathrm{kg/m^3}$ |
| Beer specific heat capacity | $c_p$ | $3770\mathrm{J/(kgK)}$ |
| Initial temperature | $T_0$ | $14.5^\circ\mathrm C$ |
| Target temperature | $T_{\mathrm{target}}$ | $2^\circ\mathrm C$ |
| Refrigerant | — | Ammonia (R717) |
| Refrigerant evaporation temperature | $T_{\mathrm{eva}}$ | $-4^\circ\mathrm C$ |
| Refrigeration system EER | $\epsilon_{\mathrm{KKM}}$ | $4.35$ |
| Number of active cooling zones | — | 5 |

The beer mass is calculated as:

$$
M=V_{\mathrm{GT}}\rho_{\mathrm{beer}}
$$

$$
\boxed{M=460\times1040=478400\ \mathrm{kg}}
$$

### 1.2 Modelling assumptions

For the initial optimisation model:

1. One representative fermentation tank, GT01, is modelled independently.
2. Beer temperature is represented by a single state variable.
3. Cooling is either fully active or switched off.
4. No beer enters or leaves the tank during the main cooling phase.
5. Cooling capacity depends on the current beer temperature.
6. The refrigeration system operates with a constant EER of 4.35.
7. Ambient heat gains are initially neglected in the simplified model, with a sensitivity analysis used to evaluate their effect.

**Model simplification:** The dissertation uses a three-layer stratified tank model implemented in Dymola using the AixLib library. The single-temperature model developed here is a reduced-order approximation intended to make optimisation computationally tractable. It should be validated against the dissertation's more detailed model or its published reference behaviour.


## 2. Thermal process model

### 2.1 Energy balance

The general energy balance for the tank is:

$$
\frac{dU}{dt} = \dot Q_{\mathrm{ambient}} - \dot Q_{\mathrm{cooling}}
$$

For a simplified well-mixed tank with constant mass and specific heat capacity:

$$ Mc_p\frac{dT}{dt} = \dot Q_{\mathrm{ambient}} - u(t)\dot Q_{\mathrm{cool}}(T) $$

where:

| Variable | Description | Unit |
|---|---|---|
| $U$ | Internal energy of the beer | J |
| $M$ | Mass of beer | kg |
| $c_p$ | Specific heat capacity | J/(kg K) |
| $T$ | Beer temperature | °C |
| $\dot Q_{\mathrm{ambient}}$ | Heat entering the tank | W |
| $\dot Q_{\mathrm{cool}}$ | Available cooling capacity | W |
| $u(t)$ | Binary cooling control | — |

The cooling control is:

$$
u(t)\in\{0,1\}
$$

$$
u(t)=
\begin{cases}
1 & \text{Cooling active}\\
0 & \text{Cooling inactive}
\end{cases}
$$

The dissertation estimates that environmental heat input during the fermentation-tank cooling process is less than 6.3% of the thermal cooling energy. It neglects this contribution when identifying the empirical cooling-capacity function.

For the initial reduced-order model, we can therefore use:

$$
\dot Q_{\mathrm{ambient}}=0
$$

and investigate the effect of this assumption separately.

### 2.2 Temperature-dependent cooling capacity

The dissertation estimates the cooling capacity from historical tank-temperature measurements.

The thermal energy change over a time interval is:

$$
\frac{\Delta U_{\mathrm{GT}}}{\Delta t}
=
V_{\mathrm{GT}}\rho_{\mathrm{beer}}c_p
\frac{\Delta T_{\mathrm{GT}}}{\Delta t}
$$

Since temperature decreases during cooling, the energy change is negative. The cooling-capacity function is therefore fitted using its positive magnitude.

The empirical relationship is:

$$
\dot Q_{\mathrm{cool}}(T)=aT^b+c
$$

The fitted parameters are:

$$
a=16450,\qquad b=1.33,\qquad c=-4603
$$

Therefore:

$$
\boxed{\dot Q_{\mathrm{cool}}(T) = 16450T^{1.33}-4603}
$$

where:

- $T$ is in °C.
- $\dot Q_{\mathrm{cool}}$ is in W.
- The function is valid for the main cooling phase, specifically $T>2^\circ\mathrm C$.

The parameters were identified using the 28 main cooling cycles of GT01 during 2022.

**Source:** von Hayn (2025), Section 7.1.3.2, Equations 7-4 to 7-6, printed pages 147–149.

### 2.3 Discrete-time thermal model

For optimisation, the thermal model is discretised into hourly time steps.

Using a forward Euler approximation:

$$
T_{t+1} = T_t + \frac{\Delta t_s}{Mc_p} \left[ \dot Q_{\mathrm{ambient},t} - u_t\dot Q_{\mathrm{cool}}(T_t) \right]
$$

where $\Delta t_s$ is the time step in seconds.

For hourly discretisation:

$$
\Delta t_s=3600\ \mathrm{s}
$$

With ambient heat gains neglected:

$$
\boxed{ T_{t+1} = T_t - \frac{3600}{Mc_p} u_t\dot Q_{\mathrm{cool}}(T_t) }
$$

This is a proposed reduced-order discrete-time model derived from the dissertation's thermal modelling principles. It is not an equation copied verbatim from the dissertation.



## 3. Electrical power and energy-cost model

### 3.1 Electrical power

The dissertation assumes a constant refrigeration-system energy efficiency ratio:

$$
\mathrm{EER}=4.35
$$

The electrical power required to provide the cooling capacity is:

$$
P_{\mathrm{electric}} = \frac{\dot Q_{\mathrm{cool}}}{\mathrm{EER}}
$$

Since the cooling capacity is expressed in watts, electrical power in kilowatts is:

$$
\boxed{ P_t = \frac{u_t\dot Q_{\mathrm{cool}}(T_t)} {1000\times4.35} }
$$

where $P_t$ is in kW.

### 3.2 Electricity cost

Let:

- $p_t$: gross electricity price in €/kWh.
- $P_t$: electrical power in kW.
- $\Delta t_h$: time step in hours.

The electricity cost over one cooling cycle is:

$$
\boxed{ C= \sum_{t=0}^{N-1}p_tP_t\Delta t_h }
$$

Substituting the electrical power equation:

$$ \boxed{ C = \sum_{t=0}^{N-1}p_t \frac{u_t\dot Q_{\mathrm{cool}}(T_t)} {1000\times4.35} \Delta t_h }
$$

The optimisation objective is to minimise electricity cost rather than necessarily minimise electricity consumption.

The controller can consume approximately the same amount of electricity while reducing costs by shifting cooling operation to cheaper hours.



## 4. Production constraints

The cooling process must reach the target temperature before the production deadline.

For each cooling cycle $j$, define:

| Variable | Description |
|---|---|
| $t_j^{\mathrm{start}}$ | Start of the main cooling phase |
| $t_j^{\mathrm{ref,end}}$ | Reference end time under continuous cooling |
| $D_j$ | Usable production pause after the reference cooling process |
| $t_j^{\mathrm{deadline}}$ | Latest permitted completion time |

The dissertation defines the optimisation deadline as:

$$ 
\boxed{ t_j^{\mathrm{deadline}} = t_j^{\mathrm{ref,end}}+D_j }
$$

The usable production pause is cycle-specific. Its reported average is 33 hours, with a range of 0–149 hours.

### 4.1 Initial temperature

At the start of each cooling cycle:

$$ 
\boxed{ T_{t_j^{\mathrm{start}}}=14.5 }
$$

### 4.2 Temperature limits

During the main cooling phase:

$$
\boxed{2\leq T_t\leq14.5}
$$

### 4.3 Deadline constraint

The target temperature must be reached by the deadline:

$$
\boxed{ T_{t_j^{\mathrm{deadline}}}\leq2 }
$$

Together with the lower temperature bound, this means the terminal temperature is exactly 2°C.

The model should only apply these main-phase constraints over the interval being optimised. Subsequent cooling from 2°C towards approximately −1.5°C is outside the scope of this project.

**Source:** von Hayn (2025), Sections 8.1.1.1 and 8.1.1.2, printed pages 171–173.



## 5. Baseline: Rule-based Model Predictive Control (RBMPC)

The dissertation implements a rule-based model predictive control framework.

Its central idea is to estimate future cooling demand and use electricity-price information to identify economically favourable operating periods.

For this project, the baseline controller should follow the same underlying methodology.

At each control update:

1. Read the current tank temperature.
2. Obtain the electricity-price forecast.
3. Estimate the remaining thermal cooling demand.
4. Estimate the cooling duration and electrical energy demand.
5. Identify cost-effective cooling time windows.
6. Decide whether to operate or interrupt cooling.
7. Execute the decision for one time step.
8. Update the tank state and repeat.

A simplified representation of the control decision is:

$$
u_t=
\begin{cases}
1 & \text{if cooling is economically preferable}\\
0 & \text{otherwise}
\end{cases}
$$

subject to the temperature and production-deadline constraints.

**Important:** This is a conceptual representation, not the complete mathematical algorithm from the dissertation. The actual RBMPC implementation includes additional calculations for energy-demand estimation, cooling duration and operating windows. The baseline should be reconstructed from Chapter 5, particularly Sections 5.2–5.4, before it is described as an exact reproduction.



## 6. Proposed contribution: MILP-based MPC

The proposed contribution is to replace the rule-based scheduling decision with a Mixed-Integer Linear Programming (MILP) formulation.

The challenge is that the cooling-capacity function is nonlinear:

$$
\dot Q_{\mathrm{cool}}(T) = 16450T^{1.33}-4603
$$

A conventional MILP cannot directly represent this nonlinear function. We therefore approximate it using a piecewise-linear formulation.

### 6.1 Piecewise-linear cooling-capacity approximation

Define temperature breakpoints:

$$
\theta_1,\theta_2,\ldots,\theta_K
$$

Calculate the cooling capacity at each breakpoint:

$$
q_k=16450\theta_k^{1.33}-4603
$$

Introduce interpolation variables $\lambda_{k,t}$:

$$
\dot Q_{\mathrm{cool}}(T_t)
\approx
\sum_{k=1}^{K}\lambda_{k,t}q_k
$$

subject to:

$$
\sum_{k=1}^{K}\lambda_{k,t}=1
$$

$$
T_t=\sum_{k=1}^{K}\lambda_{k,t}\theta_k
$$

$$
\lambda_{k,t}\geq0
$$

An SOS2 constraint ensures that only two adjacent temperature breakpoints can have non-zero interpolation weights.

This produces a piecewise-linear approximation of the cooling-capacity function.

### 6.2 Linearisation of the cooling ON/OFF decision

The thermal model contains the product:

$$
u_t\dot Q_{\mathrm{cool}}(T_t)
$$

This is a product of a binary variable and a continuous variable. It must also be linearised.

Introduce:

$$
q_t\approx\dot Q_{\mathrm{cool}}(T_t)
$$

$$
v_t=u_tq_t
$$

Let $q_{\min}$ and $q_{\max}$ be valid lower and upper bounds on cooling capacity over the temperature interval $[2,14.5]$.

The product can be represented exactly for binary $u_t$ using:

$$
v_t\leq q_{\max}u_t
$$

$$
v_t\geq q_{\min}u_t
$$

$$
v_t\leq q_t-q_{\min}(1-u_t)
$$

$$
v_t\geq q_t-q_{\max}(1-u_t)
$$

These constraints ensure:

$$
v_t=
\begin{cases}
q_t & u_t=1\\
0 & u_t=0
\end{cases}
$$

The resulting formulation is linear, provided the cooling-capacity approximation is piecewise linear and the thermal dynamics use this auxiliary variable.

### 6.3 MILP objective function

The objective is to minimise total electricity cost:

$$
\boxed{
\min
\sum_{t=0}^{N-1}
p_t
\frac{v_t}
{1000\mathrm{EER}}
\Delta t_h
}
$$

where:

- $p_t$ is the electricity price in €/kWh.
- $v_t$ is the active cooling capacity in W.
- EER is 4.35.
- $\Delta t_h$ is the time step in hours.

### 6.4 MILP constraints

**Thermal dynamics**

With ambient heat gains neglected:

$$
\boxed{ T_{t+1} = T_t - \frac{\Delta t_s}{Mc_p}v_t }
$$

**Cooling control**

$$
u_t\in\{0,1\}
$$

**Temperature limits**

$$
2\leq T_t\leq14.5
$$

**Initial temperature**

$$
T_{t_j^{\mathrm{start}}}=14.5
$$

**Deadline constraint**

$$
T_{t_j^{\mathrm{deadline}}}\leq2
$$

**Cooling-capacity approximation**

$$
q_t=\sum_{k=1}^{K}\lambda_{k,t}q_k
$$

$$
T_t=\sum_{k=1}^{K}\lambda_{k,t}\theta_k
$$

$$
\sum_{k=1}^{K}\lambda_{k,t}=1
$$

$$
\lambda_{k,t}\geq0
$$

with SOS2 adjacency constraints.

This formulation gives a linear optimisation model with binary cooling decisions, continuous temperature states and piecewise-linear cooling capacity.



## 7. Embedding the MILP in MPC

A single MILP can optimise the complete cooling schedule for one cycle. However, the proposed controller should use a rolling-horizon approach.

At every control update $t$:

1. Obtain the current tank temperature $T_t$.
2. Obtain the latest electricity-price forecasts.
3. Solve the MILP over the remaining optimisation horizon.
4. Implement only the first cooling decision $u_t$.
5. Advance the simulation by one time step.
6. Update the temperature and price forecasts.
7. Solve the optimisation problem again.

At each update, the optimisation objective becomes:

$$
\min
\sum_{\tau=t}^{t+H-1}
p_{\tau|t}P_{\tau|t}\Delta t_h
$$

subject to the thermal dynamics, cooling constraints and production deadline.

Here, $p_{\tau|t}$ represents the forecast electricity price for time $\tau$, generated at update time $t$.

### 7.1 Different time horizons

It is important to distinguish three different horizons:

| Concept | Meaning | Proposed experiments |
|---|---|---|
| Forecast horizon | How far ahead electricity prices are predicted | 24, 48, 72, 96, 168 h |
| Optimisation horizon | How far ahead cooling is scheduled | Remaining time until the cycle deadline |
| Re-optimisation interval | How often the controller updates | Initially 1 h |

The average main cooling duration is approximately 107 hours, or 4.5 days. This motivates investigating multi-day price forecasts.

If the optimisation horizon ends before the production deadline, an appropriate terminal constraint or terminal estimate of remaining cooling demand is required. Otherwise, the optimiser may postpone cooling beyond its planning horizon.



## 8. Proposed model architecture

The overall architecture consists of five components:

1. **Price forecasting:** SARIMA, XGBoost and selected time-series foundation models.
2. **Thermal simulation:** Reduced-order tank-temperature model.
3. **Cooling-capacity model:** Empirical temperature-dependent cooling function.
4. **Optimisation:** RBMPC baseline and proposed MILP-MPC.
5. **Evaluation:** Cost, energy consumption, temperature compliance, deadline compliance and computation time.

The same thermal model, production constraints and price information should be used when comparing the two controllers. This ensures that differences in performance can be attributed to the control strategy.



## 9. Evaluation methodology

The project should evaluate the controllers using the following metrics:

| Metric | Purpose |
|---|---|
| Total electricity cost (€) | Primary optimisation objective |
| Electricity consumption (kWh) | Quantifies energy use |
| Cost reduction (%) | Measures economic improvement |
| Energy reduction (%) | Measures change in electricity demand |
| Temperature constraint violations | Checks process feasibility |
| Deadline compliance | Checks production feasibility |
| Cooling interruption duration | Measures operational flexibility |
| Computation time (s) | Evaluates optimisation performance |
| Forecast error | Evaluates price prediction quality |

The reference case should represent continuous cooling at full cooling capacity from 14.5°C to 2°C.

The optimised cases should use the same cycle start times and cycle-specific deadlines as the reference schedule.

### 9.1 Replay simulation

The project uses GT01 cooling-cycle timings derived from 2022 and applies electricity prices from October 2025 to September 2026.

This is a **replay simulation**, not a reconstruction of actual brewery operations during 2025–2026. The 2022 operating schedule is reused with a different electricity-price period to investigate how forecasting and scheduling strategies respond to newer price patterns.

This distinction should be stated clearly in the final report.



## 10. Important modelling decisions

Before implementing the optimisation, the following tasks should be completed:

1. **Validate the reduced-order thermal model.** Compare its temperature trajectories against the dissertation's three-layer stratified tank model or published GT01 reference trajectories.
2. **Validate the cooling-capacity function.** Compare the piecewise-linear approximation against the original empirical power function over the complete operating temperature range.
3. **Test the ambient heat assumption.** Compare results with and without ambient heat gains.
4. **Reconstruct the RBMPC baseline.** Use the actual rules and calculations in Chapter 5 rather than relying on the simplified conceptual decision rule.
5. **Validate MILP feasibility.** Check the temperature dynamics, binary-continuous linearisation, deadline constraints and terminal behaviour.
6. **Use consistent evaluation conditions.** Both controllers must use the same thermal model, cycle schedules, price inputs and process constraints.



## 11. Dissertation references

Adrian von Hayn (2025), *Regelbasierte Energiekostenoptimierung industrieller Kälteversorgungssysteme durch Reaktion auf volatile Strompreise am Beispiel des Brauprozesses*, Technische Universität Darmstadt.

| Topic | Dissertation section | Printed pages / equations |
|---|---|---|
| RBMPC methodology | Chapter 5 | Sections 5.2–5.4 |
| Thermal energy balance | Chapter 5 | Equation 5-5 |
| Fermentation-tank model | Section 7.1.3.1 | Pages 144–145 |
| Empirical cooling-capacity model | Section 7.1.3.2 | Equations 7-4 to 7-6, pages 147–149 |
| Model validation | Section 7.1.3.3 | Page 150 onwards |
| Reference cooling schedule | Section 8.1.1.1 | Page 171 |
| Production deadlines and optimisation | Section 8.1.1.2 | Pages 172–173 |
| Annual evaluation | Section 8.1.1.2 | Pages 175–176 |

**Public dissertation:** https://tuprints.ulb.tu-darmstadt.de/bitstreams/114d1262-4e18-4ad1-a1a1-20ba75c71c05/download

**Associated research data:** https://tudatalib.ulb.tu-darmstadt.de/items/1c2145d5-70e1-4125-8b83-6fece3867bad



## 12. Summary

The mathematical framework has two main parts:

- **Baseline:** Reproduction of the dissertation's rule-based MPC methodology.
- **Proposed contribution:** A piecewise-linear MILP embedded in a rolling-horizon MPC framework, using multi-day electricity-price forecasts.

The physical and operational foundation comes from the dissertation. The reduced-order single-temperature model, MILP formulation and multi-day forecasting framework are proposed extensions for this project.

The next implementation step is to validate the reduced-order thermal model and cooling-capacity approximation before building the MILP controller.