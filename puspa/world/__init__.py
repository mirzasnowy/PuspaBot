"""World model package (Brain phase).

- ``puspa.world.model``: the structured world model (predict before
  acting, update from observation, correct on misprediction).
  Beliefs cover prices, bills, schedules (how long a task kind
  usually takes and how reliable its deadline is), capabilities
  (what the agent can and cannot do per task kind), contacts
  (how one contact usually responds, from observed interactions
  only), and habits (recurring patterns in the user's own task
  history, from repeated tasks only).
- ``puspa.world.simulator``: a small deterministic bill plus price
  watch world used as proof that prediction accuracy improves.
- ``puspa.world.noisy_simulator``: the same world with noisy price
  drift and occasional spikes, same interface, used to report
  accuracy honestly instead of claiming 1.0.
- ``puspa.world.planner``: multi-step planning by simulating against
  a copy of the model state.
- ``puspa.world.store``: Postgres persistence of the model state
  (one row per user/agent, ``save_world_state`` /
  ``load_world_model``), wired to the live task loop through
  ``TaskRunner`` (``puspa/runtime/tasks.py``).
"""

from .model import Prediction, WorldModel
from .noisy_simulator import (
    NoisySimulatedWorld,
)
from .noisy_simulator import (
    run_episode as run_noisy_episode,
)
from .noisy_simulator import (
    run_episodes as run_noisy_episodes,
)
from .planner import plan
from .simulator import SimulatedWorld, run_episode, run_episodes
from .store import (
    create_schema as create_world_schema,
)
from .store import (
    delete_world_state,
    deserialize_state,
    load_world_model,
    load_world_state,
    save_world_state,
    serialize_state,
)

__all__ = [
    "NoisySimulatedWorld",
    "Prediction",
    "SimulatedWorld",
    "WorldModel",
    "create_world_schema",
    "delete_world_state",
    "deserialize_state",
    "load_world_model",
    "load_world_state",
    "plan",
    "run_episode",
    "run_episodes",
    "run_noisy_episode",
    "run_noisy_episodes",
    "save_world_state",
    "serialize_state",
]
