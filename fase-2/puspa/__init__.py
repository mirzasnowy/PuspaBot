"""Paket Puspa — Fase 2 (Runtime tim + PWA + Konektor + Penjadwal).

Paket ini berbagi namespace `puspa` dengan base Fase 0 dan Fase 1:
`extend_path` membuat subpaket Fase 0 (`puspa.ledger`, `puspa.accounts`,
...) dan Fase 1 (`puspa.memory`, `puspa.auth`, ...) tetap bisa diimpor,
sementara subpaket baru Fase 2 (`puspa.runtime`, `puspa.evidence`,
`puspa.router`, `puspa.connectors`, `puspa.scheduler`, `puspa.push`,
`puspa.cassette`, `puspa.pwa`) dibaca dari direktori ini.

Syarat: `sys.path` memuat direktori Fase 2 SEBELUM Fase 1 dan Fase 0
(lihat `tests/conftest.py`). Kebijakan bahasa (perintah Nero 21:53 WIB):
identifier Inggris, nilai domain Indonesia (revisi 7), prosa Indonesia.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
