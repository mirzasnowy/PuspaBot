"""Tests for the LLM query decomposer in the reranker.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)

A stub LLM stands in for a real model: it returns a fixed JSON
decomposition for the prompt it receives, so the prompt, the
parsing, the de-duplication and the retrieval merge are
exercised for real while the decomposition itself is fully
controlled. The query used throughout is the complex one from
the audit: "cheap Galaxy S24 deal trustworthy and compare with
official store price". A regex split would cut that query on
"and" into two under-specified halves; the LLM stub returns
three self-contained sub-questions instead, which is exactly
what the test proves is not a regex split.
"""

import json

import pytest

from puspa.retrieval.reranker import (
    DecomposerError,
    Decomposition,
    LLMQueryDecomposer,
    RegexQueryDecomposer,
    build_decompose_prompt,
    decompose_query,
    parse_decompose_reply,
    retrieve_with_decomposition,
)

QUERY = "cheap Galaxy S24 deal trustworthy and compare with official store price"

#: What the stub LLM returns for QUERY: three self-contained
#: sub-questions, each repeating the shared context (Galaxy S24)
#: instead of the bare halves a regex split would produce.
STUB_SUBQUESTIONS = [
    "Is the cheap Galaxy S24 deal trustworthy?",
    "What is the price of the Galaxy S24 at the official store?",
    ("How does the cheap Galaxy S24 deal price compare with the official store price?"),
]


class StubDecomposerLLM:
    """A controllable stand-in for an LLM completion function.

    Returns ``reply`` for every prompt and records each prompt
    and call, so tests can prove the decomposer asked the LLM
    exactly once per query and passed the question along.
    """

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply

    @property
    def calls(self) -> int:
        return len(self.prompts)


def json_reply(subquestions):
    return json.dumps({"subquestions": subquestions})


# --- The LLM decomposer really decomposes -----------------------


def test_llm_decomposer_returns_self_contained_subquestions():
    stub = StubDecomposerLLM(json_reply(STUB_SUBQUESTIONS))
    result = LLMQueryDecomposer(stub.complete).decompose(QUERY)
    assert isinstance(result, Decomposition)
    assert result.source == "llm"
    assert result.subquestions == STUB_SUBQUESTIONS
    # Every sub-question repeats the shared context, so each
    # can be retrieved on its own; the regex split of the same
    # query produces bare halves without it.
    assert all("Galaxy S24" in sub for sub in result.subquestions)
    assert result.subquestions != decompose_query(QUERY)
    assert stub.calls == 1
    assert QUERY in stub.prompts[0]


def test_llm_decomposer_keeps_a_simple_query_whole():
    stub = StubDecomposerLLM(json_reply(["What is the price of Galaxy S24?"]))
    result = LLMQueryDecomposer(stub.complete).decompose("What is the price of Galaxy S24?")
    assert result.subquestions == ["What is the price of Galaxy S24?"]
    assert result.source == "llm"


def test_decompose_prompt_asks_for_json_and_self_contained_parts():
    prompt = build_decompose_prompt(QUERY, max_subquestions=4)
    assert QUERY in prompt
    assert "subquestions" in prompt
    assert "JSON only" in prompt
    assert "stand on its own" in prompt
    assert "4" in prompt


def test_llm_decomposer_rejects_a_non_callable():
    with pytest.raises(ValueError):
        LLMQueryDecomposer("not a callable")
    with pytest.raises(ValueError):
        LLMQueryDecomposer(lambda prompt: "{}", max_subquestions=0)
    with pytest.raises(ValueError):
        LLMQueryDecomposer(lambda prompt: "{}").decompose("")


# --- Duplicates and overlap are handled -------------------------


def test_llm_decomposer_drops_duplicate_subquestions():
    stub = StubDecomposerLLM(
        json_reply(
            [
                "Is the cheap Galaxy S24 deal trustworthy?",
                "is the cheap galaxy s24 deal trustworthy?",
                "What is the price of the Galaxy S24 at the official store?",
                "Is the cheap Galaxy S24 deal trustworthy?",
            ]
        )
    )
    result = LLMQueryDecomposer(stub.complete).decompose(QUERY)
    assert result.subquestions == [
        "Is the cheap Galaxy S24 deal trustworthy?",
        "What is the price of the Galaxy S24 at the official store?",
    ]


def test_llm_decomposer_drops_overlapping_subquestions():
    stub = StubDecomposerLLM(
        json_reply(
            [
                "What is the price of the Galaxy S24?",
                "What is the price of the Galaxy S24 at the official store?",
                "Is the cheap Galaxy S24 deal trustworthy?",
            ]
        )
    )
    result = LLMQueryDecomposer(stub.complete).decompose(QUERY)
    # The short "price" part is fully contained in the longer,
    # self-contained one, so only the longer survives.
    assert result.subquestions == [
        "What is the price of the Galaxy S24 at the official store?",
        "Is the cheap Galaxy S24 deal trustworthy?",
    ]


def test_llm_decomposer_caps_at_max_subquestions():
    stub = StubDecomposerLLM(
        json_reply([f"Sub-question {index} about Galaxy S24?" for index in range(6)])
    )
    result = LLMQueryDecomposer(stub.complete, max_subquestions=2).decompose(QUERY)
    assert len(result.subquestions) == 2
    assert result.subquestions[0].startswith("Sub-question 0")


# --- Invalid LLM replies are rejected, not silently regexed -----


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "this is not json",
        "[]",
        "{}",
        json.dumps({"subquestions": []}),
        json.dumps({"subquestions": "not a list"}),
        json.dumps({"subquestions": ["ok?", "", "also ok?"]}),
        json.dumps({"subquestions": [42, "ok?"]}),
        json.dumps({"other_key": ["ok?"]}),
    ],
)
def test_llm_decomposer_rejects_invalid_replies(reply):
    stub = StubDecomposerLLM(reply)
    with pytest.raises(DecomposerError):
        LLMQueryDecomposer(stub.complete).decompose(QUERY)


def test_parse_decompose_reply_rejects_invalid_replies_directly():
    with pytest.raises(DecomposerError):
        parse_decompose_reply("not json")
    with pytest.raises(DecomposerError):
        parse_decompose_reply(json.dumps({"subquestions": []}))
    assert parse_decompose_reply(json_reply(["a?", "b?"])) == ["a?", "b?"]


def test_llm_decomposer_rejects_a_failing_llm_call():
    def boom(prompt):
        raise RuntimeError("provider timed out")

    with pytest.raises(DecomposerError, match="timed out"):
        LLMQueryDecomposer(boom).decompose(QUERY)


def test_invalid_llm_reply_is_not_silently_replaced_by_regex():
    # The rejected reply must surface as an error, never as the
    # regex split of the same query: the caller must see the LLM
    # path failed instead of trusting a split that never came
    # from the model.
    stub = StubDecomposerLLM("not json at all")
    with pytest.raises(DecomposerError):
        retrieve_with_decomposition(lambda sub, top_k: [], QUERY, llm_complete=stub.complete)


# --- The regex fallback, with its source recorded ---------------


def test_regex_fallback_works_without_an_llm():
    result = RegexQueryDecomposer().decompose(QUERY)
    assert result.source == "regex"
    assert result.subquestions == decompose_query(QUERY)
    assert result.subquestions == [
        "cheap Galaxy S24 deal trustworthy",
        "compare with official store price",
    ]


def test_regex_fallback_rejects_bad_input():
    with pytest.raises(ValueError):
        RegexQueryDecomposer(max_subquestions=0)
    with pytest.raises(ValueError):
        RegexQueryDecomposer().decompose("")


# --- Retrieval through the LLM decomposer -----------------------


def test_retrieve_with_llm_decomposer_uses_every_subquestion():
    seen = []

    def fake_retrieve(sub_query, top_k):
        seen.append(sub_query)
        if "trustworthy" in sub_query:
            return [{"passage_id": "p-trust", "score": 0.9}]
        # "compare" is checked before "official store": the third
        # sub-question contains both phrases.
        if "compare" in sub_query:
            return [
                {"passage_id": "p-compare", "score": 0.9},
                {"passage_id": "p-trust", "score": 0.8},
            ]
        if "official store" in sub_query:
            return [{"passage_id": "p-official", "score": 0.9}]
        raise AssertionError(f"unexpected sub-question: {sub_query!r}")

    stub = StubDecomposerLLM(json_reply(STUB_SUBQUESTIONS))
    merged = retrieve_with_decomposition(fake_retrieve, QUERY, top_k=3, llm_complete=stub.complete)
    # Every sub-question was retrieved, not just the whole query:
    # each leg contributes its own passage to the merge.
    assert seen == STUB_SUBQUESTIONS
    assert [r["passage_id"] for r in merged] == ["p-trust", "p-compare", "p-official"]
    assert all(r["decomposition_source"] == "llm" for r in merged)
    assert all("rrf_score" in r for r in merged)
    trust = next(r for r in merged if r["passage_id"] == "p-trust")
    assert len(trust["subquestions"]) == 2


def test_retrieve_with_decomposer_object_uses_it():
    seen = []

    def fake_retrieve(sub_query, top_k):
        seen.append(sub_query)
        return [{"passage_id": "p-one", "score": 0.9}]

    decomposer = LLMQueryDecomposer(
        StubDecomposerLLM(
            json_reply(
                [
                    "Is the Galaxy S24 deal trustworthy?",
                    "What is the Galaxy S24 official store price?",
                ]
            )
        ).complete
    )
    merged = retrieve_with_decomposition(fake_retrieve, QUERY, top_k=2, decomposer=decomposer)
    assert seen == [
        "Is the Galaxy S24 deal trustworthy?",
        "What is the Galaxy S24 official store price?",
    ]
    assert merged[0]["decomposition_source"] == "llm"


def test_retrieve_without_llm_falls_back_to_regex_explicitly():
    seen = []

    def fake_retrieve(sub_query, top_k):
        seen.append(sub_query)
        return [{"passage_id": "p-" + str(len(seen)), "score": 0.9}]

    merged = retrieve_with_decomposition(fake_retrieve, QUERY, top_k=2)
    assert seen == decompose_query(QUERY)
    assert all(r["decomposition_source"] == "regex" for r in merged)


def test_retrieve_rejects_both_decomposer_and_llm_complete():
    decomposer = RegexQueryDecomposer()
    with pytest.raises(ValueError):
        retrieve_with_decomposition(
            lambda sub, top_k: [], QUERY, decomposer=decomposer, llm_complete=lambda prompt: "{}"
        )
    with pytest.raises(ValueError):
        retrieve_with_decomposition(lambda sub, top_k: [], QUERY, top_k=0)
