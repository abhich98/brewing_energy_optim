"""Gymnasium environment for the Gärtank reference simulation."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd
from eta_utility import timeseries
from eta_utility.eta_x import ConfigOptRun
from eta_utility.eta_x.envs import BaseEnvSim, StateConfig, StateVar


class Gartank(BaseEnvSim):
    version = "v1.0"
    description = "Gärtank FMU reference cooling cycle"
    fmu_name = "Veltins_Gartank_FMU"

    def __init__(
        self,
        env_id: int,
        config_run: ConfigOptRun,
        verbose: int = 2,
        callback: Callable | None = None,
        *,
        scenario_time_begin: str,
        scenario_time_end: str,
        episode_duration: float,
        sampling_time: float,
        temp_start_storage: float,
        min_temp_storage: float,
        max_temp_storage: float,
        V_Gaertank: float,
        h_Gaertank: float,
        ambient_file: str,
        price_file: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            env_id,
            config_run,
            verbose,
            callback,
            scenario_time_begin=scenario_time_begin,
            scenario_time_end=scenario_time_end,
            episode_duration=episode_duration,
            sampling_time=sampling_time,
            model_parameters={
                "T_sto_start": temp_start_storage,
                "V_tank": V_Gaertank,
                "h_tank": h_Gaertank,
            },
            **kwargs,
        )
        if self.scenario_time_begin + timedelta(seconds=self.episode_duration) != self.scenario_time_end:
            raise ValueError("Episode duration must match the configured scenario start and end.")
        if self.episode_duration % self.sampling_time != 0:
            raise ValueError("Episode duration must be an exact multiple of sampling time.")
        if not min_temp_storage < max_temp_storage:
            raise ValueError("The minimum storage temperature must be below the maximum.")
        if self.path_scenarios is None:
            raise ValueError("The scenario data directory must be configured.")

        self.min_temp_storage = min_temp_storage
        self.max_temp_storage = max_temp_storage
        self.state_config = StateConfig(
            StateVar("input_cooling_on", is_agent_action=True, is_ext_input=True,
                     is_ext_output=True, low_value=0, high_value=1),
            StateVar("ambient_temp", ext_id="input_ambient_temp", scenario_id="ambient_temperature",
                     is_ext_input=True, from_scenario=True, is_agent_observation=True),
            StateVar("storage_temperature", is_ext_output=True, is_agent_observation=True),
            StateVar("cooling_power", is_ext_output=True, is_agent_observation=True),
            StateVar("power_chiller", is_ext_output=True, is_agent_observation=True),
        )
        self.action_space, self.observation_space = self.state_config.continuous_spaces()

        self.ts_current = timeseries.scenario_from_csv(
            self.path_scenarios / ambient_file,
            start_time=self.scenario_time_begin,
            end_time=self.scenario_time_end,
            resample_time=self.sampling_time,
            interpolation_method="interpolate",
            rename_cols={"temp_aussen_kelvin": "ambient_temperature"},
            time_conversion_str="%Y-%m-%d %H:%M",
        )
        expected = pd.date_range(self.scenario_time_begin, self.scenario_time_end,
                                 freq=pd.Timedelta(seconds=self.sampling_time))
        if not self.ts_current.index.equals(expected) or not np.isfinite(
            self.ts_current["ambient_temperature"].to_numpy()
        ).all():
            raise ValueError("Ambient data must cover every simulation timestamp with finite values.")

        prices = pd.read_csv(self.path_scenarios / price_file, parse_dates=["datetime"])
        prices = prices.set_index("datetime")["ENTSOE-DEU-LUX_Price"]
        price_hours = expected[:-1].floor("h")
        prices = prices.loc[(prices.index >= price_hours[0]) & (prices.index <= price_hours[-1])]
        if not prices.index.is_unique or not prices.index.is_monotonic_increasing:
            raise ValueError("Price timestamps within the episode must be unique and ordered.")
        self.prices = prices.reindex(price_hours).to_numpy(dtype=float) / 1000
        if not np.isfinite(self.prices).all():
            raise ValueError("Historical prices must cover every simulation interval with finite values.")

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, dict[str, Any]]:
        self.additional_state = {}
        observations, info = super().reset(seed=seed, options=options)
        self.records: list[dict[str, Any]] = []
        self._rendered = False
        self.records.append({
            "timestamp": self.scenario_time_begin,
            "storage_temperature_K": self.state["storage_temperature"],
            "ambient_temperature_K": self.state["ambient_temp"],
            "cooling_on": None,
            "cooling_power_W": self.state["cooling_power"],
            "power_chiller_W": self.state["power_chiller"],
            "price_EUR_per_kWh": None,
            "energy_kWh": 0.0,
            "cost_EUR": 0.0,
        })
        return observations, info

    def simulate(self, state: Mapping[str, float]) -> tuple[dict[str, float], bool, float]:
        output, success, elapsed = super().simulate(state)
        if not success:
            raise RuntimeError(f"FMU simulation failed at step {self.n_steps + 1}; see simulator log.")
        return output, success, elapsed

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        if action.shape != (1,) or action[0] not in (0, 1):
            raise ValueError(f"Cooling action must be 0 or 1, got {action}.")
        ambient_input = self.state["ambient_temp"]
        self.additional_state = {}
        observations, reward, terminated, truncated, info = super().step(action)
        if terminated and self.n_steps < self.n_episode_steps:
            raise RuntimeError(f"FMU simulation failed at step {self.n_steps}.")
        power = self.state["power_chiller"]
        if not np.isfinite(observations).all() or not np.isfinite(power):
            raise ValueError(f"FMU returned non-finite values at step {self.n_steps}.")
        energy = power * self.sampling_time / 3_600_000
        price = self.prices[self.n_steps - 1]
        self.records.append({
            "timestamp": self.scenario_time_begin + timedelta(seconds=self.n_steps * self.sampling_time),
            "storage_temperature_K": self.state["storage_temperature"],
            "ambient_temperature_K": ambient_input,
            "cooling_on": int(action[0]),
            "cooling_power_W": self.state["cooling_power"],
            "power_chiller_W": power,
            "price_EUR_per_kWh": price,
            "energy_kWh": energy,
            "cost_EUR": energy * price,
        })
        if terminated or truncated:
            self.render()
        return observations, reward, terminated, truncated, info

    def render(self) -> None:
        if getattr(self, "_rendered", False) or not getattr(self, "records", None):
            return
        if self.n_steps != self.n_episode_steps:
            return
        result = pd.DataFrame(self.records)
        path = Path(self.path_results) / f"{self.run_name}_trajectory.csv"
        result.to_csv(path, 
                      sep=";",  
                      decimal=".",
                      index=False
                      )
        reached = result[result["storage_temperature_K"] <= self.min_temp_storage]
        print(f"Trajectory: {path}")
        print(f"Energy: {result['energy_kWh'].sum():.3f} kWh; cost: {result['cost_EUR'].sum():.2f} EUR")
        print(f"First target crossing: {reached['timestamp'].iloc[0] if not reached.empty else 'not reached'}")
        self._rendered = True

    def close(self) -> None:
        if hasattr(self, "simulator"):
            super().close()