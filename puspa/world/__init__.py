"""World model package (Brain phase).

- ``puspa.world.model``: the structured world model (predict before
  acting, update from observation, correct on misprediction).
- ``puspa.world.simulator``: a small deterministic bill plus price
  watch world used as proof that prediction accuracy improves.
- ``puspa.world.planner``: multi-step planning by simulating against
  a copy of the model state.
"""

from .model import Prediction, WorldModel
from .planner import plan
from .simulator import SimulatedWorld, run_episode, run_episodes

__all__ = [
    "Prediction",
    "WorldModel",
    "plan",
    "SimulatedWorld",
    "run_episode",
    "run_episodes",
]
