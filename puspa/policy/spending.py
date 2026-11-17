"""Batas belanja Puspa (definisi 2.6, CONTRACTS.md bagian "Gerbang tunggal").

Default Rp0: pembayaran apa pun diblokir sampai user menaikkan limit
di Pengaturan (API ``set_limit`` sudah ada; UI Pengaturan fase 2).

Persetujuan DAN batas adalah dua cek independen (STATUS.md keputusan
2): persetujuan tidak menggantikan batas, batas tidak menggantikan
persetujuan. Gerbang mengecek keduanya.

Penyimpanan in-memory dan dibagi antar instans (satu proses).
Persistensi (dari ledger) fase 2; ``ledger`` opsional bisa dipasang
nanti bila menyediakan ``spent_in_period(user_id)``.
"""

__all__ = ["SpendingLimit"]

# Batas default (Rupiah). Rp0 = tidak ada pembayaran yang lolos.
DEFAULT_LIMIT = 0

# Penyimpanan bersama antar instans, in-memory.
_limits: dict[str, int] = {}
_spent: dict[str, int] = {}


class SpendingLimit:
    """Batas belanja per pengguna.

    ``SpendingLimit(user_id)`` mengikat instans ke satu pengguna,
    tapi semua pemanggilan juga menerima ``user_id`` eksplisit
    (``set_limit(user_id, rupiah)`` / ``set_limit(rupiah)``).
    """

    def __init__(self, user_id=None, ledger=None):
        self.user_id = user_id
        self._ledger = ledger

    # -- util ---------------------------------------------------------
    @staticmethod
    def _split_user_amount(user_id, amount, default_user):
        """Dukung set_limit(rupiah) maupun set_limit(user_id, rupiah)."""
        if amount is None:
            # Dipanggil dengan satu argumen angka sebagai user_id.
            if isinstance(user_id, (int, float)) and not isinstance(user_id, bool):
                return default_user, int(user_id)
            raise TypeError("set_limit butuh nominal rupiah")
        if user_id is None:
            user_id = default_user
        if user_id is None:
            raise TypeError("set_limit butuh user_id")
        return user_id, int(amount)

    def _resolve_user(self, user_id):
        uid = user_id if user_id is not None else self.user_id
        if uid is None:
            raise TypeError("butuh user_id")
        return uid

    # -- API kontrak --------------------------------------------------
    def set_limit(self, user_id=None, rupiah=None):
        """Naikkan/atur batas belanja pengguna (hanya user sendiri
        yang boleh; penegakan pemanggil ada di lapisan API)."""
        uid, nominal = self._split_user_amount(user_id, rupiah, self.user_id)
        if nominal < 0:
            raise ValueError("batas belanja tidak boleh negatif")
        _limits[uid] = nominal
        return nominal

    def get_limit(self, user_id=None):
        """Batas saat ini; default Rp0 kalau belum pernah diatur."""
        return _limits.get(self._resolve_user(user_id), DEFAULT_LIMIT)

    def spent_in_period(self, user_id=None):
        """Total Rupiah yang sudah dibelanjakan pengguna di periode
        berjalan. Dari ledger bila tersedia, kalau tidak dari memori.
        """
        uid = self._resolve_user(user_id)
        if self._ledger is not None and hasattr(self._ledger, "spent_in_period"):
            return int(self._ledger.spent_in_period(uid))
        return _spent.get(uid, 0)

    def record_spend(self, user_id=None, nominal=None):
        """Catat pengeluaran (dipakai gerbang setelah bayar lolos,
        atau oleh tes). Dukung record_spend(nominal)."""
        uid, amount = self._split_user_amount(user_id, nominal, self.user_id)
        if amount < 0:
            raise ValueError("nominal tidak boleh negatif")
        _spent[uid] = _spent.get(uid, 0) + amount
        return _spent[uid]

    def check(self, user_id=None, nominal=None):
        """Benar kalau ``nominal + sudah_dibelanjakan <= batas``.

        Dukung check(nominal) untuk instans yang terikat pengguna,
        dan check(user_id, nominal). Nominal 0 selalu lolos selama
        belum ada pengeluaran (0 + 0 <= 0).
        """
        if nominal is None:
            # check(nominal) dengan satu argumen angka.
            if isinstance(user_id, (int, float)) and not isinstance(user_id, bool):
                nominal = user_id
                user_id = self.user_id
            else:
                raise TypeError("check butuh nominal")
        uid = self._resolve_user(user_id)
        return int(nominal) + self.spent_in_period(uid) <= self.get_limit(uid)
