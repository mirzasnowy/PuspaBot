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

TRIGGER_TYPES = ("schedule", "due_date", "price_drop", "manual")
# Pemicu yang sengaja ditolak di MVP (§2.12, keputusan 18).
TRIGGER_REJECTED = ("email_login", "webhook")
MIN_INTERVAL_HOURS = 1
WEEKDAYS = ("senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "week")


class TriggerError(Exception):
    """Pemicu tidak valid atau di luar MVP."""


def validate_trigger(trigger: dict) -> list[str]:
    """Periksa satu pemicu. Kembalikan daftar masalah (kosong = lolos)."""
    problems: list[str] = []
    if not isinstance(trigger, dict):
        return ["pemicu harus objek"]
    event_type = trigger.get("type")
    if event_type in TRIGGER_REJECTED:
        return [f"pemicu {event_type!r} sesudah MVP (keputusan 18) — tidak dibangun"]
    if event_type not in TRIGGER_TYPES:
        return [f"jenis pemicu {event_type!r} tidak dikenal"]
    if event_type == "schedule":
        mode = trigger.get("mode")
        if mode == "date":
            day = trigger.get("date")
            if not isinstance(day, int) or not 1 <= day <= 31:
                problems.append("pemicu jadwal tanggal: 'tanggal' harus 1-31")
        elif mode == "day":
            if trigger.get("day") not in WEEKDAYS:
                problems.append(f"pemicu jadwal hari: 'hari' harus salah satu {WEEKDAYS}")
        elif mode == "interval_hour":
            hour = trigger.get("hour")
            if not isinstance(hour, (int, float)) or hour < MIN_INTERVAL_HOURS:
                problems.append(
                    f"pemicu jadwal interval: minimal {MIN_INTERVAL_HOURS} jam")
        else:
            problems.append("pemicu jadwal: 'mode' harus tanggal/hari/interval_hour")
        start_time = trigger.get("start_time", "07:00")
        try:
            time.fromisoformat(str(start_time))
        except ValueError:
            problems.append(f"pemicu jadwal: 'jam_mulai' {start_time!r} bukan jam valid")
    elif event_type == "due_date":
        day = trigger.get("days_before")
        if not isinstance(day, int) or day < 0:
            problems.append("pemicu due_date: 'day_sebelum' harus bilangan >= 0")
        if trigger.get("provider") not in ("PLN", "PDAM", "airtime", "paket data"):
            problems.append("pemicu due_date: 'provider' harus provider MVP")
    elif event_type == "price_drop":
        if not trigger.get("product_id"):
            problems.append("pemicu price_turun: 'product_id' wajib (dari watchlist)")
        threshold = trigger.get("price_threshold")
        if not isinstance(threshold, (int, float)) or threshold <= 0:
            problems.append("pemicu price_turun: 'price_threshold' harus Rupiah > 0")
    return problems


def _start_hour(trigger: dict) -> time:
    return time.fromisoformat(str(trigger.get("start_time", "07:00")))


def next_trigger_at(trigger: dict, now: datetime | None = None) -> datetime:
    """Hitung waktu picu berikutnya untuk pemicu jadwal. `manual`,
    `jatuh_tempo`, dan `harga_turun` tidak punya jadwal tetap — mereka
    dievaluasi saat data berubah (lihat `cocok_jatuh_tempo` dan
    `cocok_harga_turun`); fungsi ini mengembalikan None untuk ketiganya.
    """
    if trigger.get("type") != "schedule":
        return None
    now = now or datetime.now(timezone.utc)
    hour = _start_hour(trigger)
    mode = trigger.get("mode")
    if mode == "interval_hour":
        return now + timedelta(hours=float(trigger["hour"]))
    if mode == "day":
        target = WEEKDAYS.index(trigger["day"])  # senin = 0
        candidate = datetime.combine(now.date(), hour, tzinfo=timezone.utc)
        delta_days = (target - now.weekday()) % 7
        candidate += timedelta(days=delta_days)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate
    # mode tanggal: tanggal yang sama tiap bulan.
    day = int(trigger["date"])
    tahun, month = now.year, now.month
    for _ in range(2):  # bulan ini, kalau lewat bulan depan
        try:
            candidate = datetime.combine(
                date(tahun, month, day), hour, tzinfo=timezone.utc)
        except ValueError:
            # tanggal 31 di bulan 30 hari: geser ke akhir bulan.
            if month == 12:
                tahun, month = tahun + 1, 1
            else:
                month += 1
            continue
        if candidate > now:
            return candidate
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
        delta_days = (due - today).days
        if 0 <= delta_days <= int(trigger.get("days_before", 3)):
            match.append(t)
    return match


def match_price_drop(trigger: dict, current_price: int | None) -> bool:
    """Benar bila harga kini (dari watchlist/sampler Fase 3) sudah di
    bawah atau sama dengan ambang. `harga_kini` None = belum ada data
    (tidak memicu — gagal terlihat, bukan tebak)."""
    if current_price is None:
        return False
    return int(current_price) <= int(trigger["price_threshold"])


def save_to_scheduler(*, user_id: str, workflow_id: str, trigger: dict,
                        now: datetime | None = None, dsn=None):
    """Simpan pemicu jadwal ke tabel pemicu Fase 2 (dipakai ulang).
    Pemicu jatuh_tempo / harga_turun / manual tidak punya jadwal tetap
    sehingga tidak disimpan di sini — mereka dievaluasi tiap tick oleh
    `evaluasi_pemicu_data` di runner. Kembalikan Pemicu Fase 2 atau
    None bila jenisnya bukan jadwal."""
    from puspa.scheduler.scheduler import create_trigger

    if trigger.get("type") != "schedule":
        return None
    next_time = next_trigger_at(trigger, now)
    return create_trigger(
        user_id, "schedule",
        next_trigger_at=next_time,
        schedule=f"alur:{workflow_id}",
        workflow=workflow_id,
        dsn=dsn,
    )
