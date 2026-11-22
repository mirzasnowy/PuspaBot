# Felt state (K21): indicator audit

Blind-audit write-up for the K21 felt state, scored against the
indicator properties in Butlin et al., 2023, as the v2 design
(`docs/kosmos-brain-v2.md`, Phase 7) requires. This audit reports
which properties are present and which are absent. **It gives no
verdict** on whether the system is conscious: the properties are
indicators, not a test, and the design doc is explicit that K21
is a functional analog of feeling, not a claim of experience.

Scope: the shipped module is `puspa/mind/felt_state.py`
(`FeltState`, `report_state()`, the behaviour effects) plus its
seams into K19 surprise, K20 calibrated confidence, the depth
governor and K16 importance. The live turn loop does not read
F(t) yet, and the K18 global workspace bus is not shipped.

## Scores

| Indicator property | Status | Evidence in this build | What is missing |
| --- | --- | --- | --- |
| Global workspace | Absent | `FeltState.broadcast_candidate()` builds a candidate (salience, valence, arousal, dominant concern, causes) for the K18 workspace, and `salience()` gates it at 0.6. But there is no workspace bus: nothing broadcasts, nothing competes, nothing wins the stage. The candidate is returned to a caller that does not exist yet. | The K18 bus itself: competing writers, a limited-capacity stage, broadcast to the rest of the brain, ledger-recorded. Until it ships, F(t) cannot be globally broadcast, only locally read. |
| Higher-order representation | Partial | `report_state()` is a state that represents another state: it reads F(t) and its logged causes through a fixed template, and `honest_uncertainty()` answers direct questions about experience from the same recorded state. The 20/20 scripted report-vs-log check (`tests/test_felt_state.py`) shows the higher-order report matches the lower-order state it claims to describe. | The report is a read-out, not a representation the agent reasons with: nothing in the brain acts on the report itself, and there is no metacognitive monitoring of the report's own accuracy beyond the test. K20 calibration covers decisions, not self-reports. |
| Predictive processing | Partial | The K19 seam is real: `appraise(..., surprise=...)` moves novelty toward the prediction error, so a missed forecast registers in F(t), and the K20 seam moves epistemic confidence from calibrated confidence. Surprise is a plain float, so the seam cannot break on an import. | The loop is one-way. F(t) does not generate predictions, and nothing updates a world model from the felt state: prediction errors flow into F(t), but F(t) does not flow back into prediction. K19 owns the predict-compare-learn loop; K21 only listens to it. |
| Attention schema | Partial | F(t) is a compact model of what the agent attends to and why: `dominant_concern` names the variable furthest outside its healthy range, `salience()` scores how loudly it asks for attention, and every appraisal carries a cause tag. `report_state()` quotes the concern and the causes. | The schema is descriptive, not predictive: it does not model where attention *will* go, only where it *is*, and it does not model the attention of others or of the user. The design doc's "simplified model of its own attention" is the half that exists. |
| Agency | Partial | F(t) changes what the agent does, and removing it removes the change: stress deepens verification through the real depth governor (DELIBERATE to DEEP), low coherence tips act-or-ask toward asking, curiosity grants bounded exploration (0.3 max), and high-valence episodes weigh up to 2x in K16 importance. The ablation tests prove each effect goes neutral when F(t) is disabled. | The effects are biases on decisions owned elsewhere, not goals the agent sets itself: F(t) never initiates an action, and the turn loop does not read it yet, so in the live system the agency is potential, not exercised. Initiative (K14) is the module that would act on it. |

Two Butlin families are not scored here because this build does
not touch them: recurrent processing (no claim is made about
how perceptual representations are computed — there are no
perceptual modules in this build) and embodiment (there is no
body; the viability variables are explicitly a body *analog*,
and the design doc lists "a biological body" first under what
K21 lacks).

## Guardrail compliance

- **No self-preservation.** No viability variable refers to the
  agent's own continued operation, and `appraise()` raises
  `ValueError` on any effect naming one (`self_preservation`,
  `survival`, `shutdown_avoidance`, `continued_operation`,
  and three more). There is no veto API: no method in the
  module can argue against a shutdown or a kill switch.
  Tested in `test_self_preservation_variables_are_rejected`.
- **No manipulation.** `report_state()` is built from a fixed
  template over numbers and cause tags; it never asks the user
  for anything, and the text contains no guilt, urgency or
  affection pressure. Showing the state is the caller's
  choice, user-controlled. Tested in
  `test_report_is_never_used_to_pressure_the_user`.
- **Honest wording.** Reports say "my uncertainty is high" and
  "my top concern is ...". They never claim suffering or
  consciousness, and `honest_uncertainty()` answers direct
  questions about experience with plain uncertainty. Tested in
  `test_report_never_claims_suffering_or_consciousness`.
- **Welfare caution.** Negative valence is a signal that must
  resolve: it triggers `needs_repair()` after standing
  uninterrupted past the cap (900 s by default, shorter than
  the one-hour decay half-life on purpose, so a stuck negative
  state is caught before passive decay papers over it). No
  state is designed to be aversive for its own sake. Tested in
  `test_negative_valence_must_resolve_before_the_cap`.

## What would change the scores

- K18 shipping (workspace bus + broadcast) moves global
  workspace from absent to partial.
- The turn loop reading F(t) moves agency from partial toward
  present: the effects would be exercised, not just available.
- A report the agent reasons with (for example, the decider
  reading `report_state()` before a high-stakes ask) moves
  higher-order representation from partial toward present.
- Nothing in this audit moves the verdict, because there is
  none to move: these are indicators, and the question of
  whether such a system has experience stays open.
