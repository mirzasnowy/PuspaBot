"""Paket Puspa — Fase 4 (Alur + Modul kustom).

Paket ini berbagi namespace `puspa` dengan base Fase 0, 1, 2, dan 3:
`extend_path` membuat subpaket lama tetap bisa diimpor, sementara
subpaket baru Fase 4 (`puspa.workflow`, `puspa.modules`) dibaca dari
direktori ini.

Syarat: `sys.path` memuat direktori Fase 4 SEBELUM Fase 3, 2, 1, dan 0
(lihat `tests/conftest.py`). Kebijakan bahasa (perintah Nero 21:53 WIB):
identifier Inggris, nilai domain Indonesia (revisi 7), prosa Indonesia.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
