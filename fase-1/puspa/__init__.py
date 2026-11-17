"""Paket Puspa — Fase 1 (Persona + Memori + Onboarding).

Paket ini berbagi namespace `puspa` dengan base Fase 0: `extend_path`
membuat subpaket Fase 0 (`puspa.ledger`, `puspa.accounts`, ...) tetap
bisa diimpor dari direktori Fase 0, sementara subpaket baru Fase 1
(`puspa.memory`, `puspa.persona`, ...) dibaca dari direktori ini.
Syarat: `sys.path` memuat direktori Fase 1 SEBELUM direktori Fase 0
(lihat `tests/conftest.py`).
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
