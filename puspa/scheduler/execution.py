"""Execution-prompt composition for scheduled runs (G8).

A scheduled run always executes with a fresh agent and no
conversation history: the prompt carries everything the agent
needs (the job's own prompt, the skills injected for it, the
delivery target, and the run identity), so a run never inherits
state from an earlier run or an unrelated conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "ExecutionRequest",
    "compose_execution_prompt",
    "build_execution",
]


@dataclass
class ExecutionRequest:
    """Everything one scheduled execution needs, no history."""

    job_id: str
    run_id: str
    prompt: str
    skills: list[str] = field(default_factory=list)
    deliver_to: str = ""
    model: str | None = None
    provider: str | None = None
    script: str | None = None
    due_state: str = "due"

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "prompt": self.prompt,
            "skills": list(self.skills),
            "deliver_to": self.deliver_to,
            "model": self.model,
            "provider": self.provider,
            "script": self.script,
            "due_state": self.due_state,
        }


def compose_execution_prompt(job: dict, *, run_id: str,
                             due_state: str = "due") -> str:
    """Compose the single prompt a fresh agent executes for a run.

    The prompt states the run identity, the job's own prompt, the
    injected skills (if any), and where the result goes. Nothing
    from any previous run or conversation is included.
    """
    prompt = str(job.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("job has no prompt to execute")
    skills = [str(s) for s in (job.get("skills") or []) if str(s).strip()]
    deliver_to = str(job.get("deliver_to") or "").strip()
    lines = [
        f"Scheduled run {run_id} for job {job.get('id', '?')}.",
        "You are a fresh agent with no conversation history.",
        f"Run state: {due_state}.",
        "",
        "Task:",
        prompt,
    ]
    if skills:
        lines += ["", "Injected skills: " + ", ".join(skills) + "."]
    if deliver_to:
        lines += ["", f"Send the result to: {deliver_to}."]
    else:
        lines += ["", "No delivery target is set; return the result."]
    return "\n".join(lines)


def build_execution(job: dict, *, run_id: str,
                    due_state: str = "due") -> ExecutionRequest:
    """Build an :class:`ExecutionRequest` from a stored job dict."""
    return ExecutionRequest(
        job_id=str(job.get("id", "")),
        run_id=run_id,
        prompt=compose_execution_prompt(job, run_id=run_id,
                                        due_state=due_state),
        skills=[str(s) for s in (job.get("skills") or []) if str(s).strip()],
        deliver_to=str(job.get("deliver_to") or ""),
        model=job.get("model"),
        provider=job.get("provider"),
        script=job.get("script"),
        due_state=due_state,
    )
