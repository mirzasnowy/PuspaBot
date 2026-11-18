"""Edge temporal di graf harga/fraud (pola Graphiti, ditulis ulang).

Graphiti (Apache-2.0): tiap fakta di graf punya masa berlaku
(`valid_from` / `valid_until`) dan fakta kedaluwarsa tidak lagi
dianggap benar. Di Puspa, pola itu selama ini hanya ada di profil
(`puspa.memory.profile`: satu versi aktif, versi lama berstatus
`replaced` dan riwayat tetap bisa dibaca). Modul ini membawa pola
yang sama ke graf: harga dan laporan fraud itu inherently temporal,
tapi edge graf selama ini tidak punya validity sama sekali.

Mekanisme inti (bukan seam, semuanya memengaruhi hasil query):

- Tiap edge membawa `valid_from` / `valid_until`. Jendela
  validity adalah sumber kebenaran tunggal: query menghitung
  kedaluwarsa dari jendela itu, bukan dari status yang disimpan.
- Query default (`query`, `neighbors`, `relations`) HANYA
  mengembalikan edge yang masih berlaku pada `as_of` (default:
  sekarang, UTC). Edge kedaluwarsa dan edge yang sudah diganti
  tidak muncul.
- `include_stale=True` mengembalikan semua edge, tapi tiap entri
  ditandai `stale` + `stale_reason` (`"expired"` / `"superseded"`),
  jadi pemanggil bisa menampilkan peringatan, bukan fakta basi
  yang disajikan sebagai fakta segar.
- Fakta baru untuk pasangan (subject, relation) yang sama
  menggantikan fakta lama (supersede): edge lama ditandai
  `superseded`, `valid_until`-nya ditutup pada awal berlakunya
  fakta baru, dan riwayatnya tetap bisa dibaca lewat
  `include_stale=True` atau `history()`. Pemanggil mendapat
  `superseded` (id edge lama) di hasil, seperti `conflicts_with`
  di profil. Tidak ada penimpaan diam-diam.
- Edge tanpa validity (keduanya None) selalu berlaku, kecuali
  sudah diganti, backward compat dengan graf yang sudah ada.

Batasan jujur: modul ini in-memory dan tidak menyentuh Postgres.
Persistensi `valid_from` / `valid_until` pada `price_series` /
`listing_links` butuh migrasi skema (`infra/schema_graphs.sql`)
yang di luar scope worker ini; lihat laporan ke koordinator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

STALE_EXPIRED = "expired"
STALE_SUPERSEDED = "superseded"
STALE_NOT_YET_VALID = "not_yet_valid"


class TemporalError(Exception):
    """Graf temporal menolak masukan (jendela tidak valid, dsb.)."""


def _normalize_name(name: str) -> str:
    """Satu bentuk kanonik nama entitas: huruf kecil, spasi tunggal."""
    return " ".join(str(name).lower().split())


def _require_aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TemporalError(f"{field_name} harus datetime atau None, bukan {type(value).__name__}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise TemporalError(f"{field_name} harus timezone-aware (pakai datetime dengan tzinfo=timezone.utc)")
    return value


def _as_of_or_now(as_of: datetime | None) -> datetime:
    if as_of is None:
        return datetime.now(timezone.utc)
    return _require_aware(as_of, "as_of")


@dataclass
class TemporalEdge:
    """Satu edge berarah dengan masa berlaku.

    `valid_until` eksklusif: edge berlaku untuk
    `valid_from <= as_of < valid_until`. `valid_until=None`
    berarti terbuka (tidak kedaluwarsa). `superseded=True`
    berarti fakta ini sudah diganti fakta baru untuk pasangan
    (subject, relation) yang sama.
    """

    subject: str
    relation: str
    object: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source_ids: set = field(default_factory=set)
    superseded: bool = False
    id: str = field(default_factory=lambda: uuid4().hex[:12])

    def is_current(self, as_of: datetime | None = None) -> bool:
        """True bila edge ini masih berlaku pada `as_of`."""
        moment = _as_of_or_now(as_of)
        if self.superseded:
            return False
        if self.valid_from is not None and moment < self.valid_from:
            return False
        if self.valid_until is not None and moment >= self.valid_until:
            return False
        return True

    def stale_reason(self, as_of: datetime | None = None) -> str | None:
        """Alasan edge ini basi pada `as_of`, atau None bila segar."""
        moment = _as_of_or_now(as_of)
        if self.superseded:
            return STALE_SUPERSEDED
        if self.valid_until is not None and moment >= self.valid_until:
            return STALE_EXPIRED
        if self.valid_from is not None and moment < self.valid_from:
            return STALE_NOT_YET_VALID
        return None

    def to_dict(self, as_of: datetime | None = None) -> dict:
        """Edge sebagai dict, dengan flag stale untuk `as_of`."""
        reason = self.stale_reason(as_of)
        return {
            "id": self.id,
            "subject": self.subject,
            "relation": self.relation,
            "object": self.object,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "source_ids": sorted(self.source_ids),
            "superseded": self.superseded,
            "stale": reason is not None,
            "stale_reason": reason,
        }


class TemporalGraph:
    """Graf in-memory dengan edge temporal + supersede.

    Kunci supersede adalah pasangan (subject, relation): satu
    subject hanya punya satu fakta aktif per relasi (harga
    terkini satu produk, status fraud terkini satu seller).
    Menambah fakta baru untuk kunci yang sama menggantikan yang
    lama; kunci lain tidak tersentuh.
    """

    def __init__(self):
        self.edges: list[TemporalEdge] = []

    def add_edge(self, subject: str, relation: str, object: str, *,
                 valid_from: datetime | None = None,
                 valid_until: datetime | None = None,
                 source_id: str | None = None,
                 supersede: bool = True,
                 as_of: datetime | None = None) -> dict:
        """Tambah edge; gantikan edge aktif untuk kunci yang sama.

        Kembalikan {"edge": edge_baru, "superseded": [id_lama]}.
        Edge lama yang diganti: `superseded=True` dan
        `valid_until` ditutup pada `valid_from` fakta baru (atau
        pada `as_of` bila fakta baru tanpa `valid_from`).

        `supersede=False` mematikan penggantian: dipakai untuk
        edge dari graf retrieval yang multi-valued (satu subject
        + relasi bisa punya banyak object, mis. satu seller
        `sells` banyak produk), di sana kedaluwarsa lewat
        jendela validity saja, bukan lewat penggantian.
        """
        if not subject or not isinstance(subject, str):
            raise TemporalError("subject harus string tak kosong")
        if not relation or not isinstance(relation, str):
            raise TemporalError("relation harus string tak kosong")
        if not object or not isinstance(object, str):
            raise TemporalError("object harus string tak kosong")
        valid_from = _require_aware(valid_from, "valid_from")
        valid_until = _require_aware(valid_until, "valid_until")
        if valid_from is not None and valid_until is not None and valid_until <= valid_from:
            raise TemporalError("valid_until harus setelah valid_from")
        moment = _as_of_or_now(as_of)
        subject = _normalize_name(subject)
        relation = relation.strip().lower()
        object = _normalize_name(object)
        superseded_ids = []
        close_at = valid_from if valid_from is not None else moment
        if supersede:
            for old in self.edges:
                if old.subject != subject or old.relation != relation:
                    continue
                if old.superseded:
                    continue
                if not old.is_current(moment):
                    continue
                old.superseded = True
                old.valid_until = close_at
                superseded_ids.append(old.id)
        edge = TemporalEdge(
            subject=subject, relation=relation, object=object,
            valid_from=valid_from, valid_until=valid_until,
            source_ids={source_id} if source_id else set())
        self.edges.append(edge)
        return {"edge": edge, "superseded": superseded_ids}

    def query(self, subject: str | None = None, relation: str | None = None,
              object: str | None = None, *, as_of: datetime | None = None,
              include_stale: bool = False) -> list[dict]:
        """Edge yang cocok, sebagai dict.

        Default: hanya edge yang masih berlaku pada `as_of`.
        `include_stale=True`: semua edge yang cocok, masing-masing
        ditandai `stale` + `stale_reason`.
        """
        moment = _as_of_or_now(as_of)
        subject = _normalize_name(subject) if subject else None
        relation = relation.strip().lower() if relation else None
        object = _normalize_name(object) if object else None
        out = []
        for edge in self.edges:
            if subject is not None and edge.subject != subject:
                continue
            if relation is not None and edge.relation != relation:
                continue
            if object is not None and edge.object != object:
                continue
            if not include_stale and not edge.is_current(moment):
                continue
            out.append(edge.to_dict(moment))
        return out

    def neighbors(self, name: str, relation: str | None = None, *,
                  as_of: datetime | None = None,
                  include_stale: bool = False) -> list[str]:
        """Nama tetangga satu node, hanya lewat edge yang berlaku."""
        name = _normalize_name(name)
        out = set()
        for entry in self.query(as_of=as_of, include_stale=include_stale):
            if relation is not None and entry["relation"] != relation.strip().lower():
                continue
            if entry["subject"] == name:
                out.add(entry["object"])
            elif entry["object"] == name:
                out.add(entry["subject"])
        return sorted(out)

    def relations(self, name: str, *, as_of: datetime | None = None,
                  include_stale: bool = False) -> list[tuple]:
        """Triple yang menyentuh satu node, hanya yang berlaku."""
        name = _normalize_name(name)
        return sorted(
            (e["subject"], e["relation"], e["object"])
            for e in self.query(as_of=as_of, include_stale=include_stale)
            if e["subject"] == name or e["object"] == name
        )

    def history(self, subject: str, relation: str, *,
                as_of: datetime | None = None) -> list[dict]:
        """Semua versi satu fakta (aktif + diganti + kedaluwarsa),
        terurut dari yang terbaru. Riwayat tidak pernah dihapus
        oleh supersede, hanya ditandai."""
        entries = self.query(subject=subject, relation=relation,
                             as_of=as_of, include_stale=True)
        entries.sort(
            key=lambda e: e["valid_from"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True)
        return entries

    def current_fact(self, subject: str, relation: str, *,
                     as_of: datetime | None = None) -> dict | None:
        """Satu fakta aktif untuk (subject, relation), atau None."""
        found = self.query(subject=subject, relation=relation, as_of=as_of)
        return found[0] if found else None

    def prune_expired(self, as_of: datetime | None = None) -> dict:
        """Hapus permanen edge yang sudah kedaluwarsa pada `as_of`.

        Opt-in dan eksplisit: defaultnya riwayat dipertahankan.
        Edge yang diganti (superseded) TIDAK ikut terhapus, itu
        riwayat versi, bukan sampah kedaluwarsa. Kembalikan
        {"removed": n, "edges": m}.
        """
        moment = _as_of_or_now(as_of)
        kept = []
        removed = 0
        for edge in self.edges:
            if (not edge.superseded and edge.valid_until is not None
                    and moment >= edge.valid_until):
                removed += 1
            else:
                kept.append(edge)
        self.edges = kept
        return {"removed": removed, "edges": len(self.edges)}

    @property
    def edge_count(self) -> int:
        return len(self.edges)


def stale_fact_rate(entries: list[dict]) -> float:
    """Fraksi entri basi dalam satu hasil query.

    `entries` adalah hasil `query(include_stale=True)`: tiap entri
    membawa flag `stale`. 0.0 berarti tidak ada fakta basi yang
    lolos; 1.0 berarti semuanya basi. Daftar kosong -> 0.0.
    """
    if not entries:
        return 0.0
    stale = sum(1 for e in entries if e.get("stale"))
    return round(stale / len(entries), 4)


def from_knowledge_graph(knowledge_graph, validity=None) -> TemporalGraph:
    """Bangun graf temporal dari `KnowledgeGraph` retrieval.

    `knowledge_graph` dibaca saja (modul `puspa.retrieval` tidak
    diubah): tiap edge-nya menjadi `TemporalEdge`. `validity`
    adalah dict {(subject, relation, object): (valid_from,
    valid_until)} atau callable yang menerima satu `Triple` dari
    graf retrieval dan mengembalikan pasangan itu (atau None).
    Edge tanpa entri validity tetap bekerja seperti dulu: tanpa
    jendela, selalu berlaku, backward compat dengan graf yang
    sudah ada.

    Edge dari graf retrieval dimasukkan dengan `supersede=False`:
    graf retrieval itu multi-valued (satu seller `sells` banyak
    produk), jadi satu pasangan (subject, relation) boleh punya
    banyak object sekaligus. Di sini yang memfilter fakta basi
    adalah jendela validity, bukan penggantian.
    """
    graph = TemporalGraph()
    if callable(validity):
        lookup = validity
    else:
        mapping = validity or {}
        lookup = lambda triple: mapping.get(
            (triple.subject, triple.relation, triple.object))
    for triple in knowledge_graph.edges.values():
        window = lookup(triple)
        valid_from, valid_until = (None, None)
        if window is not None:
            valid_from, valid_until = window
        graph.add_edge(
            triple.subject, triple.relation, triple.object,
            valid_from=valid_from, valid_until=valid_until,
            source_id=sorted(triple.source_ids)[0] if triple.source_ids else None,
            supersede=False)
    return graph
