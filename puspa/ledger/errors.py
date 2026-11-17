"""Galat untuk paket Buku Kerja (`puspa.ledger`)."""


class LedgerError(Exception):
    """Galat umum Buku Kerja."""


class InvalidEventTypeError(LedgerError):
    """`jenis` kejadian di luar himpunan tertutup (definisi 2.3)."""

    def __init__(self, jenis):
        super().__init__(
            f"jenis kejadian tidak dikenal: {jenis!r} "
            "(harus salah satu dari 31 jenis tertutup di definisi 2.3)"
        )
        self.jenis = jenis
