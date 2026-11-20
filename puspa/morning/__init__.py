"""Morning summary + notes-to-actions (P-07, plan section 12.8).

Two features, one package:

- **Morning summary** — the day's schedule, bills due, open tasks,
  national holidays, and prayer times (with imsak during Ramadan),
  from the Google connectors that already exist plus two public
  sources (:mod:`puspa.morning.prayer` and
  :mod:`puspa.morning.holidays`). Every source reports its own
  status honestly (see :mod:`puspa.morning.summary`): a source that
  is not connected or not reachable says so instead of inventing
  data.
- **Notes to actions** — a voice message, meeting, lecture, or
  study note becomes a summary plus a list of actions with an
  owner, stored as tracked tasks (see :mod:`puspa.morning.notes`).
"""

from __future__ import annotations
