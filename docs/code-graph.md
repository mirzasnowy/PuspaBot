# Code graph (G29)

The code graph answers questions about Puspa's own Python code
from a knowledge graph, without re-reading the files: which
functions call a function, what a function reaches in two hops,
what a file defines and imports. The pattern follows
Graphify (build a knowledge graph from a codebase and query
the graph), rewritten as Puspa's own code — no Graphify code
is copied, vendored, or depended on. Graphify is Apache-2.0;
see `REUSE.md` / `NOTICE.md` (G29) for the attribution.

## What is extracted

`puspa/retrieval/code_graph.py`, `CodeExtractor` (an
`EntityExtractor` over `ast`). One Python file yields:

| Entity kind | Name | Example |
| --- | --- | --- |
| `file` | the file path | `billing.py` |
| `function` | qualified name | `billing.charge`, `orders.Order.pay`, `billing.outer.inner` |
| `class` | qualified name | `orders.Order` |
| `module` | imported module | `os`, `helpers.validate` |
| `table` | SQL table in a string literal | `invoices` |
| `config` | key read via `os.getenv` / `os.environ` | `billing_key` |

| Relation | Meaning | Example |
| --- | --- | --- |
| `defines` | file defines a function/class; class defines a method | `billing.py` defines `billing.charge` |
| `imports` | file imports a module | `billing.py` imports `helpers.validate` |
| `calls` | caller calls callee | `billing.charge` calls `billing.record` |
| `inherits` | class inherits a base class | `orders.SpecialOrder` inherits `orders.Order` |
| `reads_table` | a `FROM` / `JOIN` table in a string literal | `db.py` reads `invoices` |
| `writes_table` | an `INTO` / `UPDATE` / `DELETE FROM` table | `db.py` writes `receipts` |
| `reads_config` | a config key read through `os.getenv` / `os.environ` | `db.load` reads `billing_key` |

Call resolution is done in two passes: a definition pass
collects every function, class and import alias first, so a
call resolves even when its target is defined later in the
file; the second pass records the edges. Calls resolve inside
the module (including nested functions), through `self` for
methods, through class names, and through imports
(`from helpers import validate` then `validate(...)` becomes
an edge to `helpers.validate`).

The entities and edges live in the shared `KnowledgeGraph`
(`puspa/retrieval/knowledge_graph.py`), not in a second
parallel graph: a code file's nodes and edges sit next to the
prose nodes and edges, keyed by file path as their source id,
so the HippoRAG retriever and the graph query modes see the
same index.

## Querying

Module functions in `puspa/retrieval/code_graph.py` (also
methods on `CodeGraph`, same names), all running on the
graph's edges:

```python
from puspa.retrieval.code_graph import (
    CodeGraph, call_path, callees_of, callers_of,
    reachable_from, reaches,
)

code = CodeGraph()
code.add_file("billing.py", source)

callers_of(code.graph, "billing.record")       # ["billing.charge"]
callees_of(code.graph, "billing.charge")       # ["billing.record", "helpers.validate"]
reachable_from(code.graph, "billing.charge")   # {"billing.record": 1, "helpers.validate": 1, "billing.store": 2}
reaches(code.graph, "billing.charge", "billing.store")  # True
call_path(code.graph, "billing.charge", "billing.store")
# ["billing.charge", "billing.record", "billing.store"]
```

`reachable_from` is a breadth-first walk over the directed
`calls` edges, so the count is the shortest call distance;
`max_hops` caps the walk. `call_path` returns the shortest
path, or `None` when the target is unreachable.

Names may be qualified (`billing.charge`) or bare
(`charge`): a bare name resolves when exactly one node in
the graph ends with `.charge`. An ambiguous bare name raises
`ValueError` naming the candidates instead of guessing; an
unknown name returns an empty result (`[]`, `{}`, `False`,
`None`), never an invented node.

`defined_in(graph, path)` lists what a file defines and
`imports_of(graph, path)` lists what it imports, from the
`defines` / `imports` edges.

## Indexing files (incremental)

```python
code = CodeGraph()                    # fresh shared graph
code = CodeGraph(existing_graph)      # index into a live graph

code.add_file("billing.py", source)       # index one file
code.update_file("billing.py", new_source)  # replace in place
code.remove_file("billing.py")            # detach one file
```

`CodeGraph` stores the extraction indexed for each file, so
an update detaches exactly what the old file added and merges
the new one: entities and edges that disappear from the file
are removed when no other file still supports them, shared
nodes survive, and nothing is duplicated. Adding the same
file with unchanged text is a no-op (`already_indexed`).
Each call returns counts (`added_files`, `new_nodes`,
`merged_nodes`, `new_edges`, `merged_edges`,
`removed_nodes`, `removed_edges`, `nodes`, `edges`,
`files`).

`CodeGraph` uses its own `CodeExtractor` by default even
when it wraps a graph whose prose extractor is different;
pass `extractor=` to override it.

## Wiring the LLM triple extractor (live)

`JsonTripleExtractor` in `puspa/retrieval/knowledge_graph.py`
now defaults to the router's live completion: with no
argument, or with the string `"default"`, it resolves
through `puspa.router.complete.resolve_llm_complete` to
`default_llm_complete`, the `complete(prompt)` callable
built on `complete_messages` — the same entry point the
turn loop's `turn_api` calls. Building the callable makes
no network call and needs no key; the key is resolved (and
a missing key reported) on the first extraction, never
faked. Extra keyword arguments (`model=`, `transport=`,
`key_resolver=`, ...) pass through to the live completion,
so tests bind a fake transport there:

```python
from puspa.retrieval.knowledge_graph import JsonTripleExtractor

extractor = JsonTripleExtractor()            # live, via the router
extractor = JsonTripleExtractor("default")   # same, explicit
extractor = JsonTripleExtractor(fake_complete)  # injected, tests
```

Passing anything else (a non-callable, a string other than
`"default"`) raises `ValueError`.

## Honest limits

- Only Python is parsed (`ast`). Any other language is out
  of scope; a file that does not parse raises `ValueError`
  instead of indexing a half-read file.
- Only calls the AST can resolve become `calls` edges.
  Dynamic calls are invisible: `getattr`, `eval`, a
  function passed as a value and called later, a call
  through an unknown object, a decorator call recorded
  against the enclosing scope only when its target
  resolves.
- Names are normalized the way the shared graph normalizes
  them (lowercased, single spaces), so `Foo` and `foo`
  collide; qualified names keep their dots.
- Relative imports with no module (`from . import x`) are
  skipped: they have no dotted target to record.
- SQL table and config detection is a heuristic over
  string literals: a table name inside prose or a
  docstring is read as a table, and a table reached only
  through a dynamically built query string is missed.
- The LLM extractor path is live by default, but no live
  provider call is proven in the suite: tests use an
  injected fake callable or a fake transport, and a real
  extraction still needs a key from the BYOK path.
