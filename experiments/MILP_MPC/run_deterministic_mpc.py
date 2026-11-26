"""Run deterministic Pyomo MPC against the Gärtank FMU."""

import os
from pathlib import Path
from time import perf_counter

import os
from eta_utility import get_logger, log_add_filehandler
from eta_utility.eta_x import ETAx


def main() -> None:
    get_logger()

    environment = "gartank"
    run_name = "deterministic_mpc"

    config_temp = { # These overwrite the values provided in the configuration file
            "settings": {"episode_duration": 3600}  # random temporary value in seconds input to satisfy the configuration. Later the actual duration will be set in the environment based on the scenario.
        }

    experiment = ETAx(
        root_path=Path(__file__).resolve().parent,
        config_name=run_name + ".json",
        relpath_config="config",
        config_overwrite=config_temp,
    )
    experiment.prepare_run(
        series_name=environment,
        run_name=run_name,
    )

    logfile = os.path.join(
                experiment.config_run.path_results,
                environment, 
                run_name + "_Simulation-Logger" + ".log"
                )
    log = log_add_filehandler(logfile, level=1)
    
    experiment.play(
            series_name=environment, 
            run_name=run_name
            )

if __name__ == "__main__":
    start = perf_counter()
    main()
    print(f"Total elapsed: {perf_counter() - start:.2f} seconds")