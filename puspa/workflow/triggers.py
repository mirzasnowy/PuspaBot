"""Pemicu alur (§2.12 + §2.14 revisi 7) — 4 pemicu MVP.

- jadwal: tiap tanggal / hari tertentu, interval minimal 1 jam.
- jatuh_tempo: N hari sebelum jatuh tempo tagihan.
- harga_turun: harga watchlist turun di bawah ambang (§3.8).
- manual: tombol atau chat.

Pemicu email masuk + webhook luar SESUDAH MVP — sengaja tidak
dibangun; `validasi_pemicu` menolak keduanya dengan alasan jelas.
Pemicu terjadwal disimpan di tabel pemicu Fase 2 (dipakai ulang, bukan
tabel baru) lewat `simpan_ke_penjadwal`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

JENIS_PEMICU = ("jadwal", "jatuh_tempo", "harga_turun", "manual")
# Pemicu yang sengaja ditolak di MVP (§2.12, keputusan 18).
PEMICU_DITOLAK = ("email_masuk", "webhook")
INTERVAL_MINIMAL_JAM = 1
HARI = ("senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "minggu")


class TriggerError(Exception):
    """Pemicu tidak valid atau di luar MVP."""


def validasi_pemicu(pemicu: dict) -> list[str]:
    """Periksa satu pemicu. Kembalikan daftar masalah (kosong = lolos)."""
    masalah: list[str] = []
    if not isinstance(pemicu, dict):
        return ["pemicu harus objek"]
    jenis = pemicu.get("jenis")
    if jenis in PEMICU_DITOLAK:
        return [f"pemicu {jenis!r} sesudah MVP (keputusan 18) — tidak dibangun"]
    if jenis not in JENIS_PEMICU:
        return [f"jenis pemicu {jenis!r} tidak dikenal"]
    if jenis == "jadwal":
        mode = pemicu.get("mode")
        if mode == "tanggal":
            tanggal = pemicu.get("tanggal")
            if not isinstance(tanggal, int) or not 1 <= tanggal <= 31:
                masalah.append("pemicu jadwal tanggal: 'tanggal' harus 1-31")
        elif mode == "hari":
            if pemicu.get("hari") not in HARI:
                masalah.append(f"pemicu jadwal hari: 'hari' harus salah satu {HARI}")
        elif mode == "interval_jam":
            jam = pemicu.get("jam")
            if not isinstance(jam, (int, float)) or jam < INTERVAL_MINIMAL_JAM:
                masalah.append(
                    f"pemicu jadwal interval: minimal {INTERVAL_MINIMAL_JAM} jam")
        else:
            masalah.append("pemicu jadwal: 'mode' harus tanggal/hari/interval_jam")
        jam_mulai = pemicu.get("jam_mulai", "07:00")
        try:
            time.fromisoformat(str(jam_mulai))
        except ValueError:
            masalah.append(f"pemicu jadwal: 'jam_mulai' {jam_mulai!r} bukan jam valid")
    elif jenis == "jatuh_tempo":
        hari = pemicu.get("hari_sebelum")
        if not isinstance(hari, int) or hari < 0:
            masalah.append("pemicu jatuh_tempo: 'hari_sebelum' harus bilangan >= 0")
        if pemicu.get("provider") not in ("PLN", "PDAM", "pulsa", "paket data"):
            masalah.append("pemicu jatuh_tempo: 'provider' harus provider MVP")
    elif jenis == "harga_turun":
        if not pemicu.get("produk_id"):
            masalah.append("pemicu harga_turun: 'produk_id' wajib (dari watchlist)")
        ambang = pemicu.get("ambang_harga")
        if not isinstance(ambang, (int, float)) or ambang <= 0:
            masalah.append("pemicu harga_turun: 'ambang_harga' harus Rupiah > 0")
    return masalah


def _jam_mulai(pemicu: dict) -> time:
    return time.fromisoformat(str(pemicu.get("jam_mulai", "07:00")))


def pemicu_berikutnya(pemicu: dict, sekarang: datetime | None = None) -> datetime:
    """Hitung waktu picu berikutnya untuk pemicu jadwal. `manual`,
    `jatuh_tempo`, dan `harga_turun` tidak punya jadwal tetap — mereka
    dievaluasi saat data berubah (lihat `cocok_jatuh_tempo` dan
    `cocok_harga_turun`); fungsi ini mengembalikan None untuk ketiganya.
    """
    if pemicu.get("jenis") != "jadwal":
        return None
    sekarang = sekarang or datetime.now(timezone.utc)
    jam = _jam_mulai(pemicu)
    mode = pemicu.get("mode")
    if mode == "interval_jam":
        return sekarang + timedelta(hours=float(pemicu["jam"]))
    if mode == "hari":
        target = HARI.index(pemicu["hari"])  # senin = 0
        calon = datetime.combine(sekarang.date(), jam, tzinfo=timezone.utc)
        selisih = (target - sekarang.weekday()) % 7
        calon += timedelta(days=selisih)
        if calon <= sekarang:
            calon += timedelta(days=7)
        return calon
    # mode tanggal: tanggal yang sama tiap bulan.
    tanggal = int(pemicu["tanggal"])
    tahun, bulan = sekarang.year, sekarang.month
    for _ in range(2):  # bulan ini, kalau lewat bulan depan
        try:
            calon = datetime.combine(
                date(tahun, bulan, tanggal), jam, tzinfo=timezone.utc)
        except ValueError:
            # tanggal 31 di bulan 30 hari: geser ke akhir bulan.
            if bulan == 12:
                tahun, bulan = tahun + 1, 1
            else:
                bulan += 1
            continue
        if calon > sekarang:
            return calon
        if bulan == 12:
            tahun, bulan = tahun + 1, 1
        else:
            bulan += 1
    raise TriggerError("tidak bisa menghitung pemicu berikutnya")


def cocok_jatuh_tempo(pemicu: dict, tagihan: list[dict],
                     hari_ini: date | None = None) -> list[dict]:
    """Tagihan mana yang memicu hari ini: jatuh tempo dalam
    `hari_sebelum` hari ke depan (dan belum dibayar). Kembalikan
    daftar tagihan yang cocok (kosong = belum waktunya)."""
    hari_ini = hari_ini or date.today()
    cocok = []
    for t in tagihan:
        if t.get("provider") != pemicu.get("provider"):
            continue
        if t.get("status") == "sudah_bayar":
            continue
        jatuh = t.get("jatuh_tempo")
        if not jatuh:
            continue
        jatuh = date.fromisoformat(str(jatuh)) if isinstance(jatuh, str) else jatuh
        selisih = (jatuh - hari_ini).days
        if 0 <= selisih <= int(pemicu.get("hari_sebelum", 3)):
            cocok.append(t)
    return cocok


def cocok_harga_turun(pemicu: dict, harga_kini: int | None) -> bool:
    """Benar bila harga kini (dari watchlist/sampler Fase 3) sudah di
    bawah atau sama dengan ambang. `harga_kini` None = belum ada data
    (tidak memicu — gagal terlihat, bukan tebak)."""
    if harga_kini is None:
        return False
    return int(harga_kini) <= int(pemicu["ambang_harga"])


def simpan_ke_penjadwal(*, user_id: str, alur_id: str, pemicu: dict,
                        sekarang: datetime | None = None, dsn=None):
    """Simpan pemicu jadwal ke tabel pemicu Fase 2 (dipakai ulang).
    Pemicu jatuh_tempo / harga_turun / manual tidak punya jadwal tetap
    sehingga tidak disimpan di sini — mereka dievaluasi tiap tick oleh
    `evaluasi_pemicu_data` di runner. Kembalikan Pemicu Fase 2 atau
    None bila jenisnya bukan jadwal."""
    from puspa.scheduler.scheduler import buat_pemicu

    if pemicu.get("jenis") != "jadwal":
        return None
    berikutnya = pemicu_berikutnya(pemicu, sekarang)
    return buat_pemicu(
        user_id, "jadwal",
        pemicu_berikutnya=berikutnya,
        jadwal=f"alur:{alur_id}",
        alur=alur_id,
        dsn=dsn,
    )
