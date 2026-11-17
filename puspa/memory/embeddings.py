"""Embedding lokal gratis untuk index episodik (definisi 2.7, C54).

Pilihan (diputuskan Fase 1, biaya Rp0 — lihat STATUS_FASE1.md):

- Implementasi bawaan: `HashEmbedding` — embedding deterministik dari
  hash token (tanpa model, tanpa jaringan, tanpa biaya). Kualitasnya
  semantiknya terbatas: kemiripan mengukur tumpang-tindih kata, bukan
  makna. Itu cukup untuk MVP 11 pengguna dan membuat jalur tulis +
  jalur baca episodik teruji ujung-ke-ujung tanpa dependensi baru.
- Seam pengganti: kelas apa pun dengan metode `embed(teks)` dan atribut
  `dim` bisa dipakai sebagai ganti (mis. model kecil lokal seperti
  BAAI/bge-small lewat fastembed, atau embedding murah OpenRouter).
  `EpisodicStore` menerima `embedder` sebagai parameter — ganti
  embedder tidak mengubah skema (dimensi vektor dicatat di
  `EMBEDDING_DIM`; ganti dimensi butuh migrasi kolom `vector(N)`).

Konvensi: identifier Inggris, nilai domain Indonesia (revisi 7).
"""

from __future__ import annotations

import hashlib
import math
import re

#: Dimensi vektor episodik. Harus sama dengan `vector(N)` di schema.sql.
EMBEDDING_DIM = 384

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


class HashEmbedding:
    """Embedding hash-token deterministik, ternormalisasi L2.

    Tiap token dipetakan ke satu dimensi lewat SHA-256, lalu vektor
    dinormalisasi. Teks yang sama selalu menghasilkan vektor yang sama;
    teks dengan kata yang sama punya cosine similarity tinggi.
    """

    dim = EMBEDDING_DIM

    def embed(self, text: str) -> list[float]:
        """Kembalikan vektor sepanjang `dim` untuk `text`."""
        if not isinstance(text, str):
            raise ValueError("teks harus string")
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            vec[index] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity dua vektor (diasumsikan ternormalisasi L2)."""
    if len(left) != len(right):
        raise ValueError("dimensi vektor tidak sama")
    return sum(a * b for a, b in zip(left, right))
