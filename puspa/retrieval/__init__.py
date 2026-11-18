"""One retrieval pipeline (HippoRAG + LightRAG + LlamaIndex reranker).

The three repos are one pipeline, not three systems: the
knowledge graph is built once, multi-hop retrieval walks it,
query modes choose how much of it to use, and the reranker
orders the shortlist. This package only exports the pieces;
``puspa.memory.episodic`` and ``puspa.graphs.identity`` wire the
pipeline into live recall.
"""

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
    "CrossEncoderReranker",
    "DeterministicExtractor",
    "DeterministicReranker",
    "Entity",
    "EntityExtractor",
    "HippoRAGRetriever",
    "JsonTripleExtractor",
    "KnowledgeGraph",
    "RerankerUnavailable",
    "Triple",
    "decompose_query",
    "find_seed_nodes",
    "global_query",
    "hybrid_query",
    "local_query",
    "make_reranker",
    "mix_query",
    "naive_query",
    "normalize_entity_name",
    "personalized_pagerank",
    "query",
    "retrieve_with_decomposition",
    "seed_weights",
]
