"""Hourly deterministic Pyomo MPC for the five-minute Gärtank FMU."""

from __future__ import annotations

from dataclasses import replace
from logging import getLogger
from pathlib import Path
from typing import Any

import numpy as np
from eta_utility.eta_x.agents import RuleBased
from pyomo.common.errors import ApplicationError
from pyomo.environ import SolverFactory, value
from pyomo.opt import TerminationCondition
from stable_baselines3.common.base_class import BasePolicy
from stable_baselines3.common.vec_env import VecEnv

from model.cooling_milp import CoolingConfig, build_model, solve_model

log = getLogger(__name__)


class MILPController(RuleBased):
    """Re-solve to the configured deadline with known historical prices."""

    def __init__(
        self,
        policy: type[BasePolicy],
        env: VecEnv,
        verbose: int = 1,
        *,
        heat_transfer_UA_W_per_K: float,
        model_config: str = "config/cooling_milp_open_loop_validation.json",
        piecewise_representation: str = "SOS2",
        **kwargs: Any,
    ) -> None:
        super().__init__(policy=policy, env=env, verbose=verbose, **kwargs)
        if env.num_envs != 1:
            raise ValueError("MILPController supports exactly one environment.")

        config_path = Path(__file__).resolve().parents[1] / model_config
        self.model_config = CoolingConfig.from_json(config_path)
        if self.model_config.step_s != 3600:
            raise ValueError("This integration requires one-hour MILP intervals.")

        self.heat_transfer_UA_W_per_K = heat_transfer_UA_W_per_K
        self.piecewise_representation = piecewise_representation
        if not np.isfinite(self.heat_transfer_UA_W_per_K) or self.heat_transfer_UA_W_per_K < 0:
            raise ValueError("Heat-transfer UA must be finite and nonnegative.")
        if not SolverFactory(self.model_config.solver).available(exception_flag=False):
            raise RuntimeError(f"MILP solver {self.model_config.solver!r} is not available.")
        observations = env.get_attr("state_config")[0].observations
        self.temperature_index = observations.index("storage_temperature")
        self.ambient_index = observations.index("ambient_temp")
        self.target = env.get_attr("min_temp_storage")[0]
        if self.target != self.model_config.temperature_target_K:
            raise ValueError("The FMU target must match the deterministic MILP target.")
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

    def control_rules(self, observation: np.ndarray) -> np.ndarray:
        step = self.env.get_attr("n_steps")[0]
        temperature_K = float(observation[self.temperature_index])
        ambient_temperature_K = float(observation[self.ambient_index])
        if temperature_K <= self.target:
            self.action = 0.0
            self.metadata = {"status": "target_reached"}
        elif step % self.steps_per_hour == 0:
            self._optimize(step, temperature_K, ambient_temperature_K)
        self.env.set_attr("controller_metadata", self.metadata)
        return np.array([self.action])

    def _optimize(self, step: int, temperature_K: float, ambient_temperature_K: float) -> None:
        remaining_hours = (self.episode_steps - step) // self.steps_per_hour
        # Match the environment's price-at-the-end-of-each-five-minute-step convention.
        prices_EUR_per_kWh = [
            float(np.mean(self.prices_EUR_per_kWh[start + 1:start + self.steps_per_hour + 1]))
            for start in range(step, self.episode_steps, self.steps_per_hour)
        ]
        config = replace(
            self.model_config,
            temperature_initial_K=temperature_K,
            prices_EUR_per_kWh=prices_EUR_per_kWh,
            ambient_temperatures_K=[ambient_temperature_K] * remaining_hours,
            heat_transfer_UA_W_per_K=self.heat_transfer_UA_W_per_K,
            piecewise_representation=self.piecewise_representation,
        )
        model = build_model(config)
        retried = False
        try:
            try:
                result = solve_model(model, config.solver)
            except ApplicationError as exc:
                if config.piecewise_representation != "SOS2" or not SolverFactory(
                    config.solver
                ).available(exception_flag=False):
                    raise
                log.warning("MPC hour %d: %s; retrying with CC.", step // self.steps_per_hour, exc)
                config = replace(config, piecewise_representation="CC")
                model = build_model(config)
                retried = True
                result = solve_model(model, config.solver)
        except (ApplicationError, RuntimeError) as exc:
            if not SolverFactory(config.solver).available(exception_flag=False):
                raise
            self.action = 1.0
            self.metadata = {
                "status": f"fallback: {exc}",
                "predicted_ambient_temperature_K": ambient_temperature_K,
            }
            log.warning("MPC hour %d: %s", step // self.steps_per_hour, self.metadata["status"])
            return
        if result.solver.termination_condition == TerminationCondition.infeasible:
            self.action = 1.0
            status = "fallback: infeasible (CC retry)" if retried else "fallback: infeasible"
            self.metadata = {
                "status": status,
                "predicted_ambient_temperature_K": ambient_temperature_K,
            }
        else:
            self.action = float(round(value(model.cooling_on[0])))
            self.metadata = {
                "status": "optimal (CC retry)" if retried else "optimal",
                "predicted_temperature_next_K": float(value(model.temperature_K[1])),
                "predicted_ambient_temperature_K": ambient_temperature_K,
            }
        log.info(
            "MPC hour %d: %s; temperature=%.3f K, action=%d",
            step // self.steps_per_hour,
            self.metadata["status"],
            temperature_K,
            int(self.action),
        )