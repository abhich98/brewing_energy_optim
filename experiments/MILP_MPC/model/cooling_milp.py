"""Standalone open-loop cooling MILP, independent of the FMU."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from pyomo.environ import (
    Binary,
    ConcreteModel,
    Constraint,
    Objective,
    Piecewise,
    RangeSet,
    SolverFactory,
    Var,
)
from pyomo.environ import value as pyomo_value
from pyomo.environ import minimize as pyomo_minimize

from pyomo.opt import SolverStatus, TerminationCondition


@dataclass(frozen=True)
class CoolingConfig:
    temperature_initial_K: float
    temperature_target_K: float
    terminal_temperature_tolerance_K: float
    # Temperature breakpoints define model bounds and cooling-capacity interpolation.
    temperature_breakpoints_K: list[float]
    tank_volume_m3: float
    beer_density_kg_per_m3: float
    beer_heat_capacity_J_per_kg_K: float
    eer: float
    step_s: float
    prices_EUR_per_kWh: list[float]
    solver: str
    ambient_temperatures_K: list[float] = field(default_factory=list)
    heat_transfer_UA_W_per_K: float = 0.0
    piecewise_representation: str = "SOS2"

    def __post_init__(self) -> None:
        numeric = (
            self.temperature_initial_K, self.temperature_target_K,
            self.terminal_temperature_tolerance_K, *self.temperature_breakpoints_K, 
            self.tank_volume_m3, self.beer_density_kg_per_m3,
            self.beer_heat_capacity_J_per_kg_K, self.eer, self.step_s,
            self.heat_transfer_UA_W_per_K,
            *self.prices_EUR_per_kWh, *self.ambient_temperatures_K,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("All model parameters and prices must be finite.")
        if min(self.tank_volume_m3, self.beer_density_kg_per_m3, self.beer_heat_capacity_J_per_kg_K,
               self.eer, self.step_s) <= 0:
            raise ValueError("Tank properties, EER, and time step must be positive.")

        points = self.temperature_breakpoints_K
        if (len(points) < 2 or points[0] != self.temperature_target_K
                or any(a >= b for a, b in zip(points, points[1:]))):
            raise ValueError("Breakpoints must increase strictly and start at the target temperature.")
        if not self.temperature_target_K < self.temperature_initial_K <= points[-1]:
            raise ValueError("Initial temperature must be above the target and within the breakpoint range.")
        if not 0 <= self.terminal_temperature_tolerance_K <= points[-1] - self.temperature_target_K:
            raise ValueError("Terminal tolerance must fit within the temperature bounds.")

        if not self.prices_EUR_per_kWh:
            raise ValueError("At least one price interval is required.")
        if self.heat_transfer_UA_W_per_K < 0:
            raise ValueError("Heat-transfer UA must be nonnegative.")
        if self.ambient_temperatures_K and len(self.ambient_temperatures_K) != len(self.prices_EUR_per_kWh):
            raise ValueError("Ambient temperatures must cover every price interval.")
        if self.heat_transfer_UA_W_per_K > 0 and not self.ambient_temperatures_K:
            raise ValueError("Ambient temperatures are required when heat-transfer UA is nonzero.")

        if self.piecewise_representation not in ("SOS2", "CC"):
            raise ValueError("Piecewise representation must be 'SOS2' or 'CC'.")
        if not self.solver:
            raise ValueError("A solver name is required.")

    @classmethod
    def from_json(cls, path: Path) -> CoolingConfig:
        with path.open(encoding="utf-8") as file:
            return cls(**json.load(file))


def cooling_capacity_W(temperature_K: float, unit: str = "K") -> float:
    """Calculate the cooling capacity in watts based on the parameters estimated from Fermentation tank cooling curves.
    
    Args:
        temperature_K: The temperature at which to calculate the cooling capacity.
        unit: The unit of the temperature, either "C" for Celsius or "K" for Kelvin.

    Returns:
        The cooling capacity in watts.
    """

    if unit == "K":
        temperature_C = temperature_K - 273.15
    elif unit == "C":
        temperature_C = temperature_K
    else:
        raise ValueError(f"Unit must be 'C' or 'K', got '{unit}'")
    
    return 16450 * temperature_C ** 1.33 - 4603


def build_model(config: CoolingConfig) -> ConcreteModel:
    """Construct a binary-control MILP with optional ambient heat transfer."""
    model = ConcreteModel()
    n = len(config.prices_EUR_per_kWh)
    model.intervals = RangeSet(0, n - 1)
    model.states = RangeSet(0, n)
    temperature_lower_K = config.temperature_breakpoints_K[0]
    temperature_upper_K = config.temperature_breakpoints_K[-1]

    # Variables
    model.temperature_K = Var(
            model.states, bounds=(temperature_lower_K, temperature_upper_K)
        )
    model.cooling_on = Var(model.intervals, domain=Binary)
    minimum_cooling_capacity_W = cooling_capacity_W(temperature_lower_K)
    maximum_cooling_capacity_W = cooling_capacity_W(temperature_upper_K)
    model.cooling_capacity_available_W = Var(
        model.intervals, bounds=(minimum_cooling_capacity_W, maximum_cooling_capacity_W)
    )
    model.cooling_power_applied_W = Var(model.intervals, bounds=(0, maximum_cooling_capacity_W))

    # Constraints
    model.initial = Constraint(expr=model.temperature_K[0] == config.temperature_initial_K)
    model.terminal = Constraint(
        expr=model.temperature_K[n]
        <= config.temperature_target_K + config.terminal_temperature_tolerance_K
    )
    model.capacity_curve = Piecewise(
        model.intervals,
        model.cooling_capacity_available_W,
        model.temperature_K,
        pw_pts=config.temperature_breakpoints_K,
        f_rule=lambda _model, _t, temperature_K: cooling_capacity_W(temperature_K),
        pw_constr_type="EQ",
        pw_repn=config.piecewise_representation,
    )
    model.cooling_upper_on = Constraint(
        model.intervals,
        rule=lambda m, t: m.cooling_power_applied_W[t]
        <= maximum_cooling_capacity_W * m.cooling_on[t],
    )
    model.cooling_lower_on = Constraint(
        model.intervals,
        rule=lambda m, t: m.cooling_power_applied_W[t]
        >= minimum_cooling_capacity_W * m.cooling_on[t],
    )
    model.cooling_upper_capacity = Constraint(
        model.intervals,
        rule=lambda m, t: m.cooling_power_applied_W[t]
        <= m.cooling_capacity_available_W[t]
        - minimum_cooling_capacity_W * (1 - m.cooling_on[t]),
    )
    model.cooling_lower_capacity = Constraint(
        model.intervals,
        rule=lambda m, t: m.cooling_power_applied_W[t]
        >= m.cooling_capacity_available_W[t]
        - maximum_cooling_capacity_W * (1 - m.cooling_on[t]),
    )
    thermal_mass_J_per_K = (
        config.tank_volume_m3 * config.beer_density_kg_per_m3 * config.beer_heat_capacity_J_per_kg_K
    )
    ambient_temperatures_K = config.ambient_temperatures_K or [0.0] * n
    model.dynamics = Constraint(
        model.intervals,
        rule=lambda m, t: m.temperature_K[t + 1] == m.temperature_K[t]
        + config.step_s / thermal_mass_J_per_K
        * (config.heat_transfer_UA_W_per_K
           * (ambient_temperatures_K[t] - m.temperature_K[t]) - m.cooling_power_applied_W[t]),
    )

    # Objective
    model.cost = Objective(expr=sum(
        config.prices_EUR_per_kWh[t]
        * model.cooling_power_applied_W[t] * config.step_s
        / (config.eer * 3_600_000)
        for t in model.intervals
    ), sense=pyomo_minimize)

    return model


def solve_model(model: ConcreteModel, solver_name: str):
    solver = SolverFactory(solver_name)
    if not solver.available(exception_flag=False):
        raise RuntimeError(f"MILP solver {solver_name!r} is not available.")
    result = solver.solve(model, load_solutions=False)
    if result.solver.termination_condition == TerminationCondition.infeasible:
        return result
    if (result.solver.status != SolverStatus.ok
            or result.solver.termination_condition != TerminationCondition.optimal):
        raise RuntimeError(
            f"Solver did not prove optimality: status={result.solver.status}, "
            f"termination={result.solver.termination_condition}."
        )
    model.solutions.load_from(result)
    return result


def schedule_rows(model: ConcreteModel, config: CoolingConfig) -> list[dict[str, float | int]]:
    rows = []
    for t in model.intervals:
        cooling_power_W = pyomo_value(model.cooling_power_applied_W[t])
        electric_energy_J = cooling_power_W * config.step_s / config.eer
        electric_energy_kWh = electric_energy_J / 3_600_000
        rows.append({
            "interval": t,
            "start_time_s": t * config.step_s,
            "end_time_s": (t + 1) * config.step_s,
            "temperature_start_K": pyomo_value(model.temperature_K[t]),
            "temperature_end_K": pyomo_value(model.temperature_K[t + 1]),
            "cooling_on": round(pyomo_value(model.cooling_on[t])),
            "cooling_capacity_available_W": pyomo_value(model.cooling_capacity_available_W[t]),
            "cooling_power_W": cooling_power_W,
            "electric_energy_J": electric_energy_J,
            "price_EUR_per_kWh": config.prices_EUR_per_kWh[t],
            "cost_EUR": electric_energy_kWh * config.prices_EUR_per_kWh[t],
        })
    return rows
