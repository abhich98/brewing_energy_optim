"""Gymnasium environment for the Gärtank reference simulation."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from eta_utility import get_logger, timeseries
from eta_utility.eta_x import ConfigOptRun
from eta_utility.eta_x.envs import BaseEnvSim, StateConfig, StateVar

log = get_logger().getChild(__name__)


class Gartank(BaseEnvSim):
    version = "v1.0"
    description = "Gärtank FMU cooling cycle"
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
        stop_at_target: bool = False,
        plot_results: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            env_id,
            config_run,
            verbose,
            callback,
            scenario_time_begin=scenario_time_begin,
            scenario_time_end=scenario_time_end,
            episode_duration=(
                datetime.strptime(scenario_time_end, "%Y-%m-%d %H:%M") 
                - datetime.strptime(scenario_time_begin, "%Y-%m-%d %H:%M")
                ).total_seconds(),
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
        self.stop_at_target = stop_at_target
        self.plot_results = plot_results
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
        price_hours = expected.floor("h")
        prices = prices.loc[(prices.index >= price_hours[0]) & (prices.index <= price_hours[-1])]
        if not prices.index.is_unique or not prices.index.is_monotonic_increasing:
            raise ValueError("Price timestamps within the episode must be unique and ordered.")
        self.prices_EUR_per_kWh = prices.reindex(price_hours).to_numpy(dtype=float) / 1000
        if not np.isfinite(self.prices_EUR_per_kWh).all():
            raise ValueError("Historical prices must cover every simulation timestamp with finite values.")

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, dict[str, Any]]:
        self.additional_state = {}
        self._episode_finished = False
        self.controller_metadata: dict[str, Any] = {}
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
            "price_EUR_per_kWh": self.prices_EUR_per_kWh[0],
            "energy_J": 0.0,
            "cost_EUR": 0.0,
            "controller_status": None,
            "predicted_temperature_next_K": None,
            "predicted_ambient_temperature_K": None,
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
        self.additional_state = {}
        observations, reward, terminated, truncated, info = super().step(action)
        if terminated and self.n_steps < self.n_episode_steps:
            raise RuntimeError(f"FMU simulation failed at step {self.n_steps}.")
        power_W = self.state["power_chiller"]
        if not np.isfinite(observations).all() or not np.isfinite(power_W):
            raise ValueError(f"FMU returned non-finite values at step {self.n_steps}.")
        energy_J = power_W * self.sampling_time
        price_EUR_per_kWh = self.prices_EUR_per_kWh[self.n_steps]
        self.records.append({
            "timestamp": self.scenario_time_begin + timedelta(seconds=self.n_steps * self.sampling_time),
            "storage_temperature_K": self.state["storage_temperature"],
            "ambient_temperature_K": self.state["ambient_temp"],
            "cooling_on": int(action[0]),
            "cooling_power_W": self.state["cooling_power"],
            "power_chiller_W": power_W,
            "price_EUR_per_kWh": price_EUR_per_kWh,
            "energy_J": energy_J,
            "cost_EUR": energy_J / 3_600_000 * price_EUR_per_kWh,
            "controller_status": self.controller_metadata.get("status"),
            "predicted_temperature_next_K": self.controller_metadata.get("predicted_temperature_next_K"),
            "predicted_ambient_temperature_K": self.controller_metadata.get("predicted_ambient_temperature_K"),
        })
        if self.stop_at_target and self.state["storage_temperature"] <= self.min_temp_storage:
            terminated = True
            info["completion_reason"] = "target_reached"
        elif terminated or truncated:
            info["completion_reason"] = "deadline"
        if terminated or truncated:
            self._episode_finished = True
            self.render()
        return observations, reward, terminated, truncated, info

    def render(self) -> None:
        if getattr(self, "_rendered", False) or not getattr(self, "records", None):
            return
        if not getattr(self, "_episode_finished", False):
            return
        result = pd.DataFrame(self.records)
        path = Path(self.path_results) / f"{self.run_name}_trajectory.csv"
        result.to_csv(path, 
                      sep=";",  
                      decimal=".",
                      index=False
                      )
        reached = result[result["storage_temperature_K"] <= self.min_temp_storage]
        log.info("Trajectory: %s", path)
        log.info(
            "Energy: %.3f kWh; cost: %.2f EUR",
            result["energy_J"].sum() / 3_600_000,
            result["cost_EUR"].sum(),
        )
        log.info(
            "First target crossing: %s",
            reached["timestamp"].iloc[0] if not reached.empty else "not reached",
        )
        if self.plot_results:
            self.render_plot(result)
        self._rendered = True

    def render_plot(self, result: pd.DataFrame) -> None:
        """Plot measured episode data in the style of the original RBMPC report."""
        time = result["timestamp"]
        fig, axes = plt.subplots(
            6, 1, sharex=True, figsize=(16, 18),
            gridspec_kw={"height_ratios": [0.45, 1, 1, 1, 1, 1]},
            constrained_layout=True,
        )
        axes[0].step(time, result["cooling_on"], where="pre", color="firebrick", label="Cooling on")
        axes[0].set_yticks([0, 1])
        axes[0].set_ylabel("Action")
        axes[1].plot(time, result["ambient_temperature_K"] - 273.15, label="Ambient")
        if result["predicted_ambient_temperature_K"].notna().any():
            axes[1].plot(time, result["predicted_ambient_temperature_K"] - 273.15,
                         linestyle="--", label="Ambient estimate")
        axes[1].set_ylabel("Ambient [°C]")
        axes[2].plot(time, result["storage_temperature_K"] - 273.15, label="Tank")
        axes[2].axhline(self.min_temp_storage - 273.15, linestyle="--", color="green", label="Target")
        axes[2].set_ylabel("Tank [°C]")
        axes[3].plot(time, result["cooling_power_W"] / 1000, label="Thermal cooling")
        axes[3].plot(time, result["power_chiller_W"] / 1000, label="Chiller electrical")
        axes[3].set_ylabel("Power [kW]")
        axes[4].step(time, result["price_EUR_per_kWh"] * 1000, where="post", label="Electricity price")
        axes[4].set_ylabel("Price [€/MWh]")
        power_axis = axes[4].twinx()
        power_axis.plot(time, result["power_chiller_W"] / 1000, color="darkorange", label="Chiller electrical")
        power_axis.set_ylabel("Power [kW]")
        axes[5].plot(time, result["cost_EUR"].cumsum(), label="Cumulative cost")
        axes[5].set_ylabel("Cost [€]")
        axes[5].set_xlabel("Time")
        for axis in axes:
            axis.grid(alpha=0.25)
            axis.legend(loc="upper left")
        power_axis.legend(loc="upper right")
        fig.suptitle("Gärtank cooling episode")
        fig.autofmt_xdate()
        path = Path(self.path_results) / f"{self.run_name}_plot.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        log.info("Plot: %s", path)

    def close(self) -> None:
        if hasattr(self, "simulator"):
            super().close()