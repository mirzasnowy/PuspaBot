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

TRIGGER_TYPES = ("jadwal", "due_date", "price_turun", "manual")
# Pemicu yang sengaja ditolak di MVP (§2.12, keputusan 18).
TRIGGER_REJECTED = ("email_login", "webhook")
INTERVAL_MINIMAL_JAM = 1
HARI = ("senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "week")


class TriggerError(Exception):
    """Pemicu tidak valid atau di luar MVP."""


def validasi_trigger(trigger: dict) -> list[str]:
    """Periksa satu pemicu. Kembalikan daftar masalah (kosong = lolos)."""
    masalah: list[str] = []
    if not isinstance(trigger, dict):
        return ["pemicu harus objek"]
    event_type = trigger.get("type")
    if event_type in TRIGGER_REJECTED:
        return [f"pemicu {event_type!r} sesudah MVP (keputusan 18) — tidak dibangun"]
    if event_type not in TRIGGER_TYPES:
        return [f"jenis pemicu {event_type!r} tidak dikenal"]
    if event_type == "jadwal":
        mode = trigger.get("mode")
        if mode == "tanggal":
            tanggal = trigger.get("tanggal")
            if not isinstance(tanggal, int) or not 1 <= tanggal <= 31:
                masalah.append("pemicu jadwal tanggal: 'tanggal' harus 1-31")
        elif mode == "day":
            if trigger.get("day") not in HARI:
                masalah.append(f"pemicu jadwal hari: 'hari' harus salah satu {HARI}")
        elif mode == "interval_jam":
            jam = trigger.get("jam")
            if not isinstance(jam, (int, float)) or jam < INTERVAL_MINIMAL_JAM:
                masalah.append(
                    f"pemicu jadwal interval: minimal {INTERVAL_MINIMAL_JAM} jam")
        else:
            masalah.append("pemicu jadwal: 'mode' harus tanggal/hari/interval_jam")
        jam_mulai = trigger.get("jam_mulai", "07:00")
        try:
            time.fromisoformat(str(jam_mulai))
        except ValueError:
            masalah.append(f"pemicu jadwal: 'jam_mulai' {jam_mulai!r} bukan jam valid")
    elif event_type == "due_date":
        day = trigger.get("day_sebelum")
        if not isinstance(day, int) or day < 0:
            masalah.append("pemicu due_date: 'day_sebelum' harus bilangan >= 0")
        if trigger.get("provider") not in ("PLN", "PDAM", "airtime", "paket data"):
            masalah.append("pemicu due_date: 'provider' harus provider MVP")
    elif event_type == "price_turun":
        if not trigger.get("product_id"):
            masalah.append("pemicu price_turun: 'product_id' wajib (dari watchlist)")
        ambang = trigger.get("price_threshold")
        if not isinstance(ambang, (int, float)) or ambang <= 0:
            masalah.append("pemicu price_turun: 'price_threshold' harus Rupiah > 0")
    return masalah


def _jam_mulai(trigger: dict) -> time:
    return time.fromisoformat(str(trigger.get("jam_mulai", "07:00")))


def next_trigger_at(trigger: dict, sekarang: datetime | None = None) -> datetime:
    """Hitung waktu picu berikutnya untuk pemicu jadwal. `manual`,
    `jatuh_tempo`, dan `harga_turun` tidak punya jadwal tetap — mereka
    dievaluasi saat data berubah (lihat `cocok_jatuh_tempo` dan
    `cocok_harga_turun`); fungsi ini mengembalikan None untuk ketiganya.
    """
    if trigger.get("type") != "jadwal":
        return None
    sekarang = sekarang or datetime.now(timezone.utc)
    jam = _jam_mulai(trigger)
    mode = trigger.get("mode")
    if mode == "interval_jam":
        return sekarang + timedelta(hours=float(trigger["jam"]))
    if mode == "day":
        target = HARI.index(trigger["day"])  # senin = 0
        calon = datetime.combine(sekarang.date(), jam, tzinfo=timezone.utc)
        selisih = (target - sekarang.weekday()) % 7
        calon += timedelta(days=selisih)
        if calon <= sekarang:
            calon += timedelta(days=7)
        return calon
    # mode tanggal: tanggal yang sama tiap bulan.
    tanggal = int(trigger["tanggal"])
    tahun, month = sekarang.year, sekarang.month
    for _ in range(2):  # bulan ini, kalau lewat bulan depan
        try:
            calon = datetime.combine(
                date(tahun, month, tanggal), jam, tzinfo=timezone.utc)
        except ValueError:
            # tanggal 31 di bulan 30 hari: geser ke akhir bulan.
            if month == 12:
                tahun, month = tahun + 1, 1
            else:
                month += 1
            continue
        if calon > sekarang:
            return calon
        if month == 12:
            tahun, month = tahun + 1, 1
        else:
            month += 1
    raise TriggerError("tidak bisa menghitung pemicu berikutnya")


def match_due_date(trigger: dict, bill: list[dict],
                     today: date | None = None) -> list[dict]:
    """Tagihan mana yang memicu hari ini: jatuh tempo dalam
    `hari_sebelum` hari ke depan (dan belum dibayar). Kembalikan
    daftar tagihan yang cocok (kosong = belum waktunya)."""
    today = today or date.today()
    match = []
    for t in bill:
        if t.get("provider") != trigger.get("provider"):
            continue
        if t.get("status") == "already_pay":
            continue
        due = t.get("due_date")
        if not due:
            continue
        due = date.fromisoformat(str(due)) if isinstance(due, str) else due
        selisih = (due - today).days
        if 0 <= selisih <= int(trigger.get("day_sebelum", 3)):
            match.append(t)
    return match


def match_price_turun(trigger: dict, price_kini: int | None) -> bool:
    """Benar bila harga kini (dari watchlist/sampler Fase 3) sudah di
    bawah atau sama dengan ambang. `harga_kini` None = belum ada data
    (tidak memicu — gagal terlihat, bukan tebak)."""
    if price_kini is None:
        return False
    return int(price_kini) <= int(trigger["price_threshold"])


def save_to_penjadwal(*, user_id: str, workflow_id: str, trigger: dict,
                        sekarang: datetime | None = None, dsn=None):
    """Simpan pemicu jadwal ke tabel pemicu Fase 2 (dipakai ulang).
    Pemicu jatuh_tempo / harga_turun / manual tidak punya jadwal tetap
    sehingga tidak disimpan di sini — mereka dievaluasi tiap tick oleh
    `evaluasi_pemicu_data` di runner. Kembalikan Pemicu Fase 2 atau
    None bila jenisnya bukan jadwal."""
    from puspa.scheduler.scheduler import create_trigger

    if trigger.get("type") != "jadwal":
        return None
    berikutnya = next_trigger_at(trigger, sekarang)
    return create_trigger(
        user_id, "jadwal",
        next_trigger_at=berikutnya,
        jadwal=f"alur:{workflow_id}",
        workflow=workflow_id,
        dsn=dsn,
    )
