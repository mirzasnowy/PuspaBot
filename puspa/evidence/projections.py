"""Proyeksi Buku Kerja (§2.3 revisi 7).

Semua tampilan PWA adalah turunan log kejadian dan bisa dibangun
ulang kapan saja. Proyeksi di sini murni fungsi atas daftar kejadian
(dict seperti yang dikembalikan `Ledger.events`): tanpa tulisan
ganda yang bisa berselisih.

Proyeksi:
- thread_chat: pesan user/agen, handoff, hasil (layar Chat).
- kantor: status live per agen + sub-agen (layar Kantor).
- audit: semua tool_dipanggil + persetujuan (Pengaturan).
- bukti: bukti_dicatat + pemeriksaan_selesai (panel Bukti).
- biaya: token + Rupiah per kejadian (Kantor, gerbang 10).
"""

from __future__ import annotations


def thread_chat(kejadian: list[dict]) -> list[dict]:
    """Thread chat: pesan + handoff + hasil, urut waktu."""
    keluar = []
    for ev in kejadian:
        jenis = ev.get("jenis", "")
        isi = ev.get("isi") or {}
        if jenis in ("pesan_user", "pesan_agen"):
            keluar.append({"jenis": "pesan", "dari": isi.get("dari", ev.get("agen_id", "")),
                           "teks": isi.get("teks", ""), "waktu": str(ev.get("waktu", ""))})
        elif jenis.startswith("handoff_"):
            keluar.append({"jenis": "handoff", "status": jenis[len("handoff_"):],
                           "dari": isi.get("dari", ""), "ke": isi.get("ke", ""),
                           "tujuan": isi.get("tujuan", ""), "waktu": str(ev.get("waktu", ""))})
        elif jenis in ("tugas_selesai", "tugas_gagal"):
            keluar.append({"jenis": "hasil", "status": jenis,
                           "tugas_id": ev.get("tugas_id", ""), "waktu": str(ev.get("waktu", ""))})
    return keluar


def kantor(kejadian: list[dict]) -> dict:
    """Status live tim: kejadian terakhir tiap agen/sub-agen + biaya."""
    agen: dict[str, dict] = {}
    for ev in kejadian:
        aid = ev.get("agen_id", "")
        if not aid:
            continue
        info = agen.setdefault(aid, {"agen_id": aid, "status": "idle",
                                     "tugas_terakhir": "", "biaya_rp": 0,
                                     "sub_agen": {}})
        jenis = ev.get("jenis", "")
        isi = ev.get("isi") or {}
        if jenis == "tugas_dibuat":
            info["status"] = "bekerja"
            info["tugas_terakhir"] = ev.get("tugas_id", "")
        elif jenis in ("tugas_selesai", "tugas_gagal", "tugas_dihentikan"):
            info["status"] = "idle"
        elif jenis == "subagen_dibuat":
            sub = isi.get("sub_agen_id") or ev.get("sub_agen_id") or ""
            info["sub_agen"][sub] = {"status": "bekerja", "kelas_izin": isi.get("kelas_izin", "")}
        elif jenis == "detak":
            sub = ev.get("sub_agen_id") or ""
            if sub in info["sub_agen"]:
                info["sub_agen"][sub]["status"] = "bekerja"
        if isinstance(isi.get("biaya_rp"), (int, float)):
            info["biaya_rp"] += int(isi["biaya_rp"])
    for info in agen.values():
        info["sub_agen"] = list(info["sub_agen"].values())
    return {"agen": list(agen.values())}


def audit(kejadian: list[dict]) -> list[dict]:
    """Semua tool call + persetujuan (Pengaturan)."""
    return [ev for ev in kejadian
            if ev.get("jenis", "") in ("tool_dipanggil", "tool_selesai",
                                       "persetujuan_diminta", "persetujuan_diberikan",
                                       "persetujuan_ditolak")]


def daftar_bukti(kejadian: list[dict]) -> list[dict]:
    """Bukti + vonis pemeriksaan (panel Bukti)."""
    bukti = {}
    for ev in kejadian:
        isi = ev.get("isi") or {}
        if ev.get("jenis") == "bukti_dicatat" and isi.get("bukti_id"):
            bukti[isi["bukti_id"]] = {"bukti_id": isi["bukti_id"], "klaim": isi.get("klaim", ""),
                                      "nilai": isi.get("nilai"), "vonis": "Belum pasti"}
        elif ev.get("jenis") == "pemeriksaan_selesai" and isi.get("bukti_id") in bukti:
            bukti[isi["bukti_id"]]["vonis"] = isi.get("vonis", bukti[isi["bukti_id"]]["vonis"])
    return list(bukti.values())


def biaya(kejadian: list[dict]) -> dict:
    """Total biaya per tugas + total keseluruhan."""
    per_tugas: dict[str, int] = {}
    for ev in kejadian:
        isi = ev.get("isi") or {}
        if isinstance(isi.get("biaya_rp"), (int, float)):
            tid = ev.get("tugas_id", "?")
            per_tugas[tid] = per_tugas.get(tid, 0) + int(isi["biaya_rp"])
    return {"per_tugas": per_tugas, "total_rp": sum(per_tugas.values())}
