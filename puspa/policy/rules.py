"""Safe rule language for the action gateway (G5, maturity phase).

A small expression language for action policies, evaluated by the
tokenizer, parser and evaluator in this module — never ``eval()``.
It supports ``contains()`` and ``matches()`` (both case-insensitive)
over the attributes OpenBot documents: ``tool.name``, ``intent``,
``bot.id``, ``actor.id``, ``page.url``, ``page.host``,
``element.ref`` / ``element.role`` / ``element.name`` /
``element.type``, ``key``, ``command``, ``file.path`` /
``file.name`` / ``file.extension``, ``mcp.server`` / ``mcp.tool`` /
``mcp.effect``, ``initiator.kind`` / ``initiator.id``; plus
``and`` / ``or`` / ``not``, parentheses, ``==`` / ``!=`` against
string literals, and the literals ``true`` / ``false``.

The pattern (deny-before-allow, fail-closed, malformed policy
halts startup) follows OpenBot's ``docs/architecture.md`` +
``app/src/lib/boundaries/rules.ts``, rewritten as Puspa code from
the documentation; the enforcing CEL code is not in the OpenBot
repo, so this evaluator is Puspa's own. The gateway that applies
these rules lives in ``puspa.policy.gateway``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ATTRIBUTES",
    "MAX_RULE_LENGTH",
    "CompiledRule",
    "PolicyError",
    "RuleEvaluationError",
    "evaluate_rule",
    "parse_rule",
]

#: Attributes a rule may inspect (OpenBot's documented list).
ATTRIBUTES = frozenset(
    {
        "tool.name",
        "intent",
        "bot.id",
        "actor.id",
        "page.url",
        "page.host",
        "element.ref",
        "element.role",
        "element.name",
        "element.type",
        "key",
        "command",
        "file.path",
        "file.name",
        "file.extension",
        "mcp.server",
        "mcp.tool",
        "mcp.effect",
        "initiator.kind",
        "initiator.id",
    }
)

#: Maximum rule source length (keeps a policy file readable).
MAX_RULE_LENGTH = 2000


class PolicyError(ValueError):
    """A policy is malformed. Raised at load time so a bad policy
    halts startup instead of silently running."""


class RuleEvaluationError(Exception):
    """One rule failed to evaluate against one action context."""


# ---------------------------------------------------------------------------
# Rule language: tokenizer + recursive-descent parser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
    | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
    | (?P<op>==|!=)
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<comma>,)
    | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
    | (?P<bad>.)
    """,
    re.VERBOSE,
)

_KEYWORDS = {"and": "AND", "or": "OR", "not": "NOT", "true": "TRUE", "false": "FALSE"}


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(source):
        match = _TOKEN_RE.match(source, pos)
        if match is None:  # pragma: no cover - regex always matches
            raise PolicyError(f"cannot tokenize rule at {pos}: {source!r}")
        pos = match.end()
        kind = (match.lastgroup or "bad").upper()
        if kind == "WS":
            continue
        if kind == "BAD":
            raise PolicyError(f"unexpected character in rule: {match.group()!r}")
        value = match.group()
        if kind == "IDENT":
            lowered = value.lower()
            if lowered in _KEYWORDS:
                kind = _KEYWORDS[lowered]
                value = lowered
        tokens.append(_Token(kind, value))
    tokens.append(_Token("EOF", ""))
    return tokens


def _unescape_string(token: str) -> str:
    quote = token[0]
    body = token[1:-1]
    return body.replace("\\" + quote, quote).replace("\\\\", "\\")


class _Parser:
    """Recursive-descent parser. Grammar::

    expr    := or_expr
    or_expr := and_expr ("or" and_expr)*
    and_expr:= not_expr ("and" not_expr)*
    not_expr:= "not" not_expr | primary
    primary := "(" expr ")" | func | comparison | literal
    func    := "contains" "(" attr "," string ")"
             | "matches" "(" attr "," string ")"
    comparison := attr ("==" | "!=") string
    literal := "true" | "false"
    attr    := dotted name from ATTRIBUTES
    """

    def __init__(self, tokens: list[_Token], source: str) -> None:
        self._tokens = tokens
        self._pos = 0
        self._source = source

    def _peek(self) -> _Token:
        return self._tokens[self._pos]

    def _next(self) -> _Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _expect(self, kind: str) -> _Token:
        token = self._next()
        if token.kind != kind:
            raise PolicyError(f"expected {kind} in rule {self._source!r}, got {token.value!r}")
        return token

    def parse(self):
        node = self._parse_or()
        if self._peek().kind != "EOF":
            raise PolicyError(f"trailing tokens in rule {self._source!r}: {self._peek().value!r}")
        return node

    def _parse_or(self):
        node = self._parse_and()
        while self._peek().kind == "OR":
            self._next()
            node = ("or", node, self._parse_and())
        return node

    def _parse_and(self):
        node = self._parse_not()
        while self._peek().kind == "AND":
            self._next()
            node = ("and", node, self._parse_not())
        return node

    def _parse_not(self):
        if self._peek().kind == "NOT":
            self._next()
            return ("not", self._parse_not())
        return self._parse_primary()

    def _parse_attr(self) -> str:
        token = self._expect("IDENT")
        if token.value not in ATTRIBUTES:
            raise PolicyError(
                f"unknown attribute {token.value!r} in rule {self._source!r}; "
                f"allowed: {sorted(ATTRIBUTES)}"
            )
        return token.value

    def _parse_string(self) -> str:
        token = self._expect("STRING")
        return _unescape_string(token.value)

    def _parse_primary(self):
        token = self._peek()
        if token.kind == "LPAREN":
            self._next()
            node = self._parse_or()
            self._expect("RPAREN")
            return node
        if token.kind in ("TRUE", "FALSE"):
            self._next()
            return ("lit", token.kind == "TRUE")
        if token.kind == "IDENT" and token.value in ("contains", "matches"):
            func = self._next().value
            self._expect("LPAREN")
            attr = self._parse_attr()
            self._expect("COMMA")
            value = self._parse_string()
            self._expect("RPAREN")
            if func == "matches":
                try:
                    re.compile(value, re.IGNORECASE)
                except re.error as exc:
                    raise PolicyError(f"invalid regex in rule {self._source!r}: {exc}") from exc
            return (func, attr, value)
        if token.kind == "IDENT":
            attr = self._parse_attr()
            op = self._next()
            if op.kind != "OP":
                raise PolicyError(f"expected == or != after {attr!r} in rule {self._source!r}")
            value = self._parse_string()
            return ("cmp", attr, op.value, value)
        raise PolicyError(f"unexpected token {token.value!r} in rule {self._source!r}")


def parse_rule(source: str):
    """Parse one rule source into an AST. Raises PolicyError."""
    if not isinstance(source, str) or not source.strip():
        raise PolicyError("rule must be a non-empty string")
    if len(source) > MAX_RULE_LENGTH:
        raise PolicyError(f"rule over {MAX_RULE_LENGTH} characters")
    return _Parser(_tokenize(source), source).parse()


def _resolve_attr(context: dict[str, Any], attr: str) -> str:
    node: Any = context
    for part in attr.split("."):
        if not isinstance(node, dict) or part not in node:
            raise RuleEvaluationError(f"attribute {attr!r} is missing from the action context")
        node = node[part]
    if not isinstance(node, str):
        raise RuleEvaluationError(f"attribute {attr!r} is not a string")
    return node


def _evaluate(node, context: dict[str, Any]) -> bool:
    kind = node[0]
    if kind == "lit":
        return bool(node[1])
    if kind == "and":
        return _evaluate(node[1], context) and _evaluate(node[2], context)
    if kind == "or":
        return _evaluate(node[1], context) or _evaluate(node[2], context)
    if kind == "not":
        return not _evaluate(node[1], context)
    if kind == "cmp":
        _, attr, op, value = node
        actual = _resolve_attr(context, attr)
        return actual == value if op == "==" else actual != value
    if kind == "contains":
        _, attr, value = node
        return value.lower() in _resolve_attr(context, attr).lower()
    if kind == "matches":
        _, attr, value = node
        return re.search(value, _resolve_attr(context, attr), re.IGNORECASE) is not None
    raise RuleEvaluationError(f"unknown AST node: {kind!r}")  # pragma: no cover


def evaluate_rule(rule, context) -> bool:
    """Evaluate a parsed rule (or a rule source string) against an
    action context: a nested dict, or any object with a ``to_dict()``
    method. Raises RuleEvaluationError on a missing attribute;
    raises PolicyError on a bad source."""
    if isinstance(rule, str):
        rule = parse_rule(rule)
    if hasattr(context, "to_dict"):
        context = context.to_dict()
    return _evaluate(rule, context)


@dataclass(frozen=True)
class CompiledRule:
    """One rule, parsed at load time with its source kept for audit."""

    source: str
    ast: object
