"""Run a mission one step at a time (V2-42, issue #219).

``advance_mission`` takes the next pending checklist step and
runs it through the same turn loop a chat turn uses (or a
``step_runner`` callable in tests): the step text becomes the
turn's first message and the turn's reply is the step's result.
Each step outcome is recorded in the ledger, and a finished
mission is recorded too.

A step that needs a decision does not guess. When a step
carries a ``question`` the mission pauses in ``waiting`` and
records an ``approval_requested`` ledger event; the user
answers through ``answer_step_ask`` (or the PWA), and only
then does the step run — with the answer in its prompt. A
declined question (``go=False``) skips the step and stops the
mission, recorded as ``approval_rejected``.

With no turn loop wired in, the step is recorded as ``not_run``
and the mission stays ``active``: never silently pretended to
have run, the same rule user routines follow.
"""

from __future__ import annotations

from .missions import Mission, MissionError, _ledger, _save, get_mission

#: How much of a step's result is kept on the step and in the ledger.
RESULT_MAX_CHARS = 2000


def _result_text(result) -> tuple[str, str, str]:
    """Normalise a turn result (object or dict) to (status, text, message)."""
    if isinstance(result, dict):
        return (
            str(result.get("status", "failed")),
            str(result.get("text", "")),
            str(result.get("message", "")),
        )
    return (
        str(getattr(result, "status", "failed")),
        str(getattr(result, "text", "")),
        str(getattr(result, "message", "")),
    )


def _step_prompt(mission: Mission, step_index: int) -> str:
    """The prompt for one step: goal, step, and decisions so far."""
    step = mission.steps[step_index]
    lines = [
        f"Misi: {mission.goal}",
        f"Langkah {step_index + 1} dari {len(mission.steps)}: {step.title}",
    ]
    decided = [
        f"- {s.question} -> {s.answer}"
        for s in mission.steps
        if s.question and s.answer is not None
    ]
    if decided:
        lines.append("Keputusan user sejauh ini:")
        lines.extend(decided)
    done = [
        f"- {s.title}: {(s.result or '')[:300]}"
        for s in mission.steps[:step_index]
        if s.status == "done"
    ]
    if done:
        lines.append("Hasil langkah sebelumnya:")
        lines.extend(done)
    return "\n".join(lines)


def _waiting_step(mission: Mission):
    """The open step-ask (index, step), or None when not waiting."""
    for index, step in enumerate(mission.steps):
        if step.status == "active" and step.question and step.answer is None:
            return index, step
    return None


def advance_mission(
    mission_id: str,
    user_id: str,
    *,
    dsn=None,
    turn_loop=None,
    step_runner=None,
    task_id: str | None = None,
) -> dict:
    """Run the mission's next step (one step per call).

    Returns ``{"status": ..., "mission": {...}}`` where status is
    ``advanced`` (a step finished, more remain), ``done`` (the
    last step finished), ``waiting`` (a step asked the user a
    question), ``not_run`` (no turn loop wired in), ``failed``
    (the step failed and the mission stopped), or
    ``already_finished``.
    """
    mission = get_mission(mission_id, user_id, dsn=dsn)
    if mission.status in ("done", "failed", "stopped"):
        return {"status": "already_finished", "mission": mission.to_dict()}
    if mission.status == "waiting":
        found = _waiting_step(mission)
        if found is None:
            # Inconsistent row (waiting with no open question):
            # report the wait honestly instead of inventing a finish.
            return {"status": "waiting", "mission": mission.to_dict()}
        index, step = found
        return {
            "status": "waiting",
            "mission": mission.to_dict(),
            "step_index": index,
            "question": step.question,
            "options": list(step.options),
        }
    pending = next((i for i, s in enumerate(mission.steps) if s.status == "pending"), None)
    if pending is None:
        mission.status = "done"
        _save(mission, dsn=dsn)
        _ledger(
            mission.id,
            "task_completed",
            {
                "kind": "mission",
                "mission_id": mission.id,
                "title": mission.title,
                "steps_done": sum(1 for s in mission.steps if s.status == "done"),
            },
            dsn=dsn,
        )
        return {"status": "done", "mission": mission.to_dict()}
    step = mission.steps[pending]
    if step.question is not None and step.answer is None:
        step.status = "active"
        mission.status = "waiting"
        _save(mission, dsn=dsn)
        _ledger(
            mission.id,
            "approval_requested",
            {
                "kind": "mission_step_ask",
                "mission_id": mission.id,
                "step_index": pending,
                "step": step.title,
                "question": step.question,
                "options": list(step.options),
            },
            dsn=dsn,
        )
        return {
            "status": "waiting",
            "mission": mission.to_dict(),
            "step_index": pending,
            "question": step.question,
            "options": list(step.options),
        }
    if turn_loop is None and step_runner is None:
        return {
            "status": "not_run",
            "mission": mission.to_dict(),
            "step_index": pending,
            "reason": "no turn loop wired in; the step was not run",
        }
    step.status = "active"
    _save(mission, dsn=dsn)
    prompt = _step_prompt(mission, pending)
    run_task_id = task_id or f"{mission.id}-langkah-{pending + 1}"
    if step_runner is not None:
        raw = step_runner(prompt, task_id=run_task_id)
    else:
        from puspa.turns.messages import InternalMessage

        raw = turn_loop.run_turn(
            [InternalMessage.user(prompt)],
            user_id=str(user_id),
            task_id=run_task_id,
        )
    status, text, message = _result_text(raw)
    if status == "completed":
        step.status = "done"
        step.result = (text or "").strip()[:RESULT_MAX_CHARS]
        _save(mission, dsn=dsn)
        _ledger(
            mission.id,
            "task_completed",
            {
                "kind": "mission_step",
                "mission_id": mission.id,
                "step_index": pending,
                "step": step.title,
                "result": step.result,
            },
            dsn=dsn,
        )
        if any(s.status == "pending" for s in mission.steps):
            return {
                "status": "advanced",
                "mission": mission.to_dict(),
                "step_index": pending,
                "result": step.result,
            }
        mission.status = "done"
        _save(mission, dsn=dsn)
        _ledger(
            mission.id,
            "task_completed",
            {
                "kind": "mission",
                "mission_id": mission.id,
                "title": mission.title,
                "steps_done": sum(1 for s in mission.steps if s.status == "done"),
            },
            dsn=dsn,
        )
        return {"status": "done", "mission": mission.to_dict(), "step_index": pending}
    step.status = "failed"
    step.result = (message or text or f"turn ended {status}")[:RESULT_MAX_CHARS]
    mission.status = "failed"
    _save(mission, dsn=dsn)
    _ledger(
        mission.id,
        "task_failed",
        {
            "kind": "mission_step",
            "mission_id": mission.id,
            "step_index": pending,
            "step": step.title,
            "error": step.result,
        },
        dsn=dsn,
    )
    return {
        "status": "failed",
        "mission": mission.to_dict(),
        "step_index": pending,
        "error": step.result,
    }


def answer_step_ask(
    mission_id: str,
    user_id: str,
    *,
    answer: str = "",
    go: bool = True,
    dsn=None,
) -> Mission:
    """Answer the mission's open step-ask question.

    ``go=True`` (with the answer, free text or one of the
    offered options) sends the mission back to ``active``: the
    step runs next with the answer in its prompt.
    ``go=False`` declines: the step is skipped and the mission
    stops, recorded as ``approval_rejected``. Raises
    :class:`MissionError` when the mission is not waiting.
    """
    mission = get_mission(mission_id, user_id, dsn=dsn)
    if mission.status != "waiting":
        raise MissionError(f"mission is not waiting for an answer: {mission_id!r}")
    found = _waiting_step(mission)
    if found is None:
        raise MissionError(f"mission has no open question: {mission_id!r}")
    index, step = found
    answer_text = str(answer or "").strip()
    step.answer = answer_text
    event = {
        "kind": "mission_step_ask",
        "mission_id": mission.id,
        "step_index": index,
        "step": step.title,
        "question": step.question,
        "answer": answer_text,
    }
    if go:
        step.status = "pending"
        mission.status = "active"
        _save(mission, dsn=dsn)
        _ledger(mission.id, "approval_granted", event, dsn=dsn)
    else:
        step.status = "skipped"
        mission.status = "stopped"
        _save(mission, dsn=dsn)
        _ledger(mission.id, "approval_rejected", event, dsn=dsn)
    return mission
