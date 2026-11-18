"""Tests for the Python code graph (G29).

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests.)
"""

import httpx
import pytest

from puspa.retrieval.code_graph import (
    CodeExtractor,
    CodeGraph,
    call_path,
    callees_of,
    callers_of,
    defined_in,
    imports_of,
    module_name_for_path,
    reachable_from,
    reaches,
    resolve_code_name,
)
from puspa.retrieval.knowledge_graph import JsonTripleExtractor, KnowledgeGraph

BILLING_V1 = """\
import os
from helpers import validate

def charge(amount):
    validate(amount)
    return record(amount)

def record(amount):
    store(amount)

def store(amount):
    pass
"""

BILLING_V2 = """\
import os
from helpers import validate

def charge(amount):
    validate(amount)
    return record(amount)

def record(amount):
    pass

def refund(amount):
    record(amount)
"""

ORDERS_V1 = """\
from billing import charge

class Order:
    def __init__(self, amount):
        self.amount = amount

    def pay(self):
        charge(self.amount)
        self.confirm()

    def confirm(self):
        pass
"""

DB_ACCESS = """\
import os

DB_QUERY = "SELECT id FROM invoices JOIN customers ON invoices.customer_id = customers.id"
WRITE_QUERY = "INSERT INTO receipts (id) VALUES (1)"

def load():
    key = os.getenv("BILLING_KEY")
    host = os.environ["BILLING_HOST"]
    return run(DB_QUERY)
"""


def test_module_name_for_path():
    assert module_name_for_path("billing.py") == "billing"
    assert module_name_for_path("src/billing.py") == "src.billing"
    assert module_name_for_path("./src/billing.py") == "src.billing"
    with pytest.raises(ValueError):
        module_name_for_path("")


def test_code_extractor_extracts_functions_classes_imports_calls():
    found = CodeExtractor().extract(BILLING_V1, "billing.py")
    names = {entity.name: entity for entity in found.entities}
    assert names["billing.py"].kind == "file"
    assert names["billing.charge"].kind == "function"
    assert names["billing.record"].kind == "function"
    assert names["billing.store"].kind == "function"
    assert names["os"].kind == "module"
    assert names["helpers.validate"].kind == "module"
    edges = {(t.subject, t.relation, t.object) for t in found.triples}
    assert ("billing.py", "defines", "billing.charge") in edges
    assert ("billing.py", "defines", "billing.record") in edges
    assert ("billing.py", "defines", "billing.store") in edges
    assert ("billing.py", "imports", "os") in edges
    assert ("billing.py", "imports", "helpers.validate") in edges
    assert ("billing.charge", "calls", "helpers.validate") in edges
    assert ("billing.charge", "calls", "billing.record") in edges
    assert ("billing.record", "calls", "billing.store") in edges


def test_code_extractor_extracts_class_methods_and_self_calls():
    found = CodeExtractor().extract(ORDERS_V1, "orders.py")
    names = {entity.name: entity for entity in found.entities}
    assert names["orders.order"].kind == "class"
    assert names["orders.order.pay"].kind == "function"
    assert names["orders.order.confirm"].kind == "function"
    assert names["orders.order.__init__"].kind == "function"
    edges = {(t.subject, t.relation, t.object) for t in found.triples}
    assert ("orders.py", "defines", "orders.order") in edges
    assert ("orders.order", "defines", "orders.order.pay") in edges
    assert ("orders.order", "defines", "orders.order.confirm") in edges
    assert ("orders.py", "imports", "billing.charge") in edges
    assert ("orders.order.pay", "calls", "billing.charge") in edges
    assert ("orders.order.pay", "calls", "orders.order.confirm") in edges
    # __init__ assigns, it calls nothing.
    assert not [t for t in found.triples if t.subject == "orders.order.__init__"]


def test_code_extractor_records_sql_tables_and_config():
    found = CodeExtractor().extract(DB_ACCESS, "db.py")
    names = {entity.name: entity for entity in found.entities}
    assert names["invoices"].kind == "table"
    assert names["customers"].kind == "table"
    assert names["receipts"].kind == "table"
    assert names["billing_key"].kind == "config"
    assert names["billing_host"].kind == "config"
    edges = {(t.subject, t.relation, t.object) for t in found.triples}
    assert ("db.py", "reads_table", "invoices") in edges
    assert ("db.py", "reads_table", "customers") in edges
    assert ("db.py", "writes_table", "receipts") in edges
    assert ("db.load", "reads_config", "billing_key") in edges
    assert ("db.load", "reads_config", "billing_host") in edges
    # run() is unresolvable from the AST: no guessed edge.
    assert not [t for t in found.triples if t.relation == "calls"]


def test_code_extractor_rejects_bad_input():
    extractor = CodeExtractor()
    with pytest.raises(ValueError):
        extractor.extract("", "bad.py")
    with pytest.raises(ValueError):
        extractor.extract("def broken(:\n", "bad.py")
    with pytest.raises(ValueError):
        extractor.extract("x = 1\n", "")


def test_code_graph_answers_callers_without_rereading_files():
    code = CodeGraph()
    code.add_file("billing.py", BILLING_V1)
    code.add_file("orders.py", ORDERS_V1)
    # The answers come from the graph's edges, not the files.
    assert callers_of(code.graph, "billing.record") == ["billing.charge"]
    assert callees_of(code.graph, "billing.charge") == ["billing.record", "helpers.validate"]
    assert callers_of(code.graph, "billing.charge") == ["orders.order.pay"]
    assert defined_in(code.graph, "billing.py") == [
        "billing.charge",
        "billing.record",
        "billing.store",
    ]
    assert defined_in(code.graph, "orders.py") == ["orders.order"]
    assert imports_of(code.graph, "billing.py") == ["helpers.validate", "os"]
    assert imports_of(code.graph, "orders.py") == ["billing.charge"]


def test_multi_hop_reachability_through_calls():
    code = CodeGraph()
    code.add_file("billing.py", BILLING_V1)
    assert reachable_from(code.graph, "billing.charge") == {
        "billing.record": 1,
        "helpers.validate": 1,
        "billing.store": 2,
    }
    assert reaches(code.graph, "billing.charge", "billing.store")
    assert not reaches(code.graph, "billing.store", "billing.charge")
    assert call_path(code.graph, "billing.charge", "billing.store") == [
        "billing.charge",
        "billing.record",
        "billing.store",
    ]
    assert call_path(code.graph, "billing.charge", "billing.charge") == ["billing.charge"]
    assert call_path(code.graph, "billing.store", "billing.charge") is None
    # max_hops caps the walk: store is 2 hops away.
    assert reachable_from(code.graph, "billing.charge", max_hops=1) == {
        "billing.record": 1,
        "helpers.validate": 1,
    }
    assert not reaches(code.graph, "billing.charge", "billing.store", max_hops=1)
    with pytest.raises(ValueError):
        reachable_from(code.graph, "billing.charge", max_hops=0)


def test_bare_name_resolution_unique_and_ambiguous():
    code = CodeGraph()
    code.add_file("billing.py", BILLING_V1)
    assert resolve_code_name(code.graph, "record") == "billing.record"
    assert callers_of(code.graph, "record") == ["billing.charge"]
    assert callers_of(code.graph, "no_such_function") == []
    assert call_path(code.graph, "charge", "no_such_function") is None
    code.add_file("archive.py", "def store(amount):\n    pass\n")
    with pytest.raises(ValueError):
        resolve_code_name(code.graph, "store")
    with pytest.raises(ValueError):
        callers_of(code.graph, "store")


def test_code_graph_shares_the_prose_graph():
    graph = KnowledgeGraph()
    graph.add_document("d1", "Toko Gadget Murah sells Samsung Galaxy S24.")
    code = CodeGraph(graph)
    code.add_file("billing.py", BILLING_V1)
    # Prose and code nodes live in the one graph, not two.
    assert "toko gadget murah" in graph.nodes
    assert "billing.charge" in graph.nodes
    assert graph.node_count == 2 + 6  # prose nodes + code nodes
    assert code.callers_of("billing.record") == ["billing.charge"]


def test_incremental_update_removes_stale_entities_without_duplicates():
    code = CodeGraph()
    code.add_file("billing.py", BILLING_V1)
    code.add_file("orders.py", ORDERS_V1)
    nodes_before = code.graph.node_count
    edges_before = code.graph.edge_count
    assert code.reaches("billing.charge", "billing.store")

    stats = code.update_file("billing.py", BILLING_V2)
    # Stale entities are gone, new ones are in, nothing duplicated.
    assert "billing.store" not in code.graph.nodes
    assert "billing.refund" in code.graph.nodes
    assert "billing.record" in code.graph.nodes
    assert code.graph.node_count == nodes_before
    assert code.graph.edge_count == edges_before
    assert stats["removed_nodes"] >= 1
    assert stats["new_nodes"] >= 1
    assert not code.reaches("billing.charge", "billing.store")
    assert code.callers_of("billing.record") == ["billing.charge", "billing.refund"]
    assert code.callees_of("billing.charge") == ["billing.record", "helpers.validate"]
    # The other file is untouched by the update.
    assert code.callers_of("billing.charge") == ["orders.order.pay"]

    # The same text again is a no-op.
    again = code.update_file("billing.py", BILLING_V2)
    assert again["already_indexed"] is True
    assert code.graph.node_count == nodes_before
    assert code.graph.edge_count == edges_before


def test_remove_file_drops_only_its_entities():
    code = CodeGraph()
    code.add_file("billing.py", BILLING_V1)
    code.add_file("orders.py", ORDERS_V1)
    removed = code.remove_file("orders.py")
    assert removed["removed_files"] == 1
    assert "orders.order" not in code.graph.nodes
    assert "orders.order.pay" not in code.graph.nodes
    # billing.charge survives: billing.py still defines it.
    assert "billing.charge" in code.graph.nodes
    assert code.callers_of("billing.charge") == []
    assert code.callees_of("billing.record") == ["billing.store"]
    with pytest.raises(KeyError):
        code.remove_file("orders.py")


def test_json_extractor_default_uses_live_router_completion():
    """The default completion is the router's live caller.

    The transport is fake, so no network or vault is touched,
    but the request goes through the real ``complete`` path:
    the router's model choice, the OpenRouter URL, and the
    Bearer key all reach the fake transport.
    """
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"entities": [{"name": "Toko Gadget Murah", '
                            '"kind": "seller"}], "triples": [{"subject": '
                            '"Toko Gadget Murah", "relation": "sells", '
                            '"object": "Samsung Galaxy S24"}]}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    extractor = JsonTripleExtractor(
        "default",
        transport=httpx.MockTransport(handler),
        key_resolver=lambda provider: "test-key",
    )
    found = extractor.extract("Toko Gadget Murah sells Samsung Galaxy S24.", "doc#p0")
    assert "openrouter.ai" in seen["url"]
    assert seen["auth"] == "Bearer test-key"
    assert found.entities[0].name == "toko gadget murah"
    assert found.entities[0].kind == "seller"
    assert (found.triples[0].subject, found.triples[0].relation, found.triples[0].object) == (
        "toko gadget murah",
        "sells",
        "samsung galaxy s24",
    )


def test_json_extractor_default_builds_without_key_or_network():
    """Constructing the default extractor needs no key and makes
    no call: the key is resolved on the first extraction."""
    extractor = JsonTripleExtractor()
    assert callable(extractor.complete)
    extractor = JsonTripleExtractor("default")
    assert callable(extractor.complete)
    with pytest.raises(ValueError):
        JsonTripleExtractor("not a callable")
    with pytest.raises(ValueError):
        JsonTripleExtractor(lambda prompt: "{}", transport=httpx.MockTransport(lambda r: r))
