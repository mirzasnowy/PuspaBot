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
  ``TaskRunner`` (``puspa/runtime/tasks.py``); plus the
  bitemporal version table (``belief_versions``) and its as-of
  queries (``save_belief_versions`` / ``load_belief_history`` /
  ``beliefs_known_at`` / ``beliefs_valid_at``).
- ``puspa.world.bitemporal``: bitemporal belief versions (Kosmos
  K3) — two time pairs per belief, corrections append a new
  version and never overwrite history, as-of queries on known
  time and valid time.
- ``puspa.world.beliefs``: the belief data holders split out of
  ``model.py`` (K4 ratchet fix) — the six belief dataclasses, the
  price-key helper, and the three constants only the beliefs need.
- ``puspa.world.causal``: causal hypotheses (Kosmos K4) — "X ->
  Y under condition C" with strength, evidence, and expiry,
  proposed and updated from the learning loop's outcomes, read
  through ``CausalLedger.expect``; the ``CausalExpectationMixin``
  gives a model the same read path (``WorldModel.expect``).
"""

from .bitemporal import (
    BeliefVersion,
    BitemporalBeliefStore,
    belief_key,
    utcnow,
)
from .causal import (
    CausalHypothesis,
    CausalLedger,
    causal_effect_key,
)
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
    apply_bitemporal_migration,
    apply_causal_migration,
    beliefs_known_at,
    beliefs_valid_at,
    causal_hypotheses_valid_at,
    delete_belief_history,
    delete_causal_hypotheses,
    delete_world_state,
    deserialize_state,
    load_belief_history,
    load_causal_ledger,
    load_world_model,
    load_world_state,
    save_belief_versions,
    save_causal_hypotheses,
    save_world_state,
    serialize_state,
)
from .store import (
    create_schema as create_world_schema,
)

__all__ = [
    "BeliefVersion",
    "BitemporalBeliefStore",
    "CausalHypothesis",
    "CausalLedger",
    "NoisySimulatedWorld",
    "Prediction",
    "SimulatedWorld",
    "WorldModel",
    "apply_bitemporal_migration",
    "apply_causal_migration",
    "belief_key",
    "beliefs_known_at",
    "beliefs_valid_at",
    "causal_effect_key",
    "causal_hypotheses_valid_at",
    "create_world_schema",
    "delete_belief_history",
    "delete_causal_hypotheses",
    "delete_world_state",
    "deserialize_state",
    "load_belief_history",
    "load_causal_ledger",
    "load_world_model",
    "load_world_state",
    "plan",
    "run_episode",
    "run_episodes",
    "run_noisy_episode",
    "run_noisy_episodes",
    "save_belief_versions",
    "save_causal_hypotheses",
    "save_world_state",
    "serialize_state",
    "utcnow",
]
