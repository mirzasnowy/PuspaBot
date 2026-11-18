"""Tests for execution-prompt composition (execution.py). In-memory."""

import pytest

from puspa.scheduler.execution import (
    ExecutionRequest,
    build_execution,
    compose_execution_prompt,
)

JOB = {
    "id": "cron-abc",
    "prompt": "Check the PLN bill and summarise it.",
    "skills": ["read_bill", "summarise"],
    "deliver_to": "telegram:123",
    "model": "model-x",
    "provider": "openrouter",
}


def test_prompt_contains_job_prompt_skills_and_target():
    prompt = compose_execution_prompt(JOB, run_id="run-1")
    assert "Check the PLN bill and summarise it." in prompt
    assert "read_bill" in prompt and "summarise" in prompt
    assert "telegram:123" in prompt
    assert "run-1" in prompt
    assert "no conversation history" in prompt.lower()


def test_prompt_without_skills_or_target_still_complete():
    prompt = compose_execution_prompt(
        {"id": "cron-x", "prompt": "Say hello."}, run_id="run-2")
    assert "Say hello." in prompt
    assert "No delivery target is set" in prompt


def test_prompt_marks_late_state():
    prompt = compose_execution_prompt(JOB, run_id="run-3", due_state="late")
    assert "late" in prompt


def test_empty_prompt_is_refused():
    with pytest.raises(ValueError):
        compose_execution_prompt({"id": "cron-x", "prompt": "  "},
                                 run_id="run-4")


def test_build_execution_returns_request():
    request = build_execution(JOB, run_id="run-5", due_state="due")
    assert isinstance(request, ExecutionRequest)
    assert request.job_id == "cron-abc"
    assert request.run_id == "run-5"
    assert request.skills == ["read_bill", "summarise"]
    assert request.deliver_to == "telegram:123"
    assert request.model == "model-x"
    assert request.provider == "openrouter"
    assert request.due_state == "due"
    assert "Check the PLN bill" in request.prompt
    assert request.to_dict()["job_id"] == "cron-abc"
