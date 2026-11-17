# Puspa — Fase 0 (Keamanan + tulang punggung)

Potongan fondasional MVP Puspa (definisi revisi 7, disetujui Nero 17 Nov 2026).
Periode Fase 0: 18 Nov - 1 Des 2026. Status rinci: lihat `STATUS.md`.
Kontrak antar-paket: lihat `CONTRACTS.md`.

## Repo

Repo Puspa adalah repo **standalone, BUKAN fork**: **mirzasnowy/puspa-ai**
(https://github.com/mirzasnowy/puspa-ai, publik, branch main).
Fase 0 sudah di-push ke sana 17 Nov 2026 (komit `8e91a92` +
`657ee78`); `nanobot/` tidak ikut di-push (hanya referensi baca lokal).
**Aturan tanpa-fork (Nero):** jangan pernah membuat fork GitHub dari repo mana pun.
Kode orang dipakai sebagai dependency pip, atau di-vendor dengan file LICENSE
asli + atribusi (README dan NOTICE). Lisensi non-komersial/copyleft (GPL,
CC BY-NC-SA, PolyForm Noncommercial) jangan dipakai.

## Isi

- `nanobot/` — clone lokal referensi baca dari HKUDS/nanobot (HEAD e767174,
  MIT). Bukan base repo, belum ada kode yang di-vendor. Inventaris:
  `nanobot-INVENTORY.md`. Dari fresh clone, nanobot dipakai dari dependency
  git pin di `requirements.txt` (`nanobot-ai @ git+https://github.com/HKUDS/nanobot@e767174`).
- `puspa/ledger/` — Buku Kerja: tabel Postgres append-only `kejadian`,
  rantai hash per tugas, 31 jenis kejadian tertutup.
- `puspa/policy/` — token izin Ed25519 + subset, skill bertanda tangan,
  gerbang tunggal aksi keluar, batas belanja (default Rp0).
- `puspa/accounts/` — login email + Argon2id, sesi per perangkat (hanya
  sha256 token yang disimpan), vault Fernet per pengguna, Row Level
  Security Postgres (6 policy, penegakan di peran `puspa_app`),
  pemeriksaan kepemilikan lintas pengguna (`require_owner` + pembaca
  bermilik + registry agen). Login kata sandi digantikan login Google di
  fase 1; sesi + vault dipakai ulang.
- `puspa/quarantine/` — Pembaca terkarantina (skema ketat, `[DATA: ...]`,
  maks 200 karakter) + pemindai prompt injection (ID+EN).
- `puspa/sandbox/` — sandbox Docker fail-closed (allowlist jaringan, tanpa
  fallback eksekusi di host).
- `puspa/chassis/` — integrasi dengan nanobot sebagai chassis: token izin
  turn (ContextVar + `RequestContext`), `PuspaTool` (cek token + skill +
  gerbang + audit), `PuspaRegistry` (bungkus otomatis + backstop
  `prepare_call`), `PuspaHook` (siklus tugas ke Buku Kerja),
  `PuspaSkillsLoader` (skill workspace/plugin wajib bertanda tangan),
  `bind_turn` (sesi login -> token izin agen), `SandboxTool`
  (`jalankan_kode`), `PuspaChassis` + `assemble` (composition root).
  NOL baris kode nanobot diubah.
- `tests/` — 244 tes lolos + 4 skip (tes live-container, butuh host Docker
  normal; lihat STATUS.md soal batasan VM ini). Rinci: 105 fondasional +
  5 tes celah `check_tool` + 26 tes integrasi (`tests/test_integration.py`) +
  21 tes jenis kejadian ledger + 12 tes RLS + 7 tes vault + 26 tes akses
  lintas pengguna (23 di `tests/test_access.py` + 3 jalur 8 di
  `tests/test_attacks.py`) + 1 tes efek samping `resolve_session_as` +
  41 tes factory BYOK generik + mock harness (`tests/test_byok_provider.py`).

## Cara jalan tes

```bash
cd build/fase-0
.venv/bin/python -m pytest tests -q
```

Butuh Postgres lokal jalan (cluster 16/main, DB `puspa_test`, role `root`,
peer auth; DSN bisa dioverride env `PUSPA_TEST_DSN`) dan master key vault
di env `PUSPA_VAULT_MASTER_KEY` untuk tes vault (diatur otomatis oleh
fixture tes). Tanpa Postgres, tes ledger/accounts gagal.

## Setup database

Satu perintah ini menyiapkan semuanya (idempotent — aman dijalankan
ulang), sebagai superuser Postgres, dari direktori ini:

```bash
psql "dbname=postgres user=root host=/var/run/postgresql" -f infra/setup_db.sql
```

(Varian `sudo -u postgres psql -f ...` juga bisa, tapi file harus bisa
dibaca user postgres — salin dulu ke `/tmp` kalau permission workspace
menolak.)

Yang dilakukan `infra/setup_db.sql`: buat role `root` (superuser, peer
auth) dan `puspa_app` (peran aplikasi yang dikenai RLS) kalau belum ada,
buat database `puspa_test` kalau belum ada, lalu terapkan skema ledger
(tabel `kejadian`) + accounts (tabel pengguna/sesi/vault + policy RLS).
Tidak ada password di mana pun — auth memakai peer auth lewat socket
lokal. Jangan tambahkan password ke file itu.

**Pemulihan setelah VM restart:** Postgres di VM ini tidak tahan
restart — role + database bisa hilang, bahkan paket apt Postgresnya
pun bisa hilang total (terjadi 17 Nov 2026). Kalau `psql` gagal konek /
role hilang, pulihkan dengan urutan ini:

```bash
# 1. Kalau cluster pun hilang (instal ulang paket dulu bila perlu —
#    apt-get update lama, ~10 menit lewat proxy):
DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-16
# 2. Start cluster:
pg_ctlcluster 16 main start
# 3. Jalankan lagi setup (membuat ulang role + DB + skema):
psql "dbname=postgres user=root host=/var/run/postgresql" -f infra/setup_db.sql
# 4. Periksa tes tetap hijau:
.venv/bin/python -m pytest tests -q
```

Cadangan kalau cluster sistem tidak bisa dipakai: Postgres sementara
via pgserver (cluster di `/tmp/pgdata-puspa-test`, perintah start di
`/tmp/pgserver-start.py`), lalu tes dengan
`PUSPA_TEST_DSN="dbname=puspa_test user=root host=/tmp/pgdata-puspa-test"`.
Detail lengkap ada di komentar atas `infra/setup_db.sql`.

Isolasi antar pengguna ditegakkan Postgres (Row Level Security) pada
tabel pengguna/sesi/vault: aplikasi memasang pengguna saat ini per
transaksi lewat `puspa.accounts.rls.as_user(conn, user_id)` — lihat
`puspa/accounts/schema.sql` untuk policy dan keputusan tanpa-FORCE-nya,
dan `tests/test_rls.py` untuk bukti perilakunya. Tabel `agen` (registry
agen -> pemilik) TIDAK dibuat `setup_db.sql` — ia dibuat sesuai kebutuhan
oleh `puspa.accounts.access.create_agent_schema()`.

## Hasil uji serangan (definisi 2.6)

8/8 jalur gagal menembus — lihat `tests/test_attacks.py` + `tests/test_access.py`:
skill unsigned, tool di luar manifes, injeksi konten web/email, baca memori
agen lain, injeksi listing/email yang memerintahkan kirim/bayar (executor
0 kali), sub-agen di luar tokennya, isi diubah setelah approval, pengguna A
membaca data/konektor/kejadian pengguna B lewat API (ditolak fail-closed).
Tidak ada temuan kritis.

## Istilah (dipakai di README ini)

- **Puspa** — agen AI personal open source untuk Indonesia (produk yang
  dibangun repo ini). **MVP** — Minimum Viable Product, versi pertama yang
  diuji ke pengguna (definisi revisi 7).
- **Buku Kerja** — tabel Postgres append-only `kejadian` yang mencatat
  semua yang terjadi, dengan rantai hash per tugas (definisi revisi 7 §2.3).
- **Pembaca terkarantina** — sub-agen yang hanya boleh membaca data luar
  lewat skema ketat dan menandai konten yang mencurigakan sebagai DATA
  (definisi revisi 7 §2.6).
- **Token izin** — izin bertanda tangan (Ed25519) yang membatasi apa yang
  boleh dilakukan satu giliran agen: tool, langkah, token model, dan batas
  belanja. **Gerbang** — satu-satunya jalur aksi keluar (kirim, bayar);
  tanpa token izin + persetujuan, aksi ditolak.
- **Ed25519** — algoritma tanda tangan digital untuk token izin dan skill.
  **Argon2id** — algoritma hash kata sandi untuk login. **Fernet** —
  enkripsi simetris untuk vault per pengguna. **RLS** (Row Level Security)
  — penegakan isolasi antar pengguna di dalam Postgres.
- **DSN** — Data Source Name, alamat koneksi database (di sini Postgres).
  **HKUDS** — Hong Kong University Data Science Lab, pembuat nanobot.
  **Chassis** — kerangka agen yang dipakai ulang (nanobot) dan dibungkus
  lapisan keamanan Puspa; bukan fork, tidak ada baris kodenya yang diubah.
