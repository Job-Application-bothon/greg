# Trained planner model

Put the two files from the training notebook here, always as a pair:

- `planner.keras`: the trained neural network
- `planner_config.json`: its settings (blend weight, likely range, limits, excluded periods)

Without them the planner still works with default settings, just without the neural network.
Another folder can be used by setting the environment variable `GREG_PLANNER_DIR`.
