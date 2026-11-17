"""Paket Puspa — Fase 3 (Wedge ganda + Moat).

Paket ini berbagi namespace `puspa` dengan base Fase 0, 1, dan 2:
`extend_path` membuat subpaket lama tetap bisa diimpor, sementara
subpaket baru Fase 3 (`puspa.pricing`, `puspa.billing`, `puspa.graphs`,
`puspa.moat`) dibaca dari direktori ini.

Syarat: `sys.path` memuat direktori Fase 3 SEBELUM Fase 2, 1, dan 0
(lihat `tests/conftest.py`). Kebijakan bahasa (perintah Nero 21:53 WIB):
identifier Inggris, nilai domain Indonesia (revisi 7), prosa Indonesia.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
