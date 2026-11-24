import pandas as pd
import matplotlib.pyplot as plt

old_file = "/media/cheeko/6E92406092402EC1/Abhilash/github/rule-based-operation-strategy/experiments/rule-based-optimization/results/Gartank 2022/Gartank_cycle1_2022_ref/use_case1_000-01_episode.csv"
new_file = "/media/cheeko/6E92406092402EC1/Abhilash/github/beerbuddy/experiments/MILP_MPC/results/gartank/reference_cycle_trajectory.csv"

old_df = pd.read_csv(old_file, sep=';')
print(old_df.head())
# print(old_df.columns)

new_df = pd.read_csv(new_file, sep=';')
print(new_df.head())
# print(new_df.columns)

# Assert that the Fermentation tank temperature series are equal within a tolerance (in Kelvin)
pd.testing.assert_series_equal(old_df['storage_temperature'], new_df['storage_temperature_K'], atol=1e-3, check_names=False)

plt.figure(figsize=(10, 6))

plt.subplot(2, 1, 1)
plt.plot(old_df['ambient_temp'], label='Old')
plt.plot(new_df['ambient_temperature_K'], label='New')
plt.xlabel('Time step')
plt.ylabel('Ambient Temperature (K)')
plt.title('Reference Cooling Curve')
plt.legend()

plt.subplot(2, 1, 2, sharex=plt.gca())
plt.plot(old_df['ambient_temp'] - new_df['ambient_temperature_K'], label='Difference')
plt.xlabel('Time step')
plt.ylabel('Temperature Difference (K)')
plt.title('Difference in Ambient Temperature')
plt.legend()

plt.show()