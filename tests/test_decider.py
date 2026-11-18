"""Tests for the decider (G9): cheap decisions, strict contract.

All tests here are in-memory: no test body touches the shared
Postgres database, so the file runs with ``--noconftest``. A
stub LLM stands in for a real model, a fake clock stands in for
``time.monotonic``, and the rule backend is exercised for real —
the deterministic path is the fallback every chain ends in, so
it must be right, not stubbed.
"""

from __future__ import annotations

import json
import time

import pytest

from puspa.decider import (
    DECIDER_JOBS,
    Decider,
    DecisionLog,
    LLMBackend,
    RuleBackend,
    UserEngineBackend,
    check_backend,
    get_seam,
    parse_reply,
)
from puspa.decider.backends import BackendError
from puspa.decider.jobs import SeamError
from puspa.decider.questions import (
    ChoiceAnswer,
    ChoiceOption,
    ChoiceQuestion,
    ScoreAnswer,
    ScoreQuestion,
    YesNoAnswer,
    YesNoQuestion,
    build_prompt,
)

MODULES = [
    {"id": "bills", "description": "Pay bills", "keywords": ["bill", "pln", "pay", "electricity"]},
    {
        "id": "prices",
        "description": "Compare prices",
        "keywords": ["price", "cheap", "compare", "deal"],
    },
    {"id": "notes", "description": "Take notes", "keywords": ["note", "remember", "write"]},
]


def routing_payload(message="pay my PLN bill"):
    return {"message": message, "modules": MODULES}


def triage_payload(**overrides):
    payload = {
        "subject": "PLN bill overdue",
        "body": "Your electricity bill is overdue, please pay today.",
        "sender": "pln@example.id",
        "known_senders": ["pln@example.id"],
    }
    payload.update(overrides)
    return payload


class FakeClock:
    """A controllable ``monotonic()``: tests advance it by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class StubComplete:
    """A controllable ``complete(prompt)`` stand-in.

    Returns ``reply`` for every prompt, records each prompt, and
    optionally advances a fake clock (simulating a slow model)
    or raises (simulating a failed call).
    """

    def __init__(self, reply, *, clock=None, advance_s=0.0, error=None):
        self.reply = reply
        self.prompts = []
        self.clock = clock
        self.advance_s = advance_s
        self.error = error

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.clock is not None and self.advance_s:
            self.clock.advance(self.advance_s)
        if self.error is not None:
            raise self.error
        return self.reply


def choice_question():
    return ChoiceQuestion(
        text="pay my PLN bill",
        options=(
            ChoiceOption(id="bills", label="Pay bills", keywords=("bill", "pln", "pay")),
            ChoiceOption(id="prices", label="Compare prices", keywords=("price", "cheap")),
        ),
        id="q1",
    )


# --- The three question shapes, typed answers ------------------


def test_choice_question_validates_its_options():
    with pytest.raises(ValueError):
        ChoiceQuestion(text="x", options=(), id="q")
    with pytest.raises(ValueError):
        ChoiceQuestion(text="x", options=(ChoiceOption(id="a"), ChoiceOption(id="a")), id="q")
    with pytest.raises(ValueError):
        ChoiceQuestion(text="  ", options=(ChoiceOption(id="a"),), id="q")


def test_score_and_yes_no_answers_validate_ranges():
    with pytest.raises(ValueError):
        ScoreAnswer(scores={"a": 1.5})
    with pytest.raises(ValueError):
        ScoreAnswer(scores={})
    with pytest.raises(ValueError):
        YesNoAnswer(value=True, confidence=-0.1)
    with pytest.raises(ValueError):
        YesNoAnswer(value="yes", confidence=0.5)
    assert ChoiceAnswer(choice="a", confidence=1.0).to_dict() == {
        "kind": "choice",
        "choice": "a",
        "confidence": 1.0,
    }


def test_build_prompt_asks_for_json_only():
    prompt = build_prompt(choice_question())
    assert "bills" in prompt and "prices" in prompt
    assert "JSON only" in prompt
    assert "confidence" in prompt
    assert "scores" in build_prompt(
        ScoreQuestion(text="t", options=(ChoiceOption(id="a"),), id="q")
    )
    assert "true/false" in build_prompt(YesNoQuestion(text="t", id="q"))


# --- The rule backend answers all three shapes -----------------


def test_rule_backend_choice_picks_keyword_match():
    answer = RuleBackend().ask(choice_question())
    assert isinstance(answer, ChoiceAnswer)
    assert answer.choice == "bills"
    assert answer.confidence == 1.0  # 3 of 3 keywords hit


def test_rule_backend_choice_with_no_match_is_an_unconfident_guess():
    question = ChoiceQuestion(
        text="something unrelated entirely",
        options=(ChoiceOption(id="a", keywords=("zzz",)), ChoiceOption(id="b", keywords=("yyy",))),
        id="q",
    )
    answer = RuleBackend().ask(question)
    assert answer.choice == "a"  # first option wins the tie
    assert answer.confidence == 0.0


def test_rule_backend_scores_every_option():
    question = ScoreQuestion(
        text="cheap price deal",
        options=(
            ChoiceOption(id="a", keywords=("cheap", "price")),
            ChoiceOption(id="b", keywords=("bill",)),
        ),
        id="q",
    )
    answer = RuleBackend().ask(question)
    assert isinstance(answer, ScoreAnswer)
    assert answer.scores == {"a": 1.0, "b": 0.0}


def test_rule_backend_yes_no_uses_context_keywords():
    question = YesNoQuestion(
        text="is the bill overdue?",
        context={"yes_keywords": ["overdue", "late"], "no_keywords": ["paid", "settled"]},
        id="q",
    )
    answer = RuleBackend().ask(question)
    assert isinstance(answer, YesNoAnswer)
    assert answer.value is True
    assert answer.confidence == 1.0


def test_rule_backend_yes_no_without_evidence_defaults_unconfident():
    answer = RuleBackend().ask(YesNoQuestion(text="anything?", id="q"))
    assert answer.value is False
    assert answer.confidence == 0.0


# --- The LLM backend parses strictly ---------------------------


def test_llm_backend_parses_a_typed_answer():
    backend = LLMBackend(StubComplete(json.dumps({"choice": "prices", "confidence": 0.8})))
    answer = backend.ask(choice_question())
    assert answer == ChoiceAnswer(choice="prices", confidence=0.8)


def test_llm_backend_rejects_unknown_option_and_bad_json():
    question = choice_question()
    with pytest.raises(BackendError) as exc:
        LLMBackend(StubComplete(json.dumps({"choice": "nope", "confidence": 0.5}))).ask(question)
    assert exc.value.reason == "unparseable_answer"
    with pytest.raises(BackendError) as exc:
        LLMBackend(StubComplete("not json at all")).ask(question)
    assert exc.value.reason == "unparseable_answer"
    with pytest.raises(BackendError) as exc:
        LLMBackend(StubComplete(json.dumps({"scores": {"a": 2.0}}))).ask(
            ScoreQuestion(text="t", options=(ChoiceOption(id="a"),), id="q")
        )
    assert exc.value.reason == "unparseable_answer"


def test_parse_reply_requires_exact_score_coverage():
    question = ScoreQuestion(text="t", options=(ChoiceOption(id="a"), ChoiceOption(id="b")), id="q")
    with pytest.raises(BackendError):
        parse_reply(question, json.dumps({"scores": {"a": 0.5}}))
    answer = parse_reply(question, json.dumps({"scores": {"a": 0.5, "b": 0.9}}))
    assert answer.scores == {"a": 0.5, "b": 0.9}


# --- Backends are pluggable; the chain falls back --------------


def test_decider_uses_llm_when_it_answers():
    llm = LLMBackend(StubComplete(json.dumps({"choice": "prices", "confidence": 0.9})))
    decider = Decider([llm, RuleBackend()])
    decision = decider.decide("moduleRouting", routing_payload("find a cheap price"))
    assert decision.ok is True
    assert decision.value == {"module_id": "prices", "confidence": 0.9}
    assert decision.backend == "llm"
    assert decision.attempts == [{"backend": "llm", "ok": True, "reason": None}]


def test_decider_falls_back_to_rule_when_llm_fails():
    llm = LLMBackend(StubComplete("garbage, not json"))
    decider = Decider([llm, RuleBackend()])
    decision = decider.decide("moduleRouting", routing_payload())
    assert decision.ok is True
    assert decision.value["module_id"] == "bills"
    assert decision.backend == "rule"
    assert decision.attempts[0] == {"backend": "llm", "ok": False, "reason": "unparseable_answer"}
    assert decision.attempts[1] == {"backend": "rule", "ok": True, "reason": None}


def test_decider_falls_back_when_llm_call_raises():
    llm = LLMBackend(StubComplete("", error=RuntimeError("connection reset")))
    decider = Decider([llm, RuleBackend()])
    decision = decider.decide("moduleRouting", routing_payload())
    assert decision.ok is True
    assert decision.backend == "rule"
    assert decision.attempts[0]["reason"] == "backend_error"


def test_user_engine_backend_answers_when_configured():
    engine = UserEngineBackend(StubComplete(json.dumps({"choice": "notes", "confidence": 0.7})))
    decider = Decider([engine, RuleBackend()])
    decision = decider.decide("moduleRouting", routing_payload("remember this note"))
    assert decision.ok is True
    assert decision.value["module_id"] == "notes"
    assert decision.backend == "user_engine"


# --- Timeout: 1.500 ms, with a fake clock ----------------------


def test_timeout_with_fake_clock_is_a_failure_value():
    clock = FakeClock()
    slow = LLMBackend(
        StubComplete(json.dumps({"choice": "bills", "confidence": 0.9}), clock=clock, advance_s=2.0)
    )
    decider = Decider([slow, RuleBackend()], clock=clock)
    decision = decider.decide("moduleRouting", routing_payload())
    # The slow LLM burned the budget, so the chain fell to the rule.
    assert decision.ok is True
    assert decision.backend == "rule"
    assert decision.attempts[0] == {"backend": "llm", "ok": False, "reason": "timeout"}


def test_timeout_with_no_fallback_left_is_a_failure_value():
    clock = FakeClock()
    slow = LLMBackend(
        StubComplete(json.dumps({"choice": "bills", "confidence": 0.9}), clock=clock, advance_s=2.0)
    )
    decider = Decider([slow], clock=clock)
    decision = decider.decide("moduleRouting", routing_payload())
    assert decision.ok is False
    assert decision.reason == "timeout"
    assert decision.value is None


def test_real_slow_backend_is_cut_off_by_the_thread_budget():
    class SleepingBackend:
        name = "sleeper"
        timeout_ms = None

        def ask(self, question):
            time.sleep(0.5)
            return ChoiceAnswer(choice="bills", confidence=1.0)

        def check_key(self):
            return True, None

    decider = Decider([SleepingBackend()], timeout_ms=50)
    started = time.monotonic()
    decision = decider.decide("moduleRouting", routing_payload())
    elapsed = time.monotonic() - started
    assert decision.ok is False
    assert decision.reason == "timeout"
    assert elapsed < 0.5  # the turn did not wait for the sleeper


# --- Failure is always a value, never an exception -------------


def test_unknown_job_is_a_failure_value():
    decision = Decider([RuleBackend()]).decide("noSuchJob", {"message": "hi"})
    assert decision.ok is False
    assert decision.reason == "unknown_job"


def test_missing_modules_is_a_failure_value():
    decision = Decider([RuleBackend()]).decide("moduleRouting", {"message": "hi", "modules": []})
    assert decision.ok is False
    assert decision.reason == "no_modules"


def test_invalid_payload_is_a_failure_value():
    decision = Decider([RuleBackend()]).decide("moduleRouting", {"modules": MODULES})
    assert decision.ok is False
    assert decision.reason == "invalid_payload"
    decision = Decider([RuleBackend()]).decide("inboxTriage", {"subject": "", "body": ""})
    assert decision.ok is False
    assert decision.reason == "invalid_payload"


def test_answer_type_mismatch_is_a_failure_value():
    class WrongShapeBackend:
        name = "wrong"
        timeout_ms = None

        def ask(self, question):
            return YesNoAnswer(value=True, confidence=1.0)

        def check_key(self):
            return True, None

    decision = Decider([WrongShapeBackend()]).decide("moduleRouting", routing_payload())
    assert decision.ok is False
    assert decision.reason == "answer_type_mismatch"


def test_decide_never_raises_even_when_a_backend_explodes():
    class ExplodingBackend:
        name = "boom"
        timeout_ms = None

        def ask(self, question):
            raise SystemError("totally broken")

        def check_key(self):
            raise RuntimeError("key check broken too")

    decision = Decider([ExplodingBackend()]).decide("moduleRouting", routing_payload())
    assert decision.ok is False
    # The key-check broke first, so the backend was skipped.
    assert decision.reason == "key_check_failed: RuntimeError: key check broken too"


# --- Jobs are explicit, each with its own seam -----------------


def test_decider_jobs_are_explicit():
    assert DECIDER_JOBS == ("moduleRouting", "inboxTriage")
    assert set(get_seam("moduleRouting").__dict__) == {"build_question", "interpret"}
    with pytest.raises(SeamError) as exc:
        get_seam("roomRouting")
    assert exc.value.reason == "unknown_job"


# --- Key-check: fail fast with a clear reason ------------------


def test_key_check_rule_backend_always_ready():
    assert check_backend(RuleBackend()) == {"ok": True, "reason": None}


def test_key_check_llm_backend_with_and_without_a_key():
    ok_backend = LLMBackend(StubComplete("{}"), key_resolver=lambda provider: "sk-test")
    assert check_backend(ok_backend) == {"ok": True, "reason": None}
    missing = LLMBackend(StubComplete("{}"), key_resolver=lambda provider: None)
    assert check_backend(missing) == {"ok": False, "reason": "missing_api_key"}


def test_key_check_user_engine_needs_an_engine():
    assert check_backend(UserEngineBackend()) == {"ok": False, "reason": "no_user_engine"}
    assert check_backend(UserEngineBackend(lambda p: "{}")) == {"ok": True, "reason": None}


def test_decider_skips_a_backend_with_no_key_instead_of_timing_out():
    llm = LLMBackend(
        StubComplete(json.dumps({"choice": "prices", "confidence": 0.9})),
        key_resolver=lambda provider: None,
    )
    decider = Decider([llm, RuleBackend()])
    decision = decider.decide("moduleRouting", routing_payload())
    assert decision.ok is True
    assert decision.backend == "rule"
    assert decision.attempts[0] == {"backend": "llm", "ok": False, "reason": "missing_api_key"}
    assert llm._complete.prompts == []  # never called at all


# --- Log + state hash: audit and replay ------------------------


def test_same_decision_same_hash_different_input_different_hash():
    log = DecisionLog()
    decider = Decider([RuleBackend()], log=log)
    first = decider.decide("moduleRouting", routing_payload())
    second = decider.decide("moduleRouting", routing_payload())
    assert first.state_hash == second.state_hash
    assert len(first.state_hash) == 64
    other = decider.decide("moduleRouting", routing_payload("compare prices please"))
    assert other.state_hash != first.state_hash
    assert len(log) == 3
    found = log.find(first.state_hash)
    assert found is not None
    assert found["job"] == "moduleRouting"
    assert found["value"] == first.value
    assert log.find("0" * 64) is None


def test_failed_decision_is_logged_too():
    log = DecisionLog()
    decider = Decider([RuleBackend()], log=log)
    decision = decider.decide("noSuchJob", {})
    assert decision.ok is False
    assert len(log) == 1
    assert log.entries[0]["ok"] is False
    assert log.entries[0]["reason"] == "unknown_job"


# --- Jobs end-to-end with the rule backend ---------------------


def test_module_routing_end_to_end():
    decider = Decider([RuleBackend()])
    decision = decider.decide("moduleRouting", routing_payload("compare prices for this deal"))
    assert decision.ok is True
    assert decision.value == {"module_id": "prices", "confidence": 1.0}
    assert decision.backend == "rule"
    assert decision.state_hash is not None


def test_inbox_triage_end_to_end_act_now_from_known_sender():
    decider = Decider([RuleBackend()])
    decision = decider.decide("inboxTriage", triage_payload())
    assert decision.ok is True
    assert decision.value == {"action": "act_now", "priority": "high", "confidence": 1.0}


def test_inbox_triage_ignore_and_later():
    decider = Decider([RuleBackend()])
    promo = decider.decide(
        "inboxTriage",
        triage_payload(
            subject="Big sale!",
            body="Promo discount, click here to unsubscribe.",
            sender="shop@example.com",
            known_senders=[],
        ),
    )
    assert promo.value["action"] == "ignore"
    assert promo.value["priority"] == "low"
    later = decider.decide(
        "inboxTriage",
        triage_payload(
            subject="Monthly update",
            body="FYI update for next week.",
            sender="team@example.com",
            known_senders=["team@example.com"],
        ),
    )
    assert later.value == {"action": "act_later", "priority": "medium", "confidence": 1.0}


def test_inbox_triage_unknown_sender_act_now_is_medium():
    decider = Decider([RuleBackend()])
    decision = decider.decide(
        "inboxTriage", triage_payload(sender="stranger@example.com", known_senders=[])
    )
    assert decision.value["action"] == "act_now"
    assert decision.value["priority"] == "medium"
