"""Hourly deterministic Pyomo MPC for the five-minute Gärtank FMU."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import replace
from datetime import datetime, timedelta
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


def build_clock_hour_intervals(
    start_time: datetime,
    start_step: int,
    end_step: int,
    sampling_time_s: float,
) -> tuple[list[float], list[int]]:
    """Build episode-step intervals ending at clock hours or the episode deadline."""
    interval_durations_s: list[float] = []
    interval_end_steps: list[int] = []
    current_step = start_step

    while current_step < end_step:
        current_time = start_time + timedelta(seconds=current_step * sampling_time_s)
        next_hour = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        seconds_to_hour = (next_hour - current_time).total_seconds()
        steps_to_hour = round(seconds_to_hour / sampling_time_s)
        if not np.isclose(steps_to_hour * sampling_time_s, seconds_to_hour):
            raise ValueError("Clock-hour boundaries must align with the FMU sampling time.")

        interval_end_step = min(current_step + steps_to_hour, end_step)
        interval_durations_s.append(
            (interval_end_step - current_step) * sampling_time_s
        )
        interval_end_steps.append(interval_end_step)
        current_step = interval_end_step

    return interval_durations_s, interval_end_steps


class MILPController(RuleBased):
    """Re-solve a cooling MILP hourly using current FMU observations."""

    def __init__(
        self,
        policy: type[BasePolicy],
        env: VecEnv,
        verbose: int = 1,
        *,
        max_mpc_step_time: float = 3600,
        target_temperature_offset_K: float,
        cooling_milp: Mapping[str, Any],
        reoptimization_interval_steps: int = 2,
        temperature_deviation_threshold_K: float = 0.2,
        solver_time_limit_s: float = 300,
        **kwargs: Any,
    ) -> None:
        """Validate configuration and capture environment data for MPC runs."""
        super().__init__(policy=policy, env=env, verbose=verbose, **kwargs)
        if env.num_envs != 1:
            raise ValueError("MILPController supports exactly one environment.")

        if not isfinite(max_mpc_step_time) or max_mpc_step_time <= 0:
            raise ValueError("max_mpc_step_time must be finite and positive. Typically set to 3600 for hourly MPC.")
        self.max_mpc_step_time = max_mpc_step_time
        if (
            not np.isfinite(target_temperature_offset_K)
            or target_temperature_offset_K > 0
        ):
            raise ValueError("target_temperature_offset_K must be nonpositive.")
        self.target_temperature_offset_K = target_temperature_offset_K
        if (
            isinstance(reoptimization_interval_steps, bool)
            or not isinstance(reoptimization_interval_steps, int)
            or reoptimization_interval_steps < 1
        ):
            raise ValueError("reoptimization_interval_steps must be a positive integer.")
        if (
            not isfinite(temperature_deviation_threshold_K)
            or temperature_deviation_threshold_K < 0
        ):
            raise ValueError("temperature_deviation_threshold_K must be finite and nonnegative.")
        if not isfinite(solver_time_limit_s) or solver_time_limit_s <= 0:
            raise ValueError("solver_time_limit_s must be finite and positive.")
        self.reoptimization_interval_steps = reoptimization_interval_steps
        self.temperature_deviation_threshold_K = temperature_deviation_threshold_K
        self.solver_time_limit_s = solver_time_limit_s

        # Extract and validate MILP model parameters.
        model_parameters = dict(cooling_milp)

        self.solver = model_parameters.get("solver")
        if not isinstance(self.solver, str) or not self.solver:
            raise ValueError("cooling_milp must define a nonempty solver name.")
        if not SolverFactory(self.solver).available(exception_flag=False):
            raise RuntimeError(f"MILP solver {self.solver!r} is not available.")

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
        env_sampling_time = env.get_attr("sampling_time")[0]
        if env_sampling_time <= 0 or self.max_mpc_step_time % env_sampling_time:
            raise ValueError("The FMU sampling time must divide mpc step time.")

        self.env_sampling_time_s = env_sampling_time
        self.env_steps_per_mpc_step = int(self.max_mpc_step_time / env_sampling_time)
        self.episode_env_steps = env.get_attr("n_episode_steps")[0]
        start = env.get_attr("scenario_time_begin")[0]
        if (
            start.second
            or start.microsecond
            or (start.minute * 60) % self.env_sampling_time_s
        ):
            raise ValueError("The simulation start must align with the FMU sampling time.")
        self.scenario_time_begin: datetime = start

        self.prices_EUR_per_kWh = np.asarray(env.get_attr("prices_EUR_per_kWh")[0], dtype=float)
        if len(self.prices_EUR_per_kWh) != self.episode_env_steps + 1:
            raise ValueError("Prices must cover every FMU timestamp through the deadline.")
        self.action = 0.0
        self.metadata: dict[str, Any] = {}
        self._last_optimization_step: int | None = None
        self._plan_start_step: int | None = None
        self._plan_actions: list[float] = []
        self._plan_end_steps: list[int] = []
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
            self._plan_end_steps = []
        self._last_control_step = step
        temperature_K = float(observation[self.temperature_index])
        ambient_temperature_K = float(observation[self.ambient_index])

        if temperature_K <= self.target_temperature_K:
            self.action = 0.0
            self.metadata = {"status": "target_reached"}

        elif self._is_clock_hour_boundary(step):
            plan_interval = bisect_right(self._plan_end_steps, step)
            has_plan = 0 <= plan_interval < len(self._plan_actions)
            due = (
                self._last_optimization_step is None
                or step - self._last_optimization_step
                >= self.reoptimization_interval_steps * self.env_steps_per_mpc_step
            )
            deviation = (
                abs(temperature_K - self._plan_temperatures_K[plan_interval])
                if has_plan
                else None
            )
            if due or (
                has_plan
                and deviation is not None
                and deviation > self.temperature_deviation_threshold_K
            ):
                log.info("Optimizing at MPC step %d due to plan's age or temperature deviation", step // self.env_steps_per_mpc_step) # actually this is not accurate, if the first control step is not at a clock hour boundary, the MPC step number will be off
                self._last_optimization_step = step
                self._optimize(step, temperature_K, ambient_temperature_K)
            elif has_plan:
                log.info("Reusing optimal plan at MPC step %d", step // self.env_steps_per_mpc_step)
                self.action = self._plan_actions[plan_interval]
                self.metadata = {
                    "status": "reused optimal plan",
                    "predicted_temperature_next_K": self._plan_temperatures_K[plan_interval + 1],
                    "predicted_ambient_temperature_K": self._plan_ambient_temperature_K,
                }
            else:
                log.info("No valid plan available at MPC step %d, falling back", step // self.env_steps_per_mpc_step)
                self.action = 1.0
                self.metadata = {
                    "status": "fallback: awaiting retry",
                    "predicted_ambient_temperature_K": ambient_temperature_K,
                }

        self.env.set_attr("controller_metadata", self.metadata)
        return np.array([self.action])

    def _is_clock_hour_boundary(self, step: int) -> bool:
        """Return whether the current FMU step is the start or an exact clock hour."""
        if step == 0:
            return True
        current_time = self.scenario_time_begin + timedelta(
            seconds=step * self.env_sampling_time_s
        )
        return (
            current_time.minute == 0
            and current_time.second == 0
            and current_time.microsecond == 0
        )

    def _optimize(
        self, step: int, temperature_K: float, ambient_temperature_K: float
    ) -> None:
        """Build and solve the remaining-horizon MILP, then select its first action."""

        model_parameters = copy.deepcopy(self.model_parameters)
        interval_durations_s, interval_end_steps = build_clock_hour_intervals(
            self.scenario_time_begin,
            step,
            self.episode_env_steps,
            self.env_sampling_time_s,
        )
        # Match the environment's price-at-the-end-of-each-five-minute-step convention.
        prices_EUR_per_kWh = []
        interval_start_step = step
        for interval_end_step in interval_end_steps:
            prices_EUR_per_kWh.append(
                float(np.mean(
                    self.prices_EUR_per_kWh[
                        interval_start_step + 1 : interval_end_step + 1
                    ]
                ))
            )
            interval_start_step = interval_end_step

        optim_target_temperature_K = (
            self.target_temperature_K + self.target_temperature_offset_K
        )
        simulation_time = self.scenario_time_begin + timedelta(
            seconds=step * self.env_sampling_time_s
        )
        time_label = simulation_time.strftime("%Y-%m-%d %H:%M")
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
            interval_durations_s=interval_durations_s,
            ambient_temperatures_K=[ambient_temperature_K] * len(interval_end_steps),
            **model_parameters,
        )
        model = build_model(config)
        self._plan_start_step = None
        self._plan_actions = []
        self._plan_temperatures_K = []
        self._plan_end_steps = []
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
                    "MPC at %s: %s; retrying with CC.",
                    time_label,
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
                "MPC at %s after %.2f s: %s",
                time_label,
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
            log.warning("MPC at %s: %s", time_label, status)
        else:
            self._plan_start_step = step
            self._plan_actions = [
                float(round(value(model.cooling_on[t]))) for t in model.intervals
            ]
            self._plan_end_steps = interval_end_steps
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
            "MPC at %s: %s; temperature=%.3f K, target=%.3f K, action=%d",
            time_label,
            self.metadata["status"],
            temperature_K,
            optim_target_temperature_K,
            int(self.action),
        )
        log.info("MPC solve time: %.2f s", perf_counter() - solve_start)