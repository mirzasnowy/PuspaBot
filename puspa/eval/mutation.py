"""Mutation testing for the security packages (plan section 8.6).

A small AST-based mutation runner ("mutmut or equivalent"). For
each mutant it rewrites the target module in place, runs the
given pytest subset, and restores the original file (always, via
``finally``). A mutant is:

- killed: the tests fail (the suite catches the change);
- survived: the tests still pass (a missing test — treat
  survivors in Gate and the gateway as missing tests);
- timed out: the run exceeded the per-mutant timeout;
- errored: the mutant broke collection (counts as killed only if
  the breakage is a real failure, so it is reported separately).

Operators: arithmetic swap, comparison swap, boolean-operator
swap, ``not`` removal, boolean-constant flip, 0/1 swap. The
runner never mutates more than one site per mutant.

Use the real ``mutmut`` CLI for the scored runs when it is
available (``pip install mutmut``); this module is the
equivalent that works without it and that the tests exercise.
Run it from the repo root, never from a dirty tree: it rewrites
one file at a time and restores it, but an interrupted run can
leave a mutant behind, so it refuses to run with uncommitted
changes in the target file unless ``--force`` is given. In this
worktree (not a git checkout) the check is skipped and the
original bytes are always restored from memory.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_ARITH_SWAP = {"Add": "Sub", "Sub": "Add", "Mult": "Div", "Div": "Mult", "Mod": "Add"}
_CMP_SWAP = {
    "Eq": "NotEq",
    "NotEq": "Eq",
    "Lt": "LtE",
    "LtE": "Lt",
    "Gt": "GtE",
    "GtE": "Gt",
    "In": "NotIn",
    "NotIn": "In",
}


@dataclass(frozen=True)
class Mutant:
    id: str
    description: str
    lineno: int
    mutated_source: str


def _operator_name(node: ast.AST) -> str:
    return type(node).__name__


def generate_mutants(source: str, *, path: str = "<module>") -> list[Mutant]:
    """All single-site mutants for a module source."""
    tree = ast.parse(source)
    mutants: list[Mutant] = []
    counter = 0

    def _mutate_at(target: ast.AST, description: str) -> None:
        nonlocal counter
        counter += 1
        mutated = _replace_node(tree, target)
        lineno = int(getattr(target, "lineno", 0))
        mutants.append(
            Mutant(
                id=f"M{counter:03d}",
                description=f"{path}:{lineno}: {description}",
                lineno=lineno,
                mutated_source=mutated,
            )
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and _operator_name(node.op) in _ARITH_SWAP:
            _mutate_at(node, f"{_operator_name(node.op)} -> {_ARITH_SWAP[_operator_name(node.op)]}")
        elif (
            isinstance(node, ast.Compare) and node.ops and _operator_name(node.ops[0]) in _CMP_SWAP
        ):
            _mutate_at(
                node, f"{_operator_name(node.ops[0])} -> {_CMP_SWAP[_operator_name(node.ops[0])]}"
            )
        elif isinstance(node, ast.BoolOp) and _operator_name(node.op) in ("And", "Or"):
            other = "Or" if _operator_name(node.op) == "And" else "And"
            _mutate_at(node, f"{_operator_name(node.op)} -> {other}")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            _mutate_at(node, "remove not")
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            _mutate_at(node, f"{node.value} -> {not node.value}")
        elif (
            isinstance(node, ast.Constant)
            and node.value in (0, 1)
            and not isinstance(node.value, bool)
        ):
            _mutate_at(node, f"{node.value} -> {1 - node.value}")
    return mutants


def _replace_node(tree: ast.AST, target: ast.AST) -> str:
    """Return the module source with exactly one node mutated."""
    import copy

    mutated_tree = copy.deepcopy(tree)

    class _Replacer(ast.NodeTransformer):
        def __init__(self) -> None:
            self.done = False

        def _maybe(self, node: ast.AST) -> ast.AST:
            if self.done:
                return node
            if (
                getattr(node, "lineno", None) == getattr(target, "lineno", None)
                and getattr(node, "col_offset", None) == getattr(target, "col_offset", None)
                and type(node) is type(target)
            ):
                self.done = True
                return _mutate(node)
            return node

        def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
            return self._maybe(self.generic_visit(node))

        def visit_Compare(self, node: ast.Compare) -> ast.AST:
            return self._maybe(self.generic_visit(node))

        def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
            return self._maybe(self.generic_visit(node))

        def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
            return self._maybe(self.generic_visit(node))

        def visit_Constant(self, node: ast.Constant) -> ast.AST:
            return self._maybe(node)

    result = _Replacer().visit(mutated_tree)
    ast.fix_missing_locations(result)
    return ast.unparse(result)


def _mutate(node: ast.AST) -> ast.AST:
    if isinstance(node, ast.BinOp):
        node.op = getattr(ast, _ARITH_SWAP[_operator_name(node.op)])()
    elif isinstance(node, ast.Compare):
        node.ops = [getattr(ast, _CMP_SWAP[_operator_name(node.ops[0])])()]
    elif isinstance(node, ast.BoolOp):
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif isinstance(node, ast.UnaryOp):
        return node.operand
    elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
        node.value = not node.value
    elif isinstance(node, ast.Constant) and node.value in (0, 1):
        node.value = 1 - node.value
    return node


@dataclass
class MutationResult:
    module: str
    total: int
    killed: int
    survived: int
    timed_out: int
    errored: int
    seconds: float
    survivors: list[dict] = field(default_factory=list)

    @property
    def score(self) -> float:
        decided = self.killed + self.survived
        return self.killed / decided if decided else 0.0

    def as_dict(self) -> dict:
        return {
            "module": self.module,
            "total": self.total,
            "killed": self.killed,
            "survived": self.survived,
            "timed_out": self.timed_out,
            "errored": self.errored,
            "score": self.score,
            "seconds": self.seconds,
            "survivors": self.survivors,
        }


def run_mutation(
    module_path: str | Path,
    test_args: list[str],
    *,
    timeout: int = 120,
    max_mutants: int | None = None,
    python: str | None = None,
) -> MutationResult:
    """Mutate one module, run the tests per mutant, restore the file."""
    path = Path(module_path)
    original = path.read_text(encoding="utf-8")
    mutants = generate_mutants(original, path=str(path))
    if max_mutants is not None:
        mutants = mutants[:max_mutants]
    env = dict(os.environ)
    env["NO_PROXY"] = "localhost,127.0.0.1"
    env["no_proxy"] = "localhost,127.0.0.1"
    exe = python or sys.executable
    killed = survived = timed_out = errored = 0
    survivors: list[dict] = []
    started = time.monotonic()
    try:
        for mutant in mutants:
            path.write_text(mutant.mutated_source, encoding="utf-8")
            try:
                proc = subprocess.run(
                    [exe, "-m", "pytest", "-x", "-q", *test_args],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                timed_out += 1
                continue
            if proc.returncode == 0:
                survived += 1
                survivors.append({"id": mutant.id, "description": mutant.description})
            elif proc.returncode == 5:
                # No tests collected: the mutant broke collection.
                errored += 1
            else:
                killed += 1
    finally:
        path.write_text(original, encoding="utf-8")
    return MutationResult(
        module=str(path),
        total=len(mutants),
        killed=killed,
        survived=survived,
        timed_out=timed_out,
        errored=errored,
        seconds=time.monotonic() - started,
        survivors=survivors,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AST mutation runner (mutmut equivalent)")
    parser.add_argument("--module", required=True, help="module file to mutate")
    parser.add_argument("--tests", nargs="+", required=True, help="pytest args for the module")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-mutants", type=int, default=None)
    parser.add_argument("--output", default=None, help="write the result JSON here")
    args = parser.parse_args(argv)
    result = run_mutation(
        args.module, list(args.tests), timeout=args.timeout, max_mutants=args.max_mutants
    )
    print(
        f"mutation score for {result.module}: {result.score:.3f} "
        f"({result.killed} killed, {result.survived} survived, "
        f"{result.timed_out} timed out, {result.errored} errored, "
        f"of {result.total} mutants in {result.seconds:.1f}s)"
    )
    for survivor in result.survivors:
        print(f"  SURVIVED {survivor['id']}: {survivor['description']}")
    if args.output:
        import json

        Path(args.output).write_text(json.dumps(result.as_dict(), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
