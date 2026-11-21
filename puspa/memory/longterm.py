"""Long-term consolidation (K16, Kosmos Phase 2).

Memori jangka panjang: apa yang naik dari episodik ke long-term,
bagaimana yang tidak penting pelan-pelan turun prioritasnya, dan
di mana barang dingin disimpan supaya tetap bisa kembali.

Aturan main, semuanya deterministik dan bisa diuji tanpa model:

- Promotion rules (:func:`promotion_decision`): sebuah episode
  naik ke long-term kalau polanya berulang (satu layanan muncul
  3 kali atau lebih), kalau pentingnya tinggi (0.7 atau lebih),
  atau kalau itu koreksi user. Koreksi selalu naik sebagai
  pinned, bukan sekadar promoted.
- Forgetting curve (:func:`forgetting_priority`): kurva lupa
  Ebbinghaus, tapi HANYA menurunkan prioritas retrieval, tidak
  pernah menghapus. Item low-importance tetap ada dan tetap bisa
  di-retrieve kalau dicari eksplisit, cuma ranking-nya turun.
  Prioritas tidak pernah menyentuh nol (lantai 0.05). Pengulangan
  memperlambat lupa: tiap pengulangan memperpanjang konstanta
  waktunya.
- Pinned tidak pernah decay: koreksi user dan komitmen selalu
  berprioritas 1.0, di usia berapa pun. Ini konsisten dengan
  amnesia test (:mod:`puspa.memory.amnesia`): pinned harus selalu
  bisa di-recall dingin.
- Tiers (:func:`tier_for`): pinned, hot, warm, cold, archived.
  Item dingin yang sudah lama dan prioritasnya rendah pindah ke
  archive; arsip tetap bisa dicari dan kembali ke warm begitu
  diakses (:func:`touch_item` / :func:`restore_item`).

Apa yang dipakai ulang dari K9, apa yang baru:

- Dipakai ulang: :func:`puspa.memory.consolidate.consolidate_idle`
  (ringkasan episodik + promosi prosedural) dan
  :func:`puspa.memory.durable.classify_fact` (koreksi / komitmen /
  goal adalah pinned). K16 TIDAK menulis konsolidasi kedua: pass
  K16 memanggil K9 dulu (``run_k9=True``), lalu menerapkan
  promotion rules, forgetting curve, dan tiers di atasnya.
- Baru di K16: promotion rules eksplisit, forgetting curve sebagai
  prioritas retrieval, tiers + archive, dan seam
  :func:`promote_from_working` untuk K15 (working memory memanggil
  ini saat rehearsal mempromosikan sebuah item).

Penyimpanan: tabel ``longterm_tiers`` (migrasi 0034) hanya menyimpan
STATE penempatan (tier, importance, repetitions, pinned, kapan
terakhir diakses, kapan diarsipkan). Isi item tetap tinggal di
tabel asalnya (``profile_facts`` / ``episodic``), yang tidak pernah
dihapus oleh decay, jadi arsip selalu bisa dibaca ulang dari
sumbernya. ``MemoryLongTermStore`` adalah store in-memory dengan
interface yang sama, untuk tes dan pemanggil tanpa database.

Semua fungsi murni menerima ``now`` yang bisa di-inject, jadi tes
tidak pernah bergantung pada jam dinding nyata.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from . import consolidate, durable, episodic
from . import facts as facts_module
from .schema import resolve_dsn

__all__ = [
    "ARCHIVE_AFTER_DAYS",
    "ARCHIVE_PRIORITY",
    "FORGETTING_TAU_DAYS",
    "HOT_PRIORITY",
    "IMPORTANCE_THRESHOLD",
    "MIN_PRIORITY",
    "REPETITION_THRESHOLD",
    "TIERS",
    "WARM_PRIORITY",
    "MemoryItem",
    "MemoryLongTermStore",
    "PostgresLongTermStore",
    "archive_item",
    "consolidate_longterm",
    "default_store",
    "forgetting_priority",
    "item_from_episode",
    "item_from_fact",
    "promote_from_working",
    "promotion_decision",
    "rank_items",
    "restore_item",
    "tier_for",
    "to_item",
    "touch_item",
]

#: Tiers, dari yang paling mudah di-recall sampai arsip.
TIERS = ("pinned", "hot", "warm", "cold", "archived")

#: Satu pola (layanan) yang muncul sekian kali naik ke long-term.
REPETITION_THRESHOLD = 3

#: Importance di atas ini naik ke long-term walau baru sekali muncul.
IMPORTANCE_THRESHOLD = 0.7

#: Konstanta waktu kurva lupa (hari): setelah 30 hari tanpa
#: diakses, prioritas item biasa tinggal ~37% dari importance-nya.
FORGETTING_TAU_DAYS = 30.0

#: Tiap pengulangan memperpanjang konstanta waktu sebesar ini.
REPETITION_TAU_BONUS = 0.2

#: Lantai prioritas: tidak pernah nol, item tetap bisa di-retrieve.
MIN_PRIORITY = 0.05

#: Prioritas di atas ini berarti hot; di atas WARM_PRIORITY warm.
#: Hot 0.55 (bukan 0.6) supaya item segar ber-importance 0.6
#: (tugas selesai) mendarat hot dengan margin, bukan tepat di
#: batas sehingga umur beberapa detik menjatuhkannya ke warm.
HOT_PRIORITY = 0.55
WARM_PRIORITY = 0.3

#: Di bawah prioritas ini DAN lebih tua dari ARCHIVE_AFTER_DAYS,
#: item pindah ke archive.
ARCHIVE_PRIORITY = 0.15
ARCHIVE_AFTER_DAYS = 90

#: Importance heuristik per jenis episode (deterministik).
_EPISODE_IMPORTANCE = {
    "user_message": 0.5,
    "task_completed": 0.6,
}

#: Importance heuristik per sumber fakta (deterministik).
_FACT_IMPORTANCE = {
    "user_confirmed": 0.8,
    "user_stated": 0.5,
    "inferred": 0.3,
}

#: Section durable yang selalu pinned (lihat durable.pinned_items).
_PINNED_SECTIONS = ("corrections", "commitments", "goals")


def _now() -> datetime:
    return datetime.now(UTC)


def _service_of(content: str) -> str:
    """Service token sebuah episode: teks sebelum titik dua pertama.

    Aturan yang sama dengan K9 (``consolidate._service_of``):
    pola yang berulang dihitung per layanan, bukan per kalimat.
    """
    return str(content or "").split(":", 1)[0].strip().lower()


@dataclass
class MemoryItem:
    """Satu item memori yang sedang dinilai K16.

    ``importance`` 0..1, ``repetitions`` berapa kali polanya
    muncul, ``pinned`` / ``is_correction`` menandai item yang
    tidak pernah decay. ``tier`` adalah penempatan saat ini;
    ``archived_at`` terisi hanya saat tier-nya archived.
    ``text`` kosong untuk baris yang dibaca dari store Postgres:
    isi item tinggal di tabel asalnya, store tier hanya menyimpan
    state penempatan (lihat docstring modul).
    """

    item_id: str
    kind: str
    user_id: str
    key: str
    text: str = ""
    importance: float = 0.5
    repetitions: int = 1
    pinned: bool = False
    is_correction: bool = False
    created_at: datetime = field(default_factory=_now)
    last_accessed_at: datetime | None = None
    tier: str = "warm"
    archived_at: datetime | None = None


def _age_days(item: MemoryItem, now: datetime) -> float:
    """Umur item dalam hari, dihitung dari akses terakhir.

    Akses terakhir menyegarkan ingatan (seperti rehearsal):
    umur dihitung dari akses terakhir kalau ada, kalau tidak
    dari kapan item dibuat. Stempel masa depan dijepit ke nol.
    """
    anchor = item.last_accessed_at or item.created_at
    age = (now - anchor).total_seconds() / 86400.0
    return max(0.0, age)


def forgetting_priority(item: MemoryItem, now: datetime | None = None) -> float:
    """Prioritas retrieval item ini sekarang (0.05..1.0).

    Koreksi dan pinned selalu 1.0: tidak pernah decay, di usia
    berapa pun. Item biasa mengikuti kurva lupa Ebbinghaus,
    ``importance * exp(-umur / tau)``; pengulangan memperpanjang
    tau, jadi pola yang sering muncul dilupakan lebih lambat.
    Hasilnya dijepit ke lantai :data:`MIN_PRIORITY`, jadi item
    low-importance hanya kehilangan prioritas, tidak pernah hilang.
    """
    now = now or _now()
    if item.pinned or item.is_correction:
        return 1.0
    age = _age_days(item, now)
    tau = FORGETTING_TAU_DAYS * (1.0 + REPETITION_TAU_BONUS * max(0, item.repetitions - 1))
    priority = item.importance * math.exp(-age / tau)
    return max(MIN_PRIORITY, min(1.0, priority))


def promotion_decision(item: MemoryItem) -> tuple[str, str]:
    """Aturan promosi eksplisit: naik, pinned, atau tetap.

    Mengembalikan ``(decision, reason)`` dengan decision salah
    satu dari ``"pinned"``, ``"promoted"``, ``"stay"``:

    - koreksi user (atau item yang sudah pinned) -> pinned.
    - pola muncul >= :data:`REPETITION_THRESHOLD` kali -> promoted
      (alasan "repetition").
    - importance >= :data:`IMPORTANCE_THRESHOLD` -> promoted
      (alasan "importance").
    - selain itu -> stay (tetap episodik / working, bukan
      long-term).
    """
    if item.is_correction or item.pinned:
        return ("pinned", "correction")
    if item.repetitions >= REPETITION_THRESHOLD:
        return ("promoted", "repetition")
    if item.importance >= IMPORTANCE_THRESHOLD:
        return ("promoted", "importance")
    return ("stay", "below thresholds")


def tier_for(item: MemoryItem, now: datetime | None = None) -> str:
    """Tier item ini sekarang, dari prioritas dan umurnya.

    Pinned / koreksi selalu "pinned". Selain itu prioritas >= 0.55
    berarti hot, >= 0.3 warm, dan item yang lebih tua dari 90 hari
    dengan prioritas di bawah 0.15 berarti archived; sisanya cold.
    """
    now = now or _now()
    if item.pinned or item.is_correction:
        return "pinned"
    priority = forgetting_priority(item, now)
    if priority >= HOT_PRIORITY:
        return "hot"
    if priority >= WARM_PRIORITY:
        return "warm"
    if _age_days(item, now) > ARCHIVE_AFTER_DAYS and priority < ARCHIVE_PRIORITY:
        return "archived"
    return "cold"


def archive_item(item: MemoryItem, now: datetime | None = None) -> MemoryItem:
    """Pindahkan item ke archive (tetap ada, tetap bisa kembali)."""
    return replace(item, tier="archived", archived_at=now or _now())


def restore_item(item: MemoryItem, now: datetime | None = None) -> MemoryItem:
    """Kembalikan item arsip ke warm: akses menyegarkannya.

    Pass konsolidasi berikutnya menilai ulang tier-nya dari
    prioritas yang sudah disegarkan, jadi item yang memang masih
    penting naik lagi, yang tidak akan turun kembali.
    """
    return replace(item, tier="warm", archived_at=None, last_accessed_at=now or _now())


def touch_item(item: MemoryItem, now: datetime | None = None) -> MemoryItem:
    """Catat satu akses: arsip kembali, yang lain disegarkan.

    Mengakses item arsip adalah cara ia kembali (restore ke
    warm); mengakses item biasa hanya memperbarui kapan terakhir
    diakses, jadi kurva lupanya mulai lagi dari nol.
    """
    now = now or _now()
    if item.tier == "archived":
        return restore_item(item, now)
    return replace(item, last_accessed_at=now)


def rank_items(
    items: list[MemoryItem],
    relevance: dict[str, float],
    now: datetime | None = None,
    *,
    include_archived: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Ranking retrieval: relevansi dikali prioritas lupa.

    ``relevance`` memetakan item id ke skor kecocokan query
    (0..1, dari pencarian semantik atau pencocokan lain). Skor
    akhir adalah ``relevance * forgetting_priority``: item yang
    sudah lama tidak diakses turun ranking-nya, tapi TIDAK
    dibuang — ia tetap ada di hasil, di bawah item segar dengan
    relevansi yang sama. Item arsip dikeluarkan secara default
    (pencarian biasa tidak mengobok-ngobok arsip); dengan
    ``include_archived=True`` (pencarian eksplisit) mereka ikut
    diranking dan bisa kembali. Hasil diurutkan skor menurun,
    lalu yang paling baru diakses dulu. Tiap entri membawa
    ``item``, ``score``, ``relevance`` dan ``priority``.
    """
    now = now or _now()
    ranked: list[dict[str, Any]] = []
    for item in items:
        if item.tier == "archived" and not include_archived:
            continue
        rel = max(0.0, min(1.0, float(relevance.get(item.item_id, 0.0))))
        priority = forgetting_priority(item, now)
        ranked.append(
            {"item": item, "score": rel * priority, "relevance": rel, "priority": priority}
        )
    ranked.sort(
        key=lambda entry: entry["item"].last_accessed_at or entry["item"].created_at,
        reverse=True,
    )
    ranked.sort(key=lambda entry: entry["score"], reverse=True)
    if limit is not None and limit >= 0:
        ranked = ranked[:limit]
    return ranked


def item_from_episode(row: dict[str, Any], *, repetitions: int = 1) -> MemoryItem:
    """Item K16 dari satu baris episodik.

    Importance heuristik per jenis (pesan user 0.5, tugas selesai
    0.6, sisanya 0.3); repetitions adalah berapa kali layanan
    episode ini muncul di jendela konsolidasi. Episode tidak
    pernah jadi koreksi: koreksi datang dari fakta.
    """
    content = str(row.get("content") or "")
    return MemoryItem(
        item_id=str(row.get("id") or ""),
        kind="episode",
        user_id=str(row.get("user_id") or ""),
        key=_service_of(content) or "episode",
        text=content,
        importance=_EPISODE_IMPORTANCE.get(str(row.get("type") or ""), 0.3),
        repetitions=max(0, int(repetitions)),
        created_at=row.get("created_at") or _now(),
    )


def item_from_fact(row: dict[str, Any], *, now: datetime | None = None) -> MemoryItem:
    """Item K16 dari satu baris fakta aktif.

    Importance heuristik per sumber (user_confirmed 0.8,
    user_stated 0.5, inferred 0.3). Section durable menentukan
    pinned: koreksi, komitmen, dan standing goals selalu pinned
    (sama seperti :func:`puspa.memory.durable.pinned_items`), dan
    koreksi ditandai ``is_correction`` sehingga tidak pernah decay
    walau flag pinned-nya suatu saat dilepas.
    """
    now = now or _now()
    section = durable.classify_fact(row)
    return MemoryItem(
        item_id=str(row.get("id") or ""),
        kind="fact",
        user_id=str(row.get("user_id") or ""),
        key=str(row.get("key") or ""),
        text=str(row.get("content") or row.get("value") or ""),
        importance=_FACT_IMPORTANCE.get(str(row.get("source") or ""), 0.3),
        repetitions=1,
        pinned=section in _PINNED_SECTIONS,
        is_correction=section == "corrections",
        created_at=row.get("created_at") or now,
    )


def _public_item(item: MemoryItem) -> dict[str, Any]:
    """Bentuk dict publik sebuah item (yang disimpan store)."""
    return {
        "item_id": str(item.item_id),
        "kind": item.kind,
        "user_id": str(item.user_id),
        "key": item.key,
        "text": item.text,
        "importance": float(item.importance),
        "repetitions": int(item.repetitions),
        "pinned": bool(item.pinned),
        "is_correction": bool(item.is_correction),
        "created_at": item.created_at,
        "last_accessed_at": item.last_accessed_at,
        "tier": item.tier,
        "archived_at": item.archived_at,
    }


def to_item(data: dict[str, Any]) -> MemoryItem:
    """Bangun :class:`MemoryItem` dari dict store."""
    return MemoryItem(
        item_id=str(data.get("item_id") or data.get("id") or ""),
        kind=str(data.get("kind") or data.get("item_kind") or ""),
        user_id=str(data.get("user_id") or ""),
        key=str(data.get("key") or data.get("item_key") or ""),
        text=str(data.get("text") or ""),
        importance=float(data.get("importance") or 0.0),
        repetitions=int(data.get("repetitions") or 0),
        pinned=bool(data.get("pinned")),
        is_correction=bool(data.get("is_correction")),
        created_at=data.get("created_at") or _now(),
        last_accessed_at=data.get("last_accessed_at"),
        tier=str(data.get("tier") or "warm"),
        archived_at=data.get("archived_at"),
    )


class MemoryLongTermStore:
    """Store tier in-memory: interface yang sama dengan Postgres."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], dict[str, Any]] = {}

    def upsert(self, item: MemoryItem) -> dict[str, Any]:
        record = _public_item(item)
        key = (record["user_id"], record["kind"], record["item_id"])
        self._rows[key] = record
        return dict(record)

    def get(self, user_id: str, kind: str, item_id: str) -> dict[str, Any] | None:
        record = self._rows.get((str(user_id), str(kind), str(item_id)))
        return dict(record) if record is not None else None

    def list_user(self, user_id: str, *, tiers: tuple[str, ...] | None = None) -> list[dict]:
        rows = [
            dict(record)
            for (owner, _kind, _item_id), record in self._rows.items()
            if owner == str(user_id) and (tiers is None or record["tier"] in tiers)
        ]
        rows.sort(key=lambda record: str(record["created_at"]), reverse=True)
        return rows

    def touch(
        self, user_id: str, kind: str, item_id: str, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """Akses satu item: arsip kembali, yang lain disegarkan."""
        record = self.get(user_id, kind, item_id)
        if record is None:
            return None
        return self.upsert(touch_item(to_item(record), now or _now()))


class PostgresLongTermStore:
    """Store tier Postgres di atas tabel ``longterm_tiers``."""

    def __init__(self, dsn: Any = None) -> None:
        self.dsn = resolve_dsn(dsn)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def upsert(self, item: MemoryItem) -> dict[str, Any]:
        record = _public_item(item)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO longterm_tiers "
                    "(user_id, item_kind, item_id, item_key, tier, importance, "
                    "repetitions, pinned, is_correction, last_accessed_at, "
                    "archived_at, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (user_id, item_kind, item_id) DO UPDATE SET "
                    "item_key = EXCLUDED.item_key, tier = EXCLUDED.tier, "
                    "importance = EXCLUDED.importance, "
                    "repetitions = EXCLUDED.repetitions, pinned = EXCLUDED.pinned, "
                    "is_correction = EXCLUDED.is_correction, "
                    "last_accessed_at = EXCLUDED.last_accessed_at, "
                    "archived_at = EXCLUDED.archived_at, updated_at = now() "
                    "RETURNING *",
                    (
                        record["user_id"],
                        record["kind"],
                        record["item_id"],
                        record["key"],
                        record["tier"],
                        record["importance"],
                        record["repetitions"],
                        record["pinned"],
                        record["is_correction"],
                        record["last_accessed_at"],
                        record["archived_at"],
                        record["created_at"],
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return _public_row(row)

    def get(self, user_id: str, kind: str, item_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM longterm_tiers "
                "WHERE user_id = %s AND item_kind = %s AND item_id = %s",
                (str(user_id), str(kind), str(item_id)),
            )
            row = cur.fetchone()
        return _public_row(row) if row is not None else None

    def list_user(self, user_id: str, *, tiers: tuple[str, ...] | None = None) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            if tiers is None:
                cur.execute(
                    "SELECT * FROM longterm_tiers WHERE user_id = %s ORDER BY created_at DESC",
                    (str(user_id),),
                )
            else:
                cur.execute(
                    "SELECT * FROM longterm_tiers WHERE user_id = %s "
                    "AND tier = ANY(%s) ORDER BY created_at DESC",
                    (str(user_id), list(tiers)),
                )
            return [_public_row(row) for row in cur.fetchall()]

    def touch(
        self, user_id: str, kind: str, item_id: str, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """Akses satu item: arsip kembali, yang lain disegarkan."""
        record = self.get(user_id, kind, item_id)
        if record is None:
            return None
        return self.upsert(touch_item(to_item(record), now or _now()))


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    """Baris Postgres ke bentuk dict publik (tanpa isi teks)."""
    return {
        "id": str(row["id"]),
        "item_id": str(row["item_id"]),
        "kind": row["item_kind"],
        "user_id": str(row["user_id"]),
        "key": row["item_key"],
        "text": "",
        "importance": float(row["importance"]),
        "repetitions": int(row["repetitions"]),
        "pinned": bool(row["pinned"]),
        "is_correction": bool(row["is_correction"]),
        "created_at": row["created_at"],
        "last_accessed_at": row["last_accessed_at"],
        "tier": row["tier"],
        "archived_at": row["archived_at"],
        "updated_at": row["updated_at"],
    }


def default_store(dsn: Any = None) -> PostgresLongTermStore:
    """Store produksi (Postgres)."""
    return PostgresLongTermStore(dsn=dsn)


def promote_from_working(
    working_item: dict[str, Any],
    *,
    user_id: str,
    now: datetime | None = None,
    store: Any = None,
    dsn: Any = None,
) -> dict[str, Any]:
    """Seam K15: working memory menyerahkan satu item ke long-term.

    K15 (working memory) memanggil ini saat rehearsal mempromosikan
    sebuah item: ``working_item`` membawa ``text`` (wajib) plus
    ``key``, ``importance``, ``repetitions``, ``pinned``,
    ``is_correction``, ``kind`` dan ``item_id`` (keduanya opsional;
    tanpa id, satu id baru dibuat). Aturan promosi K16 yang
    memutuskan: item yang lolos disimpan di tier-nya dan hasilnya
    membawa ``stored: True``; yang tidak lolos ("stay") tidak
    disimpan, dilaporkan apa adanya. Kembalikan ``{"item",
    "decision", "reason", "priority", "stored"}``.
    """
    now = now or _now()
    text = str(working_item.get("text") or "")
    if not text:
        raise ValueError("working_item must carry a non-empty text")
    item = MemoryItem(
        item_id=str(working_item.get("item_id") or uuid.uuid4()),
        kind=str(working_item.get("kind") or "fact"),
        user_id=str(user_id),
        key=str(working_item.get("key") or ""),
        text=text,
        importance=float(working_item.get("importance") or 0.5),
        repetitions=int(working_item.get("repetitions") or 1),
        pinned=bool(working_item.get("pinned")),
        is_correction=bool(working_item.get("is_correction")),
        created_at=working_item.get("created_at") or now,
    )
    decision, reason = promotion_decision(item)
    stored = False
    if decision != "stay":
        item = replace(item, tier=tier_for(item, now))
        target = store or default_store(dsn=dsn)
        target.upsert(item)
        stored = True
    return {
        "item": _public_item(item),
        "decision": decision,
        "reason": reason,
        "priority": forgetting_priority(item, now),
        "stored": stored,
    }


def consolidate_longterm(
    user_id: str,
    *,
    now: datetime | None = None,
    dsn: Any = None,
    store: Any = None,
    episodes: list[dict[str, Any]] | None = None,
    facts: list[dict[str, Any]] | None = None,
    run_k9: bool = False,
) -> dict[str, Any]:
    """Satu pass konsolidasi long-term (K16) untuk satu user.

    Langkahnya:

    1. Opsional: jalankan dulu konsolidasi K9
       (:func:`puspa.memory.consolidate.consolidate_idle`, ringkasan
       episodik + promosi prosedural) dengan ``run_k9=True`` — K16
       memperluas K9, bukan menggantikannya; hasilnya dilaporkan
       sebagai ``k9``.
    2. Baca kandidat: episode user (``episodic.list_episodes``)
       dan fakta aktif. Repetisi dihitung per layanan, jadi pola
       yang muncul 3 kali atau lebih ketahuan di sini.
    3. Terapkan promotion rules: kandidat baru yang lolos masuk
       long-term; yang tidak lolos dilaporkan sebagai ``stayed``
       dan tidak disimpan. Kandidat yang sudah tersimpan
       diperbarui (importance / repetitions / pinned terbaru),
       dengan kapan terakhir diakses tetap dipertahankan.
    4. Nilai ulang tier SEMUA item tersimpan dengan kurva lupa:
       yang dingin dan tua pindah ke archive; yang arsipnya baru
       diakses (``last_accessed_at`` segar, dicatat oleh
       :func:`touch_item` atau recall) keluar dari archive dan
       dihitung sebagai ``restored``.
    5. Simpan semua perubahan dan laporkan ``promoted``,
       ``pinned``, ``stayed``, ``archived``, ``restored`` dan
       hitungan ``tiers``.

    ``episodes`` dan ``facts`` bisa di-inject (list dict polos)
    untuk tes; kalau tidak, dibaca dari Postgres dengan ``dsn``.
    Tidak ada yang dihapus di pass ini: penghapusan bukan urusan
    decay.
    """
    now = now or _now()
    k9_result = None
    if run_k9:
        k9_result = consolidate.consolidate_idle(user_id, dsn=dsn, now=now)
    if episodes is None:
        episodes = episodic.list_episodes(user_id, limit=500, dsn=dsn)
    if facts is None:
        facts = facts_module.PostgresFactStore(dsn=dsn).list_user(user_id, statuses=("active",))
    target = store or default_store(dsn=dsn)

    service_counts: dict[str, int] = {}
    for row in episodes or []:
        service = _service_of(row.get("content") or "")
        service_counts[service] = service_counts.get(service, 0) + 1
    candidates: dict[tuple[str, str], MemoryItem] = {}
    for row in episodes or []:
        item = item_from_episode(
            row, repetitions=service_counts[_service_of(row.get("content") or "")]
        )
        candidates[(item.kind, item.item_id)] = item
    for row in facts or []:
        item = item_from_fact(row, now=now)
        candidates[(item.kind, item.item_id)] = item

    working: dict[tuple[str, str], MemoryItem] = {
        (row["kind"], row["item_id"]): to_item(row) for row in target.list_user(user_id)
    }
    promoted: list[str] = []
    pinned: list[str] = []
    archived: list[str] = []
    restored: list[str] = []
    stayed = 0
    for key, candidate in candidates.items():
        if key in working:
            old = working[key]
            working[key] = replace(
                old,
                importance=candidate.importance,
                repetitions=candidate.repetitions,
                pinned=candidate.pinned,
                is_correction=candidate.is_correction,
                key=candidate.key,
                text=candidate.text,
                created_at=candidate.created_at,
            )
            continue
        decision, _reason = promotion_decision(candidate)
        if decision == "stay":
            stayed += 1
            continue
        working[key] = candidate
        if decision == "pinned":
            pinned.append(candidate.key)
        else:
            promoted.append(candidate.key)
    for key, current in working.items():
        new_tier = tier_for(current, now)
        old_tier = current.tier
        if new_tier == "archived" and old_tier != "archived":
            updated = archive_item(current, now)
            archived.append(updated.key)
        elif new_tier != "archived" and old_tier == "archived":
            updated = replace(current, tier=new_tier, archived_at=None, last_accessed_at=now)
            restored.append(updated.key)
        elif new_tier != old_tier:
            updated = replace(current, tier=new_tier)
        else:
            updated = current
        working[key] = updated
        target.upsert(updated)
    tiers: dict[str, int] = {}
    for item in working.values():
        tiers[item.tier] = tiers.get(item.tier, 0) + 1
    return {
        "user_id": str(user_id),
        "promoted": promoted,
        "pinned": pinned,
        "stayed": stayed,
        "archived": archived,
        "restored": restored,
        "tiers": tiers,
        "k9": k9_result,
        "consolidated_at": now.isoformat(),
    }
