"""Multi-hop retrieval benchmark (plan section 8.3).

Compares BM25, plain vectors, and HippoRAG on recall@5 and answer
accuracy over 100 multi-hop questions:

- 50 internal questions, hand-written against the Puspa price
  graph below (products, sellers, promos, fraud reports) with
  answers verified against the passages by construction: a test
  asserts every gold answer string appears verbatim in a gold
  passage.
- 50 public questions, reserved from a public multi-hop subset
  (2WikiMultihopQA dev split). Those are EMPTY SLOTS in v1: the
  dataset license was not confirmed, so nothing was imported.
  See ``data/public_sources.md``.

Metrics are deterministic and model-free: recall@5 checks whether
a gold passage is in the top 5; answer accuracy checks whether
the gold answer string appears in any top-5 passage text. No
model judges the answer.

HippoRAG runs through the implementation already in the repo
(``puspa.retrieval.hipporag``), so this comparison is live, not a
placeholder. If HippoRAG does not beat plain vectors plus a
reranker on this set, the extra triple-extraction cost at index
time is not justified, and this benchmark is the number that
decides it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from puspa.memory.embeddings import HashEmbedding, cosine_similarity
from puspa.retrieval.hipporag import HippoRAGRetriever
from puspa.retrieval.knowledge_graph import KnowledgeGraph
from puspa.retrieval.reranker import DeterministicReranker

from .bm25 import BM25Index

RETRIEVAL_SET_VERSION = "1.0.0"
INTERNAL_QUESTION_COUNT = 50
PUBLIC_QUESTION_COUNT = 50

#: The eval price graph: 12 products x 4 passages (price, seller,
#: promo, fraud report or trust report). Written in the same style
#: as the price graph the HippoRAG tests already prove reachable.
PASSAGES: dict[str, str] = {
    "d01": "Samsung Galaxy S24 SM-S921B is a phone. Samsung Galaxy S24 costs Rp12.999.000 at Tokopedia.",
    "d02": "Toko Gadget Murah sells Samsung Galaxy S24. Toko Gadget Murah is a seller on Tokopedia.",
    "d03": "Toko Gadget Murah offers a promo: Samsung Galaxy S24 for Rp5.999.000, half price.",
    "d04": "Fraud report FR-88: Toko Gadget Murah ran a fake promo. The seller never shipped the phone.",
    "d05": "Xiaomi Redmi Note 13 is a phone. Xiaomi Redmi Note 13 costs Rp3.199.000 at Shopee.",
    "d06": "Toko Resmi Xiaomi sells Xiaomi Redmi Note 13. Toko Resmi Xiaomi is a seller on Shopee.",
    "d07": "Toko Resmi Xiaomi offers a promo: Xiaomi Redmi Note 13 for Rp2.999.000.",
    "d08": "Buyer Dewi reports Toko Resmi Xiaomi is trusted. The seller shipped the phone in two days.",
    "d09": "Asus Vivobook 14 is a laptop. Asus Vivobook 14 costs Rp9.499.000 at Blibli.",
    "d10": "Toko Laptop Jaya sells Asus Vivobook 14. Toko Laptop Jaya is a seller on Blibli.",
    "d11": "Toko Laptop Jaya offers free shipping for Asus Vivobook 14.",
    "d12": "Fraud report FR-91: Toko Ponsel Abal ran a fake promo. The seller never shipped the laptop.",
    "d13": "Samsung Galaxy A55 SM-A556E is a phone. Samsung Galaxy A55 costs Rp5.999.000 at Tokopedia.",
    "d14": "Toko Sinar Jaya sells Samsung Galaxy A55. Toko Sinar Jaya is a seller on Tokopedia.",
    "d15": "Toko Sinar Jaya offers a promo: Samsung Galaxy A55 for Rp5.499.000.",
    "d16": "Buyer Ani reports Toko Sinar Jaya is trusted. The seller shipped the phone in two days.",
    "d17": "Apple iPhone 13 is a phone. Apple iPhone 13 costs Rp9.999.000 at Tokopedia.",
    "d18": "Toko iBox Resmi sells Apple iPhone 13. Toko iBox Resmi is a seller on Tokopedia.",
    "d19": "Toko iBox Resmi offers a promo: Apple iPhone 13 for Rp9.499.000 with cashback Rp100.000.",
    "d20": "Buyer Budi reports Toko iBox Resmi is trusted. The seller shipped the phone in two days.",
    "d21": "Oppo Reno 11 is a phone. Oppo Reno 11 costs Rp5.499.000 at Shopee.",
    "d22": "Toko Oppo Center sells Oppo Reno 11. Toko Oppo Center is a seller on Shopee.",
    "d23": "Toko Oppo Center offers a promo: Oppo Reno 11 for Rp4.999.000.",
    "d24": "Buyer Citra reports Toko Oppo Center is trusted. The seller shipped the phone in two days.",
    "d25": "Vivo V30 is a phone. Vivo V30 costs Rp5.999.000 at Blibli.",
    "d26": "Toko Vivo Store sells Vivo V30. Toko Vivo Store is a seller on Blibli.",
    "d27": "Toko Vivo Store offers a promo: Vivo V30 for Rp5.499.000 with free shipping.",
    "d28": "Buyer Dedi reports Toko Vivo Store is trusted. The seller shipped the phone in two days.",
    "d29": "HP Pavilion 15 is a laptop. HP Pavilion 15 costs Rp12.499.000 at Tokopedia.",
    "d30": "Toko Laptop Jaya sells HP Pavilion 15. Toko Laptop Jaya is a seller on Tokopedia.",
    "d31": "Toko Laptop Jaya offers a promo: HP Pavilion 15 for Rp11.999.000.",
    "d32": "Buyer Eka reports Toko Laptop Jaya is trusted. The seller shipped the laptop in two days.",
    "d33": "Acer Aspire 5 is a laptop. Acer Aspire 5 costs Rp7.999.000 at Shopee.",
    "d34": "Toko Acer Official sells Acer Aspire 5. Toko Acer Official is a seller on Shopee.",
    "d35": "Toko Acer Official offers a promo: Acer Aspire 5 for Rp7.499.000.",
    "d36": "Buyer Fajar reports Toko Acer Official is trusted. The seller shipped the laptop in two days.",
    "d37": "Realme 11 Pro is a phone. Realme 11 Pro costs Rp4.999.000 at Tokopedia.",
    "d38": "Toko Ponsel Abal sells Realme 11 Pro. Toko Ponsel Abal is a seller on Tokopedia.",
    "d39": "Toko Ponsel Abal offers a promo: Realme 11 Pro for Rp2.499.000, half price.",
    "d40": "Fraud report FR-97: Toko Ponsel Abal ran a fake promo. The seller never shipped the phone.",
    "d41": "Infinix Note 40 is a phone. Infinix Note 40 costs Rp2.799.000 at Shopee.",
    "d42": "Toko Infinix Mall sells Infinix Note 40. Toko Infinix Mall is a seller on Shopee.",
    "d43": "Toko Infinix Mall offers a promo: Infinix Note 40 for Rp2.499.000.",
    "d44": "Buyer Gita reports Toko Infinix Mall is trusted. The seller shipped the phone in two days.",
    "d45": "Lenovo IdeaPad Slim 3 is a laptop. Lenovo IdeaPad Slim 3 costs Rp6.499.000 at Blibli.",
    "d46": "Toko Lenovo Plaza sells Lenovo IdeaPad Slim 3. Toko Lenovo Plaza is a seller on Blibli.",
    "d47": "Toko Lenovo Plaza offers a promo: Lenovo IdeaPad Slim 3 for Rp5.999.000.",
    "d48": "Buyer Hadi reports Toko Lenovo Plaza is trusted. The seller shipped the laptop in two days.",
}


@dataclass(frozen=True)
class RetrievalQuestion:
    """One multi-hop question with a verified gold answer."""

    id: str
    question: str
    gold_answer: str
    gold_passages: tuple[str, ...]
    origin: str = "internal"
    status: str = "live"


def _q(
    qid: str, question: str, gold_answer: str, gold_passages: tuple[str, ...]
) -> RetrievalQuestion:
    return RetrievalQuestion(
        id=qid, question=question, gold_answer=gold_answer, gold_passages=gold_passages
    )


def internal_questions() -> list[RetrievalQuestion]:
    """The 50 hand-written internal multi-hop questions."""
    return [
        _q(
            "RET-001",
            "Is the cheap Samsung Galaxy S24 deal trustworthy?",
            "never shipped the phone",
            ("d04",),
        ),
        _q("RET-002", "Who sells the Samsung Galaxy S24?", "Toko Gadget Murah", ("d02",)),
        _q("RET-003", "What is the real price of Samsung Galaxy S24?", "Rp12.999.000", ("d01",)),
        _q(
            "RET-004",
            "What promo does Toko Gadget Murah offer for Samsung Galaxy S24?",
            "half price",
            ("d03",),
        ),
        _q(
            "RET-005",
            "Is the Xiaomi Redmi Note 13 seller trustworthy?",
            "shipped the phone in two days",
            ("d08",),
        ),
        _q("RET-006", "Who sells the Xiaomi Redmi Note 13?", "Toko Resmi Xiaomi", ("d06",)),
        _q("RET-007", "What is the real price of Xiaomi Redmi Note 13?", "Rp3.199.000", ("d05",)),
        _q(
            "RET-008",
            "What promo does Toko Resmi Xiaomi offer for Xiaomi Redmi Note 13?",
            "Rp2.999.000",
            ("d07",),
        ),
        _q(
            "RET-009",
            "Is the cheap Asus Vivobook 14 deal trustworthy?",
            "never shipped the laptop",
            ("d12",),
        ),
        _q("RET-010", "Who sells the Asus Vivobook 14?", "Toko Laptop Jaya", ("d10",)),
        _q("RET-011", "What is the real price of Asus Vivobook 14?", "Rp9.499.000", ("d09",)),
        _q("RET-012", "Which laptop seller offers free shipping?", "Toko Laptop Jaya", ("d11",)),
        _q(
            "RET-013",
            "Is the Samsung Galaxy A55 seller trustworthy?",
            "shipped the phone in two days",
            ("d16",),
        ),
        _q("RET-014", "Who sells the Samsung Galaxy A55?", "Toko Sinar Jaya", ("d14",)),
        _q("RET-015", "What is the real price of Samsung Galaxy A55?", "Rp5.999.000", ("d13",)),
        _q(
            "RET-016",
            "What promo does Toko Sinar Jaya offer for Samsung Galaxy A55?",
            "Rp5.499.000",
            ("d15",),
        ),
        _q(
            "RET-017",
            "Is the Apple iPhone 13 seller trustworthy?",
            "shipped the phone in two days",
            ("d20",),
        ),
        _q("RET-018", "Who sells the Apple iPhone 13?", "Toko iBox Resmi", ("d18",)),
        _q("RET-019", "What is the real price of Apple iPhone 13?", "Rp9.999.000", ("d17",)),
        _q(
            "RET-020",
            "What promo does Toko iBox Resmi offer for Apple iPhone 13?",
            "cashback Rp100.000",
            ("d19",),
        ),
        _q(
            "RET-021",
            "Is the Oppo Reno 11 seller trustworthy?",
            "shipped the phone in two days",
            ("d24",),
        ),
        _q("RET-022", "Who sells the Oppo Reno 11?", "Toko Oppo Center", ("d22",)),
        _q("RET-023", "What is the real price of Oppo Reno 11?", "Rp5.499.000", ("d21",)),
        _q(
            "RET-024",
            "What promo does Toko Oppo Center offer for Oppo Reno 11?",
            "Rp4.999.000",
            ("d23",),
        ),
        _q(
            "RET-025",
            "Is the Vivo V30 seller trustworthy?",
            "shipped the phone in two days",
            ("d28",),
        ),
        _q("RET-026", "Who sells the Vivo V30?", "Toko Vivo Store", ("d26",)),
        _q("RET-027", "What is the real price of Vivo V30?", "Rp5.999.000", ("d25",)),
        _q(
            "RET-028",
            "What promo does Toko Vivo Store offer for Vivo V30?",
            "free shipping",
            ("d27",),
        ),
        _q(
            "RET-029",
            "Is the HP Pavilion 15 seller trustworthy?",
            "shipped the laptop in two days",
            ("d32",),
        ),
        _q("RET-030", "Who sells the HP Pavilion 15?", "Toko Laptop Jaya", ("d30",)),
        _q("RET-031", "What is the real price of HP Pavilion 15?", "Rp12.499.000", ("d29",)),
        _q(
            "RET-032",
            "What promo does Toko Laptop Jaya offer for HP Pavilion 15?",
            "Rp11.999.000",
            ("d31",),
        ),
        _q(
            "RET-033",
            "Is the Acer Aspire 5 seller trustworthy?",
            "shipped the laptop in two days",
            ("d36",),
        ),
        _q("RET-034", "Who sells the Acer Aspire 5?", "Toko Acer Official", ("d34",)),
        _q("RET-035", "What is the real price of Acer Aspire 5?", "Rp7.999.000", ("d33",)),
        _q(
            "RET-036",
            "What promo does Toko Acer Official offer for Acer Aspire 5?",
            "Rp7.499.000",
            ("d35",),
        ),
        _q(
            "RET-037",
            "Is the cheap Realme 11 Pro deal trustworthy?",
            "never shipped the phone",
            ("d40",),
        ),
        _q("RET-038", "Who sells the Realme 11 Pro?", "Toko Ponsel Abal", ("d38",)),
        _q("RET-039", "What is the real price of Realme 11 Pro?", "Rp4.999.000", ("d37",)),
        _q(
            "RET-040",
            "What promo does Toko Ponsel Abal offer for Realme 11 Pro?",
            "half price",
            ("d39",),
        ),
        _q(
            "RET-041",
            "Is the Infinix Note 40 seller trustworthy?",
            "shipped the phone in two days",
            ("d44",),
        ),
        _q("RET-042", "Who sells the Infinix Note 40?", "Toko Infinix Mall", ("d42",)),
        _q("RET-043", "What is the real price of Infinix Note 40?", "Rp2.799.000", ("d41",)),
        _q(
            "RET-044",
            "What promo does Toko Infinix Mall offer for Infinix Note 40?",
            "Rp2.499.000",
            ("d43",),
        ),
        _q(
            "RET-045",
            "Is the Lenovo IdeaPad Slim 3 seller trustworthy?",
            "shipped the laptop in two days",
            ("d48",),
        ),
        _q("RET-046", "Who sells the Lenovo IdeaPad Slim 3?", "Toko Lenovo Plaza", ("d46",)),
        _q("RET-047", "What is the real price of Lenovo IdeaPad Slim 3?", "Rp6.499.000", ("d45",)),
        _q(
            "RET-048",
            "What promo does Toko Lenovo Plaza offer for Lenovo IdeaPad Slim 3?",
            "Rp5.999.000",
            ("d47",),
        ),
        _q(
            "RET-049",
            "Which sellers have a bad track record?",
            "Toko Gadget Murah",
            ("d04", "d12", "d40"),
        ),
        _q(
            "RET-050",
            "Who should I avoid when buying Asus Vivobook 14?",
            "Toko Ponsel Abal",
            ("d12",),
        ),
    ]


def public_question_slots() -> list[RetrievalQuestion]:
    """The 50 reserved public questions: empty slots in v1."""
    return [
        RetrievalQuestion(
            id=f"RET-PUB-{n:03d}",
            question="",
            gold_answer="",
            gold_passages=(),
            origin="public_2wikimultihopqa",
            status="empty_slot",
        )
        for n in range(1, PUBLIC_QUESTION_COUNT + 1)
    ]


def build_eval_graph() -> KnowledgeGraph:
    """The price graph as a KnowledgeGraph (one document per passage)."""
    graph = KnowledgeGraph()
    for doc_id, text in PASSAGES.items():
        graph.add_document(doc_id, text)
    return graph


def _doc_id(passage_id: str) -> str:
    """HippoRAG passage ids carry a ``#pN`` suffix; strip it."""
    return passage_id.split("#", maxsplit=1)[0]


@dataclass
class RetrieverScore:
    recall_at_5: float
    answer_accuracy: float
    hits: int
    answered: int
    questions: int


def _score(rank_fn, questions: list[RetrievalQuestion], *, top_k: int) -> RetrieverScore:
    hits = 0
    answered = 0
    for question in questions:
        ranked = rank_fn(question.question)[:top_k]
        ids = {_doc_id(str(r.get("passage_id") or r.get("id", ""))) for r in ranked}
        if ids & set(question.gold_passages):
            hits += 1
        texts = " ".join(
            str(r.get("text", PASSAGES.get(_doc_id(str(r.get("passage_id") or "")), "")))
            for r in ranked
        )
        if question.gold_answer.lower() in texts.lower():
            answered += 1
    total = len(questions)
    return RetrieverScore(
        recall_at_5=hits / total if total else 0.0,
        answer_accuracy=answered / total if total else 0.0,
        hits=hits,
        answered=answered,
        questions=total,
    )


@dataclass
class RetrievalComparison:
    bm25: RetrieverScore = field(default_factory=lambda: RetrieverScore(0.0, 0.0, 0, 0, 0))
    vectors: RetrieverScore = field(default_factory=lambda: RetrieverScore(0.0, 0.0, 0, 0, 0))
    hipporag: RetrieverScore = field(default_factory=lambda: RetrieverScore(0.0, 0.0, 0, 0, 0))

    def as_dict(self) -> dict:
        out = {}
        for name in ("bm25", "vectors", "hipporag"):
            score = getattr(self, name)
            out[name] = {
                "recall_at_5": score.recall_at_5,
                "answer_accuracy": score.answer_accuracy,
                "hits": score.hits,
                "answered": score.answered,
                "questions": score.questions,
            }
        return out


def compare_retrievers(*, top_k: int = 5, limit_questions: int | None = None) -> dict:
    """Compare BM25, plain vectors, and HippoRAG on the live set.

    Returns ``{"bm25": {...}, "vectors": {...}, "hipporag": {...}}``
    with recall@5 and answer accuracy. Only the 50 live internal
    questions run; the 50 public questions are empty slots.
    """
    questions = internal_questions()
    if limit_questions is not None:
        questions = questions[:limit_questions]
    graph = build_eval_graph()
    embedder = HashEmbedding()

    bm25_index = BM25Index.build(PASSAGES)

    def bm25_rank(query: str) -> list[dict]:
        return [
            {"passage_id": r["passage_id"], "text": PASSAGES[r["passage_id"]]}
            for r in bm25_index.rank(query, top_k=top_k)
        ]

    passage_vecs = {doc_id: embedder.embed(text) for doc_id, text in PASSAGES.items()}

    def vector_rank(query: str) -> list[dict]:
        query_vec = embedder.embed(query)
        ranked = sorted(
            PASSAGES,
            key=lambda doc_id: (-cosine_similarity(query_vec, passage_vecs[doc_id]), doc_id),
        )
        return [{"passage_id": doc_id, "text": PASSAGES[doc_id]} for doc_id in ranked[:top_k]]

    retriever = HippoRAGRetriever(graph, embedder=embedder)
    reranker = DeterministicReranker(embedder=embedder)

    def hipporag_rank(query: str) -> list[dict]:
        return retriever.retrieve(query, top_k=top_k, reranker=reranker)

    comparison = RetrievalComparison(
        bm25=_score(bm25_rank, questions, top_k=top_k),
        vectors=_score(vector_rank, questions, top_k=top_k),
        hipporag=_score(hipporag_rank, questions, top_k=top_k),
    )
    return comparison.as_dict()
