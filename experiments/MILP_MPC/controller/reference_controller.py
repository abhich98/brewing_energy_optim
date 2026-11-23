"""Continuous cooling followed by the reference temperature hysteresis."""

from __future__ import annotations

from typing import Any

import numpy as np
from eta_utility.eta_x.agents import RuleBased
from stable_baselines3.common.base_class import BasePolicy
from stable_baselines3.common.vec_env import VecEnv


class ReferenceController(RuleBased):
    def __init__(self, policy: type[BasePolicy], env: VecEnv, verbose: int = 1, **kwargs: Any) -> None:
        super().__init__(policy=policy, env=env, verbose=verbose, **kwargs)
        self.temperature_index = env.get_attr("state_config")[0].observations.index("storage_temperature")
        self.min_temperature = env.get_attr("min_temp_storage")[0]
        self.max_temperature = env.get_attr("max_temp_storage")[0]
        self.cooling = True

    def control_rules(self, observation: np.ndarray) -> np.ndarray:
        if self.env.get_attr("n_steps")[0] == 0:
            self.cooling = True
        temperature = observation[self.temperature_index]
        if temperature <= self.min_temperature:
            self.cooling = False
        elif temperature >= self.max_temperature:
            self.cooling = True
        return np.array([float(self.cooling)])
