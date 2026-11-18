"""Python code graph over the shared knowledge graph (Graphify pattern, rewritten).

Graphify builds a knowledge graph from a codebase: it parses
source files, turns functions, classes, imports and calls into
nodes and edges, and answers questions from the graph instead
of re-reading the files. Only that pattern is followed here,
rewritten as Puspa's own code — no Graphify code is copied,
vendored, or depended on. Graphify is Apache-2.0; see
``REUSE.md`` / ``NOTICE.md`` (G29) for the attribution.

The graph is the one ``KnowledgeGraph`` the retrieval pipeline
already uses, not a second parallel graph: code entities and
edges are nodes and triples in it, with the file path as their
source id, so the HippoRAG retriever, the incremental updater
and the graph query modes all see the same index.

What is extracted from one Python file (``CodeExtractor``, an
``EntityExtractor`` over ``ast``):

- the file itself (kind ``"file"``, named by its path);
- every function and method (kind ``"function"``, qualified as
  ``module.name``, ``module.Class.method`` or
  ``module.outer.inner``) and every class (kind ``"class"``);
- every module it imports (kind ``"module"``);
- edges: ``defines`` (file to its functions/classes, class to
  its methods), ``imports`` (file to module), ``calls``
  (caller to callee, resolved inside the module, through
  ``self`` for methods, and through imports), ``inherits``
  (class to base class);
- when they appear in string literals or config reads: SQL
  tables (kind ``"table"``, ``reads_table`` /
  ``writes_table``) and config keys read through ``os.getenv``
  / ``os.environ`` (kind ``"config"``, ``reads_config``).

``CodeGraph`` indexes files into the shared graph with an
incremental update: ``add_file`` / ``update_file`` /
``remove_file``. Updating a file detaches exactly the old
extraction (stored at index time) and merges the new one, so
no stale entity or edge survives and nothing is duplicated;
a file whose text did not change is a no-op.

Queries run on the graph, never on the files:
``callers_of`` / ``callees_of`` (one hop), ``reachable_from``
(BFS over ``calls`` edges, with hop counts), ``reaches`` and
``call_path`` (shortest call path). A bare name (``"charge"``)
resolves to its unique qualified name in the graph; an
ambiguous bare name raises ``ValueError`` instead of guessing.

Honest limits, stated here and not hidden: only Python is
parsed (``ast`` — any other language is out of scope); only
calls the AST can resolve become edges, so dynamic calls
(``getattr``, ``eval``, a function passed as a value and called
later, a call through an unknown object) are invisible; names
are normalized the way the shared graph normalizes them
(lowercased), so ``Foo`` and ``foo`` collide; relative imports
with no module (``from . import x``) are skipped; SQL table
and config detection is a heuristic over string literals, so
a table name inside prose or a docstring is read as a table.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from .knowledge_graph import (
    Entity,
    EntityExtractor,
    Extraction,
    KnowledgeGraph,
    Triple,
    normalize_entity_name,
)

__all__ = [
    "REL_CALLS",
    "REL_DEFINES",
    "REL_IMPORTS",
    "REL_INHERITS",
    "REL_READS_CONFIG",
    "REL_READS_TABLE",
    "REL_WRITES_TABLE",
    "CodeExtractor",
    "CodeGraph",
    "call_path",
    "callees_of",
    "callers_of",
    "defined_in",
    "imports_of",
    "module_name_for_path",
    "reachable_from",
    "reaches",
    "resolve_code_name",
]

#: Relations the code extractor writes into the shared graph.
REL_DEFINES = "defines"
REL_IMPORTS = "imports"
REL_CALLS = "calls"
REL_INHERITS = "inherits"
REL_READS_TABLE = "reads_table"
REL_WRITES_TABLE = "writes_table"
REL_READS_CONFIG = "reads_config"

#: SQL reads: FROM / JOIN followed by a table name.
_SQL_READ_RE = re.compile(r"\b(?:FROM|JOIN)\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
#: SQL writes: INTO / UPDATE / DELETE FROM followed by a table name.
_SQL_WRITE_RE = re.compile(
    r"\b(?:INTO|UPDATE|DELETE\s+FROM)\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)


def module_name_for_path(path: str) -> str:
    """Dotted module name for a file path: ``src/billing.py`` to ``src.billing``."""
    if not isinstance(path, str) or path == "":
        raise ValueError("path must be a non-empty string")
    cleaned = path.strip().lstrip("./").replace("\\", "/")
    cleaned = cleaned.removesuffix(".py")
    return cleaned.replace("/", ".")


class _DefinitionCollector(ast.NodeVisitor):
    """First pass over a module: every defined function and class
    (qualified) and every import alias, before any call is
    resolved. Calls may target a function defined later in the
    file, so definitions are collected up front instead of
    discovered while walking bodies.
    """

    def __init__(self, *, module: str, file_path: str):
        self.module = module
        self.file_path = file_path
        self._scope: list[tuple[str, str]] = []
        self.functions: set[str] = set()
        self.classes: set[str] = set()
        self.aliases: dict[str, str] = {}

    def _current_scope(self) -> str:
        return self._scope[-1][1] if self._scope else self.file_path

    def _qualified(self, name: str) -> str:
        if self._scope:
            return f"{self._current_scope()}.{name}"
        return f"{self.module}.{name}"

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.aliases[local] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not node.module:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = self._qualified(node.name)
        self.functions.add(qualified)
        self._scope.append(("function", qualified))
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = self._qualified(node.name)
        self.classes.add(qualified)
        self._scope.append(("class", qualified))
        self.generic_visit(node)
        self._scope.pop()


class _CodeVisitor(ast.NodeVisitor):
    """Walk one module's AST and collect code entities and triples.

    The visitor keeps a scope stack of (kind, qualified name)
    for the enclosing functions and classes, an alias map from
    imported names to their qualified targets, and the sets of
    functions and classes defined in this module, so a call can
    be resolved to the qualified name it really targets.
    """

    def __init__(self, *, module: str, file_path: str, source_id: str, definitions=None):
        self.module = module
        self.file_path = file_path
        self.source_id = source_id
        self.entities: dict[str, Entity] = {}
        self.triples: list[Triple] = []
        self._scope: list[tuple[str, str]] = []
        # Definitions come from the pre-pass, so a call resolves
        # even when its target is defined later in the file.
        self._aliases: dict[str, str] = dict(definitions.aliases) if definitions else {}
        self._functions: set[str] = set(definitions.functions) if definitions else set()
        self._classes: set[str] = set(definitions.classes) if definitions else set()

    # -- helpers ---------------------------------------------------

    def _add_entity(self, name: str, kind: str | None) -> None:
        normalized = normalize_entity_name(name)
        existing = self.entities.get(normalized)
        if existing is None:
            self.entities[normalized] = Entity(
                name=normalized, kind=kind, source_ids={self.source_id}
            )
        elif existing.kind is None and kind:
            existing.kind = kind

    def _add_triple(self, subject: str, relation: str, obj: str) -> None:
        self.triples.append(
            Triple(
                subject=normalize_entity_name(subject),
                relation=relation,
                object=normalize_entity_name(obj),
                source_ids={self.source_id},
            )
        )

    def _current_scope(self) -> str:
        return self._scope[-1][1] if self._scope else self.file_path

    def _enclosing_class(self) -> str | None:
        for kind, qualified in reversed(self._scope):
            if kind == "class":
                return qualified
        return None

    def _resolve_bare(self, name: str) -> str | None:
        """A bare name to the qualified function it defines, if any.

        Walks the scope stack from the innermost scope out, so a
        nested function is found from inside its enclosing
        function, then falls back to the module level.
        """
        for _, qualified in reversed(self._scope):
            candidate = f"{qualified}.{name}"
            if candidate in self._functions:
                return candidate
        candidate = f"{self.module}.{name}"
        if candidate in self._functions:
            return candidate
        return None

    def _resolve_class(self, name: str) -> str | None:
        """A bare name to the qualified class it defines, if any."""
        for _, qualified in reversed(self._scope):
            candidate = f"{qualified}.{name}"
            if candidate in self._classes:
                return candidate
        candidate = f"{self.module}.{name}"
        if candidate in self._classes:
            return candidate
        return None

    def _dotted_name(self, node: ast.AST) -> str | None:
        """Dotted name for a Name / Attribute chain, else None."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._dotted_name(node.value)
            if base is None:
                return None
            return f"{base}.{node.attr}"
        return None

    def _resolve_callee(self, func: ast.AST) -> tuple[str, str | None]:
        """A call target to (qualified name, kind), or ("", None)
        when the AST cannot resolve it (a builtin, a call through
        an unknown object, a dynamic call). Unresolvable calls
        are skipped, never guessed.
        """
        if isinstance(func, ast.Name):
            qualified = self._resolve_bare(func.id)
            if qualified is not None:
                return qualified, "function"
            target = self._aliases.get(func.id)
            if target is not None:
                kind = "class" if target in self._classes else "function"
                return target, kind
            return "", None
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id == "self":
                enclosing = self._enclosing_class()
                if enclosing is not None:
                    return f"{enclosing}.{func.attr}", "function"
                return "", None
            if isinstance(func.value, ast.Name):
                cls = self._resolve_class(func.value.id)
                if cls is not None:
                    return f"{cls}.{func.attr}", "function"
                target = self._aliases.get(func.value.id)
                if target is not None:
                    return f"{target}.{func.attr}", "function"
                return "", None
            dotted = self._dotted_name(func)
            if dotted is None:
                return "", None
            first, _, _ = dotted.partition(".")
            target = self._aliases.get(first)
            if target is None:
                return "", None
            return target + dotted[len(first) :], "function"
        return "", None

    # -- definitions ------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            target = alias.name
            self._add_entity(target, "module")
            self._add_triple(self.file_path, REL_IMPORTS, target)
            local = alias.asname or target.split(".")[0]
            self._aliases[local] = target

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # A relative import with no module ("from . import x")
        # has no dotted target to record; it is skipped.
        if not node.module:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            target = f"{node.module}.{alias.name}"
            self._add_entity(target, "module")
            self._add_triple(self.file_path, REL_IMPORTS, target)
            self._aliases[alias.asname or alias.name] = target

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent = self._current_scope()
        if self._scope:
            qualified = f"{self._current_scope()}.{node.name}"
        else:
            qualified = f"{self.module}.{node.name}"
        self._functions.add(qualified)
        self._add_entity(qualified, "function")
        self._add_triple(parent, REL_DEFINES, qualified)
        # Decorators and defaults run at definition time, in the
        # enclosing scope — not inside the function.
        for child in list(node.decorator_list) + list(node.args.defaults):
            self.visit(child)
        for default in node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)
        self._scope.append(("function", qualified))
        for statement in node.body:
            self.visit(statement)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent = self._current_scope()
        if self._scope:
            qualified = f"{self._current_scope()}.{node.name}"
        else:
            qualified = f"{self.module}.{node.name}"
        self._classes.add(qualified)
        self._add_entity(qualified, "class")
        self._add_triple(parent, REL_DEFINES, qualified)
        for base in node.bases:
            base_name = self._dotted_name(base)
            if base_name is None:
                continue
            resolved = self._resolve_class(base_name) or self._aliases.get(base_name) or base_name
            self._add_entity(resolved, "class")
            self._add_triple(qualified, REL_INHERITS, resolved)
        for child in list(node.decorator_list):
            self.visit(child)
        self._scope.append(("class", qualified))
        for statement in node.body:
            self.visit(statement)
        self._scope.pop()

    # -- calls, config, SQL -----------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        config_key = self._config_key(node)
        if config_key is not None:
            self._add_entity(config_key, "config")
            self._add_triple(self._current_scope(), REL_READS_CONFIG, config_key)
        else:
            qualified, kind = self._resolve_callee(node.func)
            if qualified:
                self._add_entity(qualified, kind)
                self._add_triple(self._current_scope(), REL_CALLS, qualified)
        self.generic_visit(node)

    def _config_key(self, node: ast.Call) -> str | None:
        """Config key for os.getenv / os.environ.get calls, else None."""
        func = node.func
        if not isinstance(func, ast.Attribute):
            return None
        if func.attr == "getenv" and isinstance(func.value, ast.Name):
            if self._aliases.get(func.value.id) != "os":
                return None
        elif func.attr == "get" and isinstance(func.value, ast.Attribute):
            if func.value.attr != "environ" or not isinstance(func.value.value, ast.Name):
                return None
            if self._aliases.get(func.value.value.id) != "os":
                return None
        else:
            return None
        if not node.args or not isinstance(node.args[0], ast.Constant):
            return None
        key = node.args[0].value
        return str(key) if isinstance(key, str) and key else None

    def visit_Subscript(self, node: ast.Subscript) -> None:
        value = node.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "environ"
            and isinstance(value.value, ast.Name)
            and self._aliases.get(value.value.id) == "os"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and node.slice.value
        ):
            key = node.slice.value
            self._add_entity(key, "config")
            self._add_triple(self._current_scope(), REL_READS_CONFIG, key)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str):
            return
        text = node.value
        written = {name.lower() for name in _SQL_WRITE_RE.findall(text)}
        for name in written:
            self._add_entity(name, "table")
            self._add_triple(self._current_scope(), REL_WRITES_TABLE, name)
        for name in _SQL_READ_RE.findall(text):
            if name.lower() in written:
                continue
            self._add_entity(name, "table")
            self._add_triple(self._current_scope(), REL_READS_TABLE, name)


class CodeExtractor(EntityExtractor):
    """AST extractor: Python source to code entities and triples.

    ``extract(source, source_id)`` parses the source with
    ``ast``; ``source_id`` is the file path, which is also the
    file entity's name and the source id of every node and edge
    the extraction adds. A file that does not parse raises
    ``ValueError`` instead of indexing a half-read file.
    """

    def extract(self, source: str, source_id: str) -> Extraction:
        if not isinstance(source, str) or source == "":
            raise ValueError("source must be a non-empty string")
        if not isinstance(source_id, str) or source_id == "":
            raise ValueError("source_id must be a non-empty string")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError(f"source is not valid Python: {exc}") from exc
        module = module_name_for_path(source_id)
        definitions = _DefinitionCollector(module=module, file_path=source_id)
        definitions.visit(tree)
        visitor = _CodeVisitor(
            module=module, file_path=source_id, source_id=source_id, definitions=definitions
        )
        visitor._add_entity(source_id, "file")
        visitor.visit(tree)
        seen: set[tuple[str, str, str]] = set()
        triples: list[Triple] = []
        for triple in visitor.triples:
            key = (triple.subject, triple.relation, triple.object)
            if key in seen:
                continue
            seen.add(key)
            triples.append(triple)
        return Extraction(entities=list(visitor.entities.values()), triples=triples)


# -- queries over the shared graph -----------------------------------


def resolve_code_name(graph: KnowledgeGraph, name: str) -> str | None:
    """A code name to its qualified node name in the graph.

    Exact match wins; otherwise a bare name (``"charge"``)
    resolves when exactly one node ends with ``".charge"``.
    An ambiguous bare name raises ``ValueError`` naming the
    candidates instead of guessing; no match returns None.
    """
    normalized = normalize_entity_name(name)
    if normalized in graph.nodes:
        return normalized
    candidates = sorted(n for n in graph.nodes if n.endswith("." + normalized))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError(f"ambiguous code name {name!r}: {', '.join(candidates)}")
    return None


def _directed_call_edges(graph: KnowledgeGraph) -> dict[str, set[str]]:
    """Caller to callees, from the graph's ``calls`` edges only."""
    adjacency: dict[str, set[str]] = {}
    for subject, relation, obj in graph.edges:
        if relation == REL_CALLS:
            adjacency.setdefault(subject, set()).add(obj)
    return adjacency


def callers_of(graph: KnowledgeGraph, name: str) -> list[str]:
    """Functions that call ``name`` (one hop, from the graph)."""
    resolved = resolve_code_name(graph, name)
    if resolved is None:
        return []
    return sorted(
        subject
        for subject, relation, obj in graph.edges
        if relation == REL_CALLS and obj == resolved
    )


def callees_of(graph: KnowledgeGraph, name: str) -> list[str]:
    """Functions ``name`` calls (one hop, from the graph)."""
    resolved = resolve_code_name(graph, name)
    if resolved is None:
        return []
    return sorted(
        obj
        for subject, relation, obj in graph.edges
        if relation == REL_CALLS and subject == resolved
    )


def defined_in(graph: KnowledgeGraph, file_path: str) -> list[str]:
    """Functions and classes a file defines (its ``defines`` edges)."""
    name = normalize_entity_name(file_path)
    return sorted(
        obj for subject, relation, obj in graph.edges if relation == REL_DEFINES and subject == name
    )


def imports_of(graph: KnowledgeGraph, file_path: str) -> list[str]:
    """Modules a file imports (its ``imports`` edges)."""
    name = normalize_entity_name(file_path)
    return sorted(
        obj for subject, relation, obj in graph.edges if relation == REL_IMPORTS and subject == name
    )


def reachable_from(graph: KnowledgeGraph, name: str, max_hops: int | None = None) -> dict[str, int]:
    """Everything ``name`` reaches through calls, with hop counts.

    Breadth-first over the directed ``calls`` edges, so the
    count is the shortest call distance. ``name`` itself is not
    in the result. ``max_hops`` caps the walk; None walks the
    whole reachable set. An unknown name reaches nothing.
    """
    if max_hops is not None and max_hops < 1:
        raise ValueError("max_hops must be at least 1")
    resolved = resolve_code_name(graph, name)
    if resolved is None:
        return {}
    adjacency = _directed_call_edges(graph)
    distances: dict[str, int] = {}
    frontier = [resolved]
    hops = 0
    seen = {resolved}
    while frontier and (max_hops is None or hops < max_hops):
        hops += 1
        nxt: list[str] = []
        for node in frontier:
            for neighbor in sorted(adjacency.get(node, set())):
                if neighbor not in seen:
                    seen.add(neighbor)
                    distances[neighbor] = hops
                    nxt.append(neighbor)
        frontier = nxt
    return distances


def reaches(graph: KnowledgeGraph, source: str, target: str, max_hops: int | None = None) -> bool:
    """Whether ``source`` reaches ``target`` through calls."""
    resolved_target = resolve_code_name(graph, target)
    if resolved_target is None:
        return False
    return resolved_target in reachable_from(graph, source, max_hops=max_hops)


def call_path(
    graph: KnowledgeGraph, source: str, target: str, max_hops: int | None = None
) -> list[str] | None:
    """Shortest call path from ``source`` to ``target``, or None.

    The path is a list of qualified names, ``source`` first and
    ``target`` last. A source that is the target returns the
    one-element path; an unreachable target returns None.
    """
    if max_hops is not None and max_hops < 1:
        raise ValueError("max_hops must be at least 1")
    resolved_source = resolve_code_name(graph, source)
    resolved_target = resolve_code_name(graph, target)
    if resolved_source is None or resolved_target is None:
        return None
    if resolved_source == resolved_target:
        return [resolved_source]
    adjacency = _directed_call_edges(graph)
    parents: dict[str, str | None] = {resolved_source: None}
    frontier = [resolved_source]
    hops = 0
    while frontier and (max_hops is None or hops < max_hops):
        hops += 1
        nxt: list[str] = []
        for node in frontier:
            for neighbor in sorted(adjacency.get(node, set())):
                if neighbor in parents:
                    continue
                parents[neighbor] = node
                if neighbor == resolved_target:
                    path = [neighbor]
                    while parents[path[-1]] is not None:
                        path.append(parents[path[-1]])  # type: ignore[arg-type]
                    return list(reversed(path))
                nxt.append(neighbor)
        frontier = nxt
    return None


# -- incremental file indexing ---------------------------------------


@dataclass
class _FileStats:
    """Counts for one file add / update / remove, as a dict."""

    added_files: int = 0
    already_indexed: bool = False
    duplicate_files: int = 0
    new_nodes: int = 0
    merged_nodes: int = 0
    new_edges: int = 0
    merged_edges: int = 0
    removed_files: int = 0
    removed_nodes: int = 0
    removed_edges: int = 0
    nodes: int = 0
    edges: int = 0
    files: int = 0

    def as_dict(self) -> dict:
        return {
            "added_files": self.added_files,
            "already_indexed": self.already_indexed,
            "duplicate_files": self.duplicate_files,
            "new_nodes": self.new_nodes,
            "merged_nodes": self.merged_nodes,
            "new_edges": self.new_edges,
            "merged_edges": self.merged_edges,
            "removed_files": self.removed_files,
            "removed_nodes": self.removed_nodes,
            "removed_edges": self.removed_edges,
            "nodes": self.nodes,
            "edges": self.edges,
            "files": self.files,
        }


class CodeGraph:
    """Incremental Python code graph over a shared ``KnowledgeGraph``.

    ``graph`` is the index (a fresh graph when omitted); the
    code entities and edges live in it alongside any prose
    entities, keyed by file path as their source id. The
    extraction indexed for each file is stored here, so
    ``update_file`` detaches exactly what the old file added
    and ``remove_file`` drops exactly what the file owned:
    nodes and edges still supported by another file survive.
    A file added twice with unchanged text is a no-op.
    """

    def __init__(self, graph: KnowledgeGraph | None = None, extractor=None):
        # The code extractor is this class's own: the shared
        # graph may keep its prose extractor for documents.
        self.extractor = extractor or CodeExtractor()
        self.graph = graph if graph is not None else KnowledgeGraph(extractor=self.extractor)
        self._file_extraction: dict[str, Extraction] = {}
        self._file_source: dict[str, str] = {}

    # -- queries (delegates to the module functions) -----------------

    def callers_of(self, name: str) -> list[str]:
        return callers_of(self.graph, name)

    def callees_of(self, name: str) -> list[str]:
        return callees_of(self.graph, name)

    def defined_in(self, file_path: str) -> list[str]:
        return defined_in(self.graph, file_path)

    def imports_of(self, file_path: str) -> list[str]:
        return imports_of(self.graph, file_path)

    def reachable_from(self, name: str, max_hops: int | None = None) -> dict[str, int]:
        return reachable_from(self.graph, name, max_hops=max_hops)

    def reaches(self, source: str, target: str, max_hops: int | None = None) -> bool:
        return reaches(self.graph, source, target, max_hops=max_hops)

    def call_path(self, source: str, target: str, max_hops: int | None = None) -> list[str] | None:
        return call_path(self.graph, source, target, max_hops=max_hops)

    # -- indexing ----------------------------------------------------

    def add_file(self, path: str, source: str) -> dict:
        """Index one Python file. Unchanged text is a no-op;
        changed text replaces the file in place."""
        if not isinstance(path, str) or path == "":
            raise ValueError("path must be a non-empty string")
        if path in self._file_source:
            if self._file_source[path] == source:
                stats = _FileStats(already_indexed=True, duplicate_files=1)
                return self._finish(stats)
            return self.update_file(path, source)
        return self._finish(self._merge(path, source))

    def update_file(self, path: str, source: str) -> dict:
        """Replace one file in place: detach the old extraction,
        then merge the new one. An unindexed file is added; the
        same text is a no-op."""
        if not isinstance(path, str) or path == "":
            raise ValueError("path must be a non-empty string")
        if path not in self._file_source:
            return self.add_file(path, source)
        if self._file_source[path] == source:
            stats = _FileStats(already_indexed=True, duplicate_files=1)
            return self._finish(stats)
        removed = self._detach(path)
        added = self._merge(path, source)
        for key in ("new_nodes", "merged_nodes", "new_edges", "merged_edges"):
            setattr(removed, key, getattr(removed, key) + getattr(added, key))
        return self._finish(removed)

    def remove_file(self, path: str) -> dict:
        """Detach one file. Nodes and edges that lose their last
        supporting file go away; anything another file still
        supports stays."""
        if path not in self._file_source:
            raise KeyError(f"unknown file: {path!r}")
        return self._finish(self._detach(path))

    # -- internals ---------------------------------------------------

    def _finish(self, stats: _FileStats) -> dict:
        stats.nodes = self.graph.node_count
        stats.edges = self.graph.edge_count
        stats.files = len(self._file_source)
        return stats.as_dict()

    def _merge(self, path: str, source: str) -> _FileStats:
        """Extract one new file and merge it into the graph."""
        stats = _FileStats()
        extraction = self.extractor.extract(source, path)
        self._file_extraction[path] = extraction
        self._file_source[path] = source
        for entity in extraction.entities:
            if entity.name not in self.graph.nodes:
                self.graph.nodes[entity.name] = Entity(
                    name=entity.name, kind=entity.kind, source_ids=set(entity.source_ids)
                )
                stats.new_nodes += 1
            else:
                node = self.graph.nodes[entity.name]
                node.source_ids |= entity.source_ids
                if node.kind is None and entity.kind:
                    node.kind = entity.kind
                stats.merged_nodes += 1
        for triple in extraction.triples:
            key = (triple.subject, triple.relation, triple.object)
            if key not in self.graph.edges:
                self.graph.edges[key] = Triple(
                    subject=triple.subject,
                    relation=triple.relation,
                    object=triple.object,
                    source_ids=set(triple.source_ids),
                )
                stats.new_edges += 1
            else:
                self.graph.edges[key].source_ids |= triple.source_ids
                stats.merged_edges += 1
        stats.added_files = 1
        return stats

    def _detach(self, path: str) -> _FileStats:
        """Drop one file's contributions from the graph."""
        stats = _FileStats()
        extraction = self._file_extraction.pop(path)
        self._file_source.pop(path)
        names: set[str] = set()
        for entity in extraction.entities:
            names.add(entity.name)
        for triple in extraction.triples:
            names.add(triple.subject)
            names.add(triple.object)
        for name in names:
            node = self.graph.nodes.get(name)
            if node is None:
                continue
            node.source_ids.discard(path)
            if not node.source_ids:
                del self.graph.nodes[name]
                stats.removed_nodes += 1
        for triple in extraction.triples:
            key = (triple.subject, triple.relation, triple.object)
            edge = self.graph.edges.get(key)
            if edge is None:
                continue
            edge.source_ids.discard(path)
            if not edge.source_ids:
                del self.graph.edges[key]
                stats.removed_edges += 1
        stats.removed_files = 1
        return stats
