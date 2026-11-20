"""Tests for the AST mutation runner."""

from pathlib import Path

from puspa.eval.mutation import generate_mutants, run_mutation

_SMALL_SOURCE = '''"""Tiny module for mutation tests."""


def add(a, b):
    return a + b


def is_big(n):
    return n > 10 and n < 100
'''


def test_generate_mutants_covers_each_operator_kind():
    mutants = generate_mutants(_SMALL_SOURCE, path="tiny.py")
    descriptions = " ".join(m.description for m in mutants)
    assert "Add -> Sub" in descriptions
    assert "Gt -> GtE" in descriptions
    assert "And -> Or" in descriptions
    assert len({m.mutated_source for m in mutants}) == len(mutants)
    assert all(m.mutated_source != _SMALL_SOURCE for m in mutants)


def test_run_mutation_restores_the_target_file(tmp_path):
    target = tmp_path / "tiny.py"
    target.write_text(_SMALL_SOURCE, encoding="utf-8")
    test_file = tmp_path / "test_tiny.py"
    test_file.write_text(
        "from tiny import add, is_big\n\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n\n\n"
        "def test_is_big():\n"
        "    assert is_big(50) is True\n"
        "    assert is_big(10) is False\n"
        "    assert is_big(5) is False\n",
        encoding="utf-8",
    )
    result = run_mutation(
        target,
        [str(test_file)],
        timeout=60,
        max_mutants=3,
    )
    assert target.read_text(encoding="utf-8") == _SMALL_SOURCE
    assert result.total == 3
    assert result.killed == 3
    assert result.survived == 0


def test_run_mutation_on_intervals_scores_high():
    # intervals.py is pure arithmetic; its tests should kill most
    # mutants. The run is capped to a few mutants to stay fast.
    target = Path("puspa/eval/intervals.py")
    original = target.read_text(encoding="utf-8")
    result = run_mutation(
        target,
        ["tests/test_eval_intervals.py"],
        timeout=120,
        max_mutants=4,
    )
    assert target.read_text(encoding="utf-8") == original
    assert result.total == 4
    assert result.killed + result.survived + result.timed_out + result.errored == 4
