"""One retrieval pipeline (HippoRAG + LightRAG + LlamaIndex reranker).

The three repos are one pipeline, not three systems: the
knowledge graph is built once, multi-hop retrieval walks it,
query modes choose how much of it to use, and the reranker
orders the shortlist. This package only exports the pieces;
``puspa.memory.episodic`` and ``puspa.graphs.identity`` wire the
pipeline into live recall.
"""

from .code_graph import (
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
from .embedding_health import (
    EmbeddingHealth,
    EmbeddingStartupRefused,
    MixedEmbedderError,
    evaluate_startup,
    plan_reembed_tiers,
)
from .hipporag import (
    HippoRAGRetriever,
    find_seed_nodes,
    personalized_pagerank,
    seed_weights,
)
from .knowledge_graph import (
    DeterministicExtractor,
    Entity,
    EntityExtractor,
    JsonTripleExtractor,
    KnowledgeGraph,
    Triple,
    normalize_entity_name,
)
from .modes import (
    QUERY_MODES,
    global_query,
    hybrid_query,
    local_query,
    mix_query,
    naive_query,
    query,
)
from .reranker import (
    CrossEncoderReranker,
    DeterministicReranker,
    RerankerUnavailable,
    decompose_query,
    make_reranker,
    retrieve_with_decomposition,
)

__all__ = [
    "QUERY_MODES",
    "CodeExtractor",
    "CodeGraph",
    "CrossEncoderReranker",
    "DeterministicExtractor",
    "DeterministicReranker",
    "EmbeddingHealth",
    "EmbeddingStartupRefused",
    "Entity",
    "EntityExtractor",
    "HippoRAGRetriever",
    "JsonTripleExtractor",
    "KnowledgeGraph",
    "MixedEmbedderError",
    "RerankerUnavailable",
    "Triple",
    "call_path",
    "callees_of",
    "callers_of",
    "decompose_query",
    "defined_in",
    "evaluate_startup",
    "find_seed_nodes",
    "global_query",
    "hybrid_query",
    "imports_of",
    "local_query",
    "make_reranker",
    "mix_query",
    "module_name_for_path",
    "naive_query",
    "normalize_entity_name",
    "personalized_pagerank",
    "plan_reembed_tiers",
    "query",
    "reachable_from",
    "reaches",
    "resolve_code_name",
    "retrieve_with_decomposition",
    "seed_weights",
]
