On Linux x86-64, `uv` installs the CPU build of PyTorch. The project pins `marshmallow` and `polars` to versions compatible with the weather dependencies used by the current `eta_utility` installation.

The bundled FMU includes a Linux binary as well as a Windows binary.

# Considerations and Gotchas

- Following the legacy output, when the fmu temperature is recorded, ambient temperature and price are recorded at the interval-end timestamp; the ambient input used by the FMU during that interval comes from the preceding timestamp. Temperatures are in K, powers in W, price in EUR/kWh, interval energy in kWh and cost in EUR. Energy is approximated using the interval-end electric power. Cost uses the price at the **end** of each interval to match the legacy calculation, including at hourly price changes.

- The reference-cycle configuration uses ETAx's `relpath_scenarios` key for the scenario data directory. Despite the key's name, it can contain either a path relative to the experiment root or an absolute path.