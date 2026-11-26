"""Hourly deterministic Pyomo MPC for the five-minute Gärtank FMU."""

from __future__ import annotations

from dataclasses import replace
from math import ceil, isfinite
from typing import Any, Mapping
import copy
from time import perf_counter

import numpy as np
from eta_utility import get_logger
from eta_utility.eta_x.agents import RuleBased
from pyomo.common.errors import ApplicationError
from pyomo.environ import SolverFactory, value
from pyomo.opt import TerminationCondition
from stable_baselines3.common.base_class import BasePolicy
from stable_baselines3.common.vec_env import VecEnv

from model.cooling_milp import CoolingConfig, build_model, solve_model

log = get_logger().getChild(__name__)


def sample_temperature_breakpoints(
    temperature_initial_K: float,
    temperature_target_K: float,
    max_resolution_K: float,
) -> list[float]:
    """Create evenly spaced breakpoints from target to initial temperature.

    The number of intervals is rounded up so no interval exceeds the requested
    maximum resolution, and both endpoint temperatures are always included.
    """
    values = (temperature_initial_K, temperature_target_K, max_resolution_K)
    if not all(isfinite(value) for value in values):
        raise ValueError("Breakpoint temperatures and resolution must be finite.")
    if temperature_initial_K <= temperature_target_K:
        raise ValueError("Initial temperature must be above the target temperature.")
    if max_resolution_K <= 0:
        raise ValueError("Maximum breakpoint resolution must be positive.")

    interval_count = ceil(
        (temperature_initial_K - temperature_target_K) / max_resolution_K
    )
    return np.linspace(
        temperature_target_K, temperature_initial_K, interval_count + 1
    ).tolist()


class MILPController(RuleBased):
    """Re-solve a cooling MILP hourly using current FMU observations."""

    def __init__(
        self,
        policy: type[BasePolicy],
        env: VecEnv,
        verbose: int = 1,
        *,
        target_temperature_offset_K: float,
        cooling_milp: Mapping[str, Any],
        reoptimization_interval_hours: int = 2,
        temperature_deviation_threshold_K: float = 0.2,
        solver_time_limit_s: float = 300,
        **kwargs: Any,
    ) -> None:
        """Validate configuration and capture environment data for MPC runs."""
        super().__init__(policy=policy, env=env, verbose=verbose, **kwargs)
        if env.num_envs != 1:
            raise ValueError("MILPController supports exactly one environment.")

        if (
            not np.isfinite(target_temperature_offset_K)
            or target_temperature_offset_K > 0
        ):
            raise ValueError("target_temperature_offset_K must be nonpositive.")
        self.target_temperature_offset_K = target_temperature_offset_K
        if (
            isinstance(reoptimization_interval_hours, bool)
            or not isinstance(reoptimization_interval_hours, int)
            or reoptimization_interval_hours < 1
        ):
            raise ValueError("reoptimization_interval_hours must be a positive integer.")
        if (
            not isfinite(temperature_deviation_threshold_K)
            or temperature_deviation_threshold_K < 0
        ):
            raise ValueError("temperature_deviation_threshold_K must be finite and nonnegative.")
        if not isfinite(solver_time_limit_s) or solver_time_limit_s <= 0:
            raise ValueError("solver_time_limit_s must be finite and positive.")
        self.reoptimization_interval_hours = reoptimization_interval_hours
        self.temperature_deviation_threshold_K = temperature_deviation_threshold_K
        self.solver_time_limit_s = solver_time_limit_s

        # Extract and validate MILP model parameters.
        model_parameters = dict(cooling_milp)

        self.solver = model_parameters.get("solver")
        if not isinstance(self.solver, str) or not self.solver:
            raise ValueError("cooling_milp must define a nonempty solver name.")
        if not SolverFactory(self.solver).available(exception_flag=False):
            raise RuntimeError(f"MILP solver {self.solver!r} is not available.")
        if model_parameters.get("step_s") != 3600:
            raise ValueError("This integration requires one-hour MILP intervals.")

        try:
            breakpoint_res = float(
                model_parameters.get("max_temperature_breakpoints_res_K")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "cooling_milp must define max_temperature_breakpoints_res_K."
            ) from exc
        if (
            not np.isfinite(breakpoint_res) or breakpoint_res <= 0
        ):
            raise ValueError("Maximum breakpoint resolution must be finite and positive.")

        self.model_parameters = model_parameters

        # Capture environment-specific configuration for the controller.
        observations = env.get_attr("state_config")[0].observations
        self.temperature_index = observations.index("storage_temperature")
        self.ambient_index = observations.index("ambient_temp")
        self.target_temperature_K = env.get_attr("min_temp_storage")[0]
        self.max_temperature_K = env.get_attr("max_temp_storage")[0]
        if self.target_temperature_K + self.target_temperature_offset_K < 275.15:
            log.warning(
                "MILP target is below 2 C; the cooling-capacity correlation is extrapolated."
            )
        sampling_time = env.get_attr("sampling_time")[0]
        if sampling_time <= 0 or 3600 % sampling_time:
            raise ValueError("The FMU sampling time must divide one hour.")

        self.steps_per_hour = int(3600 / sampling_time)
        self.episode_steps = env.get_attr("n_episode_steps")[0]
        start = env.get_attr("scenario_time_begin")[0]
        if start.minute or start.second or start.microsecond or self.episode_steps % self.steps_per_hour:
            raise ValueError("This integration requires clock-hour-aligned start and deadline.")

        self.prices_EUR_per_kWh = np.asarray(env.get_attr("prices_EUR_per_kWh")[0], dtype=float)
        if len(self.prices_EUR_per_kWh) != self.episode_steps + 1:
            raise ValueError("Prices must cover every FMU timestamp through the deadline.")
        self.action = 0.0
        self.metadata: dict[str, Any] = {}
        self._last_optimization_step: int | None = None
        self._plan_start_step: int | None = None
        self._plan_actions: list[float] = []
        self._plan_temperatures_K: list[float] = []
        self._plan_ambient_temperature_K = 0.0
        self._last_control_step: int | None = None

    def control_rules(self, observation: np.ndarray) -> np.ndarray:
        """Reuse the previous plan unless its age or prediction error triggers a solve."""
        step = self.env.get_attr("n_steps")[0]
        if self._last_control_step is not None and step < self._last_control_step:
            self._last_optimization_step = None
            self._plan_start_step = None
            self._plan_actions = []
            self._plan_temperatures_K = []
        self._last_control_step = step
        temperature_K = float(observation[self.temperature_index])
        ambient_temperature_K = float(observation[self.ambient_index])

        if temperature_K <= self.target_temperature_K:
            self.action = 0.0
            self.metadata = {"status": "target_reached"}
        elif step % self.steps_per_hour == 0:
            plan_hour = (
                (step - self._plan_start_step) // self.steps_per_hour
                if self._plan_start_step is not None
                else None
            )
            has_plan = plan_hour is not None and 0 <= plan_hour < len(self._plan_actions)
            due = (
                self._last_optimization_step is None
                or step - self._last_optimization_step
                >= self.reoptimization_interval_hours * self.steps_per_hour
            )
            deviation = (
                abs(temperature_K - self._plan_temperatures_K[plan_hour])
                if has_plan and plan_hour is not None
                else None
            )
            if due or (
                has_plan
                and deviation is not None
                and deviation > self.temperature_deviation_threshold_K
            ):
                self._last_optimization_step = step
                self._optimize(step, temperature_K, ambient_temperature_K)
            elif has_plan and plan_hour is not None:
                self.action = self._plan_actions[plan_hour]
                self.metadata = {
                    "status": "reused optimal plan",
                    "predicted_temperature_next_K": self._plan_temperatures_K[plan_hour + 1],
                    "predicted_ambient_temperature_K": self._plan_ambient_temperature_K,
                }
            else:
                self.action = 1.0
                self.metadata = {
                    "status": "fallback: awaiting retry",
                    "predicted_ambient_temperature_K": ambient_temperature_K,
                }

        self.env.set_attr("controller_metadata", self.metadata)
        return np.array([self.action])

    def _optimize(
        self, step: int, temperature_K: float, ambient_temperature_K: float
    ) -> None:
        """Build and solve the remaining-horizon MILP, then select its first action."""

        model_parameters = copy.deepcopy(self.model_parameters)
        remaining_hours = (self.episode_steps - step) // self.steps_per_hour
        # Match the environment's price-at-the-end-of-each-five-minute-step convention.
        prices_EUR_per_kWh = [
            float(
                np.mean(
                    self.prices_EUR_per_kWh[
                        start + 1 : start + self.steps_per_hour + 1
                    ]
                )
            )
            for start in range(step, self.episode_steps, self.steps_per_hour)
        ]

        optim_target_temperature_K = (
            self.target_temperature_K + self.target_temperature_offset_K
        )
        max_temperature_breakpoints_res_K = float(model_parameters.pop("max_temperature_breakpoints_res_K"))
        breakpoint_upper_K = max(
            temperature_K, min(ambient_temperature_K, self.max_temperature_K)
        )
        config = CoolingConfig(
            temperature_initial_K=temperature_K,
            temperature_target_K=optim_target_temperature_K,
            temperature_breakpoints_K=sample_temperature_breakpoints(
                temperature_initial_K=breakpoint_upper_K,
                temperature_target_K=optim_target_temperature_K,
                max_resolution_K=max_temperature_breakpoints_res_K,
            ),
            prices_EUR_per_kWh=prices_EUR_per_kWh,
            ambient_temperatures_K=[ambient_temperature_K] * remaining_hours,
            **model_parameters,
        )
        model = build_model(config)
        self._plan_start_step = None
        self._plan_actions = []
        self._plan_temperatures_K = []
        retried = False
        solve_start = perf_counter()
        try:
            try:
                result = solve_model(
                    model, config.solver, time_limit_s=self.solver_time_limit_s
                )
            except ApplicationError as exc:
                if config.piecewise_representation != "SOS2" or not SolverFactory(
                    config.solver
                ).available(exception_flag=False):
                    raise
                log.warning(
                    "MPC hour %d: %s; retrying with CC.",
                    step // self.steps_per_hour,
                    exc,
                )
                config = replace(config, piecewise_representation="CC")
                model = build_model(config)
                retried = True
                remaining_time = self.solver_time_limit_s - (perf_counter() - solve_start)
                if remaining_time <= 0:
                    raise RuntimeError("Solver time limit expired before the CC retry.")
                result = solve_model(
                    model, config.solver, time_limit_s=remaining_time
                )
        except (ApplicationError, RuntimeError) as exc:
            if not SolverFactory(config.solver).available(exception_flag=False):
                raise
            self.action = 1.0
            self.metadata = {
                "status": f"fallback: {exc}",
                "predicted_ambient_temperature_K": ambient_temperature_K,
            }
            log.warning(
                "MPC hour %d after %.2f s: %s",
                step // self.steps_per_hour,
                perf_counter() - solve_start,
                self.metadata["status"],
            )
            return

        if result.solver.termination_condition == TerminationCondition.infeasible:
            self.action = 1.0
            status = "fallback: infeasible (CC retry)" if retried else "fallback: infeasible"
            self.metadata = {
                "status": status,
                "predicted_ambient_temperature_K": ambient_temperature_K,
            }
            log.warning("MPC hour %d: %s", step // self.steps_per_hour, status)
        else:
            self._plan_start_step = step
            self._plan_actions = [
                float(round(value(model.cooling_on[t]))) for t in model.intervals
            ]
            self._plan_temperatures_K = [
                float(value(model.temperature_K[t])) for t in model.states
            ]
            self._plan_ambient_temperature_K = ambient_temperature_K
            self.action = self._plan_actions[0]
            self.metadata = {
                "status": "optimal (CC retry)" if retried else "optimal",
                "predicted_temperature_next_K": float(value(model.temperature_K[1])),
                "predicted_ambient_temperature_K": ambient_temperature_K,
            }

        log.info(
            "MPC hour %d: %s; temperature=%.3f K, target=%.3f K, action=%d",
            step // self.steps_per_hour,
            self.metadata["status"],
            temperature_K,
            optim_target_temperature_K,
            int(self.action),
        )
        log.info("MPC solve time: %.2f s", perf_counter() - solve_start)