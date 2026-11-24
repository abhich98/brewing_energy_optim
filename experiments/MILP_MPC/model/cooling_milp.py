"""Standalone open-loop cooling MILP, independent of the FMU."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
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
    temperature_initial_k: float
    temperature_min_k: float
    temperature_max_k: float
    terminal_tolerance_k: float
    tank_volume_m3: float
    beer_density_kg_m3: float
    beer_heat_capacity_j_kg_k: float
    eer: float
    step_hours: float
    temperature_breakpoints_k: list[float]
    prices_eur_per_kwh: list[float]
    solver: str

    def __post_init__(self) -> None:
        numeric = (
            self.temperature_initial_k, self.temperature_min_k, self.temperature_max_k,
            self.terminal_tolerance_k, self.tank_volume_m3, self.beer_density_kg_m3,
            self.beer_heat_capacity_j_kg_k, self.eer, self.step_hours,
            *self.temperature_breakpoints_k, *self.prices_eur_per_kwh,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("All model parameters and prices must be finite.")
        if min(self.tank_volume_m3, self.beer_density_kg_m3, self.beer_heat_capacity_j_kg_k,
               self.eer, self.step_hours) <= 0:
            raise ValueError("Tank properties, EER, and time step must be positive.")
        if not (self.temperature_min_k <= self.temperature_initial_k <= self.temperature_max_k):
            raise ValueError("Initial temperature must lie within the specified bounds.")
        if not 0 <= self.terminal_tolerance_k <= self.temperature_max_k - self.temperature_min_k:
            raise ValueError("Terminal tolerance must fit within the temperature bounds.")
        
        points = self.temperature_breakpoints_k
        if (len(points) < 2 or points[0] != self.temperature_min_k
                or points[-1] != self.temperature_max_k
                or any(a >= b for a, b in zip(points, points[1:]))):
            raise ValueError("Breakpoints must increase strictly and span the temperature bounds.")
        if not self.prices_eur_per_kwh:
            raise ValueError("At least one price interval is required.")
        if not self.solver:
            raise ValueError("A solver name is required.")

    @classmethod
    def from_json(cls, path: Path) -> CoolingConfig:
        with path.open(encoding="utf-8") as file:
            return cls(**json.load(file))


def cooling_capacity_w(temperature: float, unit: str = "K") -> float:
    """Calculate the cooling capacity in watts based on the parameters estimated from Fermentation tank cooling curves.
    
    Args:
        temperature: The temperature at which to calculate the cooling capacity.
        unit: The unit of the temperature, either "C" for Celsius or "K" for Kelvin.

    Returns:
        The cooling capacity in watts.
    """

    if unit == "K":
        temperature_c = temperature - 273.15
    elif unit == "C":
        temperature_c = temperature
    else:
        raise ValueError(f"Unit must be 'C' or 'K', got '{unit}'")
    
    return 16450 * temperature_c ** 1.33 - 4603


def build_model(config: CoolingConfig) -> ConcreteModel:
    """Construct an hourly binary-control MILP with no ambient heat transfer."""
    model = ConcreteModel()
    n = len(config.prices_eur_per_kwh)
    model.intervals = RangeSet(0, n - 1)
    model.states = RangeSet(0, n)

    # Variables
    model.temperature = Var(
            model.states, bounds=(config.temperature_min_k, config.temperature_max_k)
        )
    model.on = Var(model.intervals, domain=Binary) # The final cooling schedule
    q_min = cooling_capacity_w(config.temperature_min_k)
    q_max = cooling_capacity_w(config.temperature_max_k)
    model.available_cooling_capacity = Var(model.intervals, bounds=(q_min, q_max))
    model.applied_cooling = Var(model.intervals, bounds=(0, q_max))

    # Constraints
    model.initial = Constraint(expr=model.temperature[0] == config.temperature_initial_k)
    model.terminal = Constraint(
        expr=model.temperature[n] <= config.temperature_min_k + config.terminal_tolerance_k
    )
    model.capacity_curve = Piecewise(
        model.intervals, model.available_cooling_capacity, model.temperature,
        pw_pts=config.temperature_breakpoints_k,
        f_rule=lambda _model, _t, temp: cooling_capacity_w(temp),
        pw_constr_type="EQ", 
        pw_repn="SOS2",
    )
    model.cooling_upper_on = Constraint(
        model.intervals, rule=lambda m, t: m.applied_cooling[t] <= q_max * m.on[t]
    )
    model.cooling_lower_on = Constraint(
        model.intervals, rule=lambda m, t: m.applied_cooling[t] >= q_min * m.on[t]
    )
    model.cooling_upper_capacity = Constraint(
        model.intervals, rule=lambda m, t: m.applied_cooling[t] <= m.available_cooling_capacity[t] - q_min * (1 - m.on[t])
    )
    model.cooling_lower_capacity = Constraint(
        model.intervals, rule=lambda m, t: m.applied_cooling[t] >= m.available_cooling_capacity[t] - q_max * (1 - m.on[t])
    )
    thermal_mass = config.tank_volume_m3 * config.beer_density_kg_m3 * config.beer_heat_capacity_j_kg_k
    model.dynamics = Constraint(
        model.intervals,
        rule=lambda m, t: m.temperature[t + 1] == m.temperature[t]
        - config.step_hours * 3600 / thermal_mass * m.applied_cooling[t],
    )

    # Objective
    model.cost = Objective(expr=sum(
        config.prices_eur_per_kwh[t] * model.applied_cooling[t] * config.step_hours / (1000 * config.eer)
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
        cooling = pyomo_value(model.applied_cooling[t])
        energy = cooling * config.step_hours / (1000 * config.eer)
        rows.append({
            "interval": t,
            "start_hour": t * config.step_hours,
            "end_hour": (t + 1) * config.step_hours,
            "temperature_start_k": pyomo_value(model.temperature[t]),
            "temperature_end_k": pyomo_value(model.temperature[t + 1]),
            "cooling_on": round(pyomo_value(model.on[t])),
            "available_cooling_w": pyomo_value(model.available_cooling_capacity[t]),
            "cooling_w": cooling,
            "electric_energy_kwh": energy,
            "price_eur_per_kwh": config.prices_eur_per_kwh[t],
            "cost_eur": energy * config.prices_eur_per_kwh[t],
        })
    return rows
