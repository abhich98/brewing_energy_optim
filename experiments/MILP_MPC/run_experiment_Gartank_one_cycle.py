"""Run the Gärtank reference cooling cycle."""

from pathlib import Path
from time import perf_counter

from eta_utility import get_logger
from eta_utility.eta_x import ETAx


def main() -> None:
    start = perf_counter()
    get_logger()
    experiment = ETAx(
        root_path=Path(__file__).resolve().parent,
        config_name="sample_one_cycle.json",
        relpath_config="config",
    )
    experiment.play("gartank_reference", "cycle1")
    print(f"Elapsed: {perf_counter() - start:.2f} seconds")


if __name__ == "__main__":
    main()
