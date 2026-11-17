# Audit Penamaan — Puspa Fase 0

Tanggal: 17 November 2026. Auditor: Luna (subagen audit penamaan).
Perintah Nero: pastikan penamaan file, README, variabel, dan lain-lain
semuanya best practice, dan JANGAN buat istilah sendiri.

Rujukan glosarium: `~/workspace/match-hunt/definisi-mvp-puspa-2026-11-17-revisi-7.md`
(revisi 7, DISETUJUI). Definisi dan checklist TIDAK diubah — hanya rujukan.
Aturan: PEP 8; istilah domain Indonesia harus ada di revisi 7; satu konsep =
satu nama di semua file.

## Ringkasan

- Temuan mentah dari tiga inventarisasi (paket puspa, tes+demo, dokumen+SQL):
  **127 temuan** (18 dokumen+SQL, 45 paket puspa, 64 tes+demo), dengan
  tumpang tindih antar laporan (typo dan angka tes yang sama ditemukan dua
  laporan). Setiap temuan diverifikasi satu per satu sebelum diputuskan.
- **Yang sudah diperbaiki langsung: 47** (typo, wording dokumen, komentar,
  docstring, rename variabel/konstanta/fungsi internal — semua terverifikasi
  pytest + demo dry-run, lihat § Bukti).
- **Usulan rename yang menunggu keputusan Nero: 22 butir** (P-1 s.d. P-22;
  tiap butir membundel beberapa temuan mentah) — modul/file/API publik/
  nama tabel/nama event tidak diubah tanpa persetujuan.
- **Ditolak setelah verifikasi (bukan temuan): 8** (salah hitung auditor,
  rujukan baris salah, atau pola yang memang disengaja).
- **Diterima sebagai pola yang disengaja: 4** (prosa Indonesia + identifier
  Inggris; `isi` vs `content` di domain berbeda; ejaan `sub_agen` ganda
  yang berasal dari revisi 7; `CannedProvider` sebagai test double demo).
- Sisa temuan mentah (terutama penamaan tes dan kunci JSON) sudah
  dibundel ke dalam butir usulan — semuanya menunggu satu keputusan besar
  dulu: kebijakan bahasa identifier (lihat § Keputusan yang dibutuhkan).

## Keputusan yang dibutuhkan (paling penting)

1. **Kebijakan bahasa identifier.** Kode sekarang mencampur Indonesia dan
   Inggris di tiga lapis: nama publik (`append_event`, `Ledger`, `Gate`),
   nama domain (`tugas_id`, `kejadian`, `rupiah_max`), dan nama tes
   (`test_..._gagal_jujur`). Tanpa kebijakan tertulis, rename sebagian
   hanya menambah varian ketiga. Usulan: **identifier kode = Inggris**
   (konsep teknis standar + nama publik), **nilai domain yang disimpan**
   (jenis kejadian, kolom SQL, status) **tetap Indonesia sesuai revisi 7**,
   **prosa (docstring/komentar/dokumen) = Indonesia**. Keputusan ini
   membuka jalan untuk butir-butir usulan di bawah.
2. **Rename publik besar** (lihat § Usulan, P-1 s.d. P-12): `canonical_json`
   ganda, alias `EVENT_TYPES`, `append_event`/`events`, dua `skills.py`,
   dua modul `session`, dan nama-nama SQL campuran. Semuanya butuh kata
   Nero karena menyentuh API publik / repo yang sudah di-push.
3. **Mock `baca_file` vs tool asli `read_file`** (F-11): mock harness
   memakai tool dummy `baca_file` sementara demo memakai `read_file`.
   Bukti protokol tetap sah (nama tool di mock bebas), tapi bukti jadi
   kurang langsung dibandingkan. Mengganti berarti regenerate 9 file
   `mock_logs/` + update 2 dokumen + tes. Menunggu kata Nero.
4. **`requirements.txt` tanpa version pin** (observasi, bukan temuan
   penamaan): fresh-clone install bisa berbeda dari VM yang dites.
   Disarankan pin saat push berikutnya.

## Yang sudah diperbaiki

### Typo (6)

| Lokasi | Sebelum | Sesudah |
| --- | --- | --- |
| `demo/MATRIKS_BYOK.md:159` | `orcarouter` | `openrouter` |
| `demo/NOTES.md:220` | `Autouser` | `Autouse` |
| `demo/byok_demo.py:646` | kunci JSON `"diexport_pada"` | `"diekspor_pada"` (tidak ada konsumen yang membaca kunci ini) |
| `puspa/policy/spending.py:10,20,28,93` | `instansi` (4x, docstring) | `instans` |
| `puspa/quarantine/injection.py:93` | `didedup` (docstring) | `dideduplikasi` |
| `puspa/accounts/users.py:53` | `"password kosong"` | `"kata sandi kosong"` (tidak ada tes yang assert pesan ini) |

### Istilah salah pakai (3)

- `puspa/policy/gate.py:15,256` — "pemeriksa batas" (istilah `Pemeriksa`
  adalah class sub-agen di revisi 7, bukan penegak batas belanja) →
  "penegak batas belanja".
- `puspa/accounts/access.py:133` — komentar "Pembaca bermilik" (istilah
  `Pembaca` adalah class sub-agen) → "fungsi baca bermilik (scoped
  readers)"; frasa yang sama di `CONTRACTS.md:116-117` ikut diperbaiki.
- `demo/NOTES.md:317` — status `menunggu_persetujuan` (tidak ada di kode;
  status gerbang yang benar adalah `diminta`) → "(persetujuan berstatus
  `diminta`)".

### Dokumen (16)

- `README.md` — angka tes 203 → **244** + rincian +41 tes BYOK; baris
  "push hanya setelah parent mengonfirmasi" (sudah basi, push sudah terjadi
  17 Nov 2026) → komit `8e91a92` + `657ee78`; tambah seksi **Istilah**
  (Puspa, MVP, Buku Kerja, Pembaca terkarantina, token izin, gerbang,
  Ed25519, Argon2id, Fernet, RLS, DSN, HKUDS, chassis).
- `CONTRACTS.md` — layout tree dilengkapi (`test_byok_provider.py`,
  `demo/`, `NOTICE.md`, `nanobot-INVENTORY.md`); kalimat "Semua tabel
  aplikasi punya `user_id`" diperbaiki (pengguna PK-nya `id`; kejadian
  memakai `agen_id` + `tugas_id`); urutan rantai `id` → `(waktu, id)`
  (sesuai implementasi + catatan penyimpangan yang sudah ada).
- `NOTICE.md:11` — "Versi yang di-vendor" → "Versi yang dipakai sebagai
  referensi (clone lokal, belum di-vendor)" (selaras dengan README).
- `STATUS.md` — baris 6 diberi anotasi "(7 jalur awal; jalur ke-8 di baris
  11)" agar angka 7 vs 8 tidak dibaca bertentangan; tambah paragraf suite
  sesudah generalisasi BYOK (244 passed, 17 Nov 2026 ~19:33 WIB). Baris
  bersejarah (203 passed ~18:38 WIB, 7 jalur) TIDAK diubah — benar pada
  waktunya.
- `demo/MATRIKS_BYOK.md` — definisi BYOK (Bring Your Own Key) + surrogate
  (`hsurr:...`) saat pertama muncul; konvensi nama provider (tabel =
  nilai kode huruf kecil; prosa = bentuk baku OpenRouter); catatan
  bedrock tidak punya file log mock (fake client, bukan HTTP).
- `demo/LAPORAN_DEMO.md` — definisi BYOK + surrogate; judul "Run terakhir
  (e3416931)" → "Run e3416931" (f1c46b9d lebih baru); konvensi "file tanpa
  suffix run-id = bukti run e3416931; run lain pakai suffix `-<run_id>`".
- `demo/NOTES.md` — lihat typo + istilah di atas.
- `demo/byok_provider_proof.md:72` — `get_surrogate()` → `get_openrouter_surrogate()` (nama fungsi sudah di-rename; rujukan di dokumen proof ikut diperbarui).

### Duplikasi helper (1)

- `puspa/policy/skills.py` — `_b64url_encode`/`_b64url_decode` duplikat
  identik dengan `puspa/policy/tokens.py` (bukti: badan fungsi sama persis)
  → dihapus dari `skills.py`, diimpor dari `.tokens`. Tidak ada perubahan
  API publik (`__all__` tidak memuat keduanya).

### Rename internal terverifikasi (21)

| Lokasi | Sebelum | Sesudah | Alasan |
| --- | --- | --- | --- |
| `demo/byok_provider.py:506` | alias `build_provider = build_byok_provider` | dihapus | tidak diimpor siapa pun (semua impor memakai `build_byok_provider`); nama ini bentrok dengan seam demo di `byok_demo.py:320` |
| `demo/byok_demo.py:320,454` | `build_provider` (seam demo) | `build_demo_provider` | bedakan dari factory `build_byok_provider` |
| `demo/openrouter_provider.py:38` + `demo/byok_provider.py:263,265` | `get_surrogate` | `get_openrouter_surrogate` (impor di `byok_provider` sebagai `_openrouter_surrogate`) | satu nama di kedua modul |
| `demo/byok_provider.py:397-493` | lokal `nama` | `provider_name` | Inggris, konsisten dengan `provider_name=` di konstruktor provider |
| `demo/byok_provider.py:397-493` | lokal `kunci` | `key` | Inggris, konsisten dengan parameter `key` |
| `demo/byok_provider.py:193-195` | `awalan, nama` (loop var) | `prefix, provider_name` | Inggris |
| `demo/byok_provider.py:205,217` | lokal `nama` di `_canonical` | `name` | samakan dengan parameter `name` |
| `demo/byok_provider.py:239,242-243` | lokal `nama` di `register_provider` | `canonical_name` | hindari bayang-bayang parameter `name` yang masih dipakai pesan error |
| `demo/byok_provider.py:307-326` | lokal `nama` di `resolve_key` | `provider_name` | Inggris |
| `demo/byok_provider.py:460` | string error `register_provider(nama, base_url)` | `register_provider(name, base_url)` | parameter aslinya bernama `name` |
| `demo/byok_demo.py` | `PENGHITUNG` (9x) | `penghitung` | UPPER_SNAKE salah untuk objek mutable |
| `demo/byok_demo.py` | `USAGE_PERMINTAAN` (6x) | `usage_permintaan` | sama |
| `demo/byok_demo.py` | `FASE0`, `DEMO_DIR` (publik) | `_FASE0`, `_DEMO_DIR` | privat, selaras dengan `byok_provider.py` + `test_byok_provider.py` |
| `demo/byok_demo.py:646` | kunci JSON `"diexport_pada"` (sudah dihitung di § Typo) | — | — |
| `demo/mock_provider_server.py:64-65,274,297,320,342` | `MESSAGES`, `TOOLS` | `MOCK_MESSAGES`, `MOCK_TOOLS` | deskriptif; tidak diimpor tes |
| `tests/test_quarantine.py` | `ALLOWED` | `ALLOWED_DOMAINS` | deskriptif |
| `tests/conftest.py:28-30` | `_nanobot_local` | `_NANOBOT_LOCAL` | UPPER_SNAKE untuk konstanta path; selaras dengan `demo/` |
| `tests/test_byok_provider.py:70,149,154` | `gagal_jujur` (3 nama tes) | `gagal_terlihat` | revisi 7 §2.6 memakai istilah "gagal terlihat" untuk kegagalan yang jujur |
| `puspa/accounts/users.py:25,54,88` | `_HASHER` | `_PASSWORD_HASHER` | deskriptif |
| `puspa/policy/gate.py:113,116,128,246,249,251` | parameter `void` | `invalidate` | `void` bukan kata yang menyatakan makna (batalkan persetujuan) |
| `puspa/quarantine/injection.py:36,86` | `_POLAS` | `_PATTERNS` | Inggris, konsisten dengan field `nama pola` yang tetap Indonesia di data |
| `puspa/quarantine/reader.py:29,95` | `_NUM_RE` | `_NUMBER_RE` | singkatan tidak jelas |

Catatan: rename `nama`/`kunci` di `build_byok_provider` dilakukan dengan
`tokenize` (bukan sed) supaya string literal prosa ("model = nama
deployment", "register_provider(nama, base_url)") tidak ikut berubah;
diff diperiksa baris per baris sebelum pytest.

## Usulan rename yang menunggu keputusan

Tidak ada yang diubah — semua butuh kata Nero karena menyentuh API publik,
file, tabel, atau repo yang sudah di-push.

| # | Usulan | Alasan | Dampak |
| --- | --- | --- | --- |
| P-1 | `canonical_json` ganda: `ledger.py:84` (-> `str`) vs `tokens.py:75` (-> `bytes`) | satu nama, dua tipe kembalian; keduanya diekspor di `__all__` | rename salah satu (mis. `canonical_json_bytes` di tokens) + 9 call site (ledger 2, tokens 4, gate 1, skills 2) |
| P-2 | Hapus alias `EVENT_TYPES = JENIS_KEJADIAN` (`ledger.py:81`) | satu konsep dua nama | 1 baris + semua importir alias |
| P-3 | `append_event`/`events`/`InvalidEventTypeError`/`compute_event_hash` → `tambah_kejadian`/`kejadian`/... ATAU sebaliknya samakan ke Inggris | `kejadian` vs `event` bercampur di API publik ledger | API publik ledger + semua pemakai (chassis, tes, demo) |
| P-4 | `read_ledger_events_for_task` vs `revoke_all_for_tugas` | `task` vs `tugas` di nama fungsi publik | 2 fungsi + pemakai |
| P-5 | `create_agent_schema`/`register_agent` vs `agen_id` | `agent` vs `agen` di API publik accounts | 2 fungsi + pemakai + SQL |
| P-6 | Uang: `rupiah`/`nominal`/`amount`/`jumlah` (4 nama) | satu konsep empat nama | token payload, gate, spending, tes |
| P-7 | `KIND_TOOLS`/`KIND_SCOPES` + status tool (`ok`/`error` vs `ditolak`/`menunggu_persetujuan`) | campur bahasa di nilai data | nilai yang disimpan di ledger — butuh migrasi/kompat |
| P-8 | Dua `skills.py` (policy vs chassis) + dua modul session (accounts/sessions.py vs chassis/session.py) | nama file sama, isi beda | rename file + semua impor |
| P-9 | `is_allowed` ganda (method + fungsi modul), `owner_of` vs `owner_of_token`, `validate` vs `verify` | nama mirip, makna beda | API publik policy/accounts |
| P-10 | SQL: kolom campuran (`vault.name`/`ciphertext` Inggris vs `dibuat`/`diubah` Indonesia; `sesi.device_label`/`token_hash` vs `terakhir_dilihat`/`dicabut`) + constraint `kejadian_jenis_check` | satu tabel dua bahasa | migrasi SQL + semua query |
| P-11 | File md: `nanobot-INVENTORY.md`, `demo/byok_provider_proof.md` (huruf kecil) vs konvensi huruf besar (README, STATUS, CONTRACTS, NOTICE) | konsistensi casing nama file | rename file + semua rujukan |
| P-12 | `RlsError` → `RLSError`; `_TINGKAT_RANK` (sisa — `_POLAS` dan `_NUM_RE` sudah diperbaiki, `void` sudah diperbaiki) | akronim PEP 8 (CapWords) | class exception + tes; `_TINGKAT_RANK` butuh keputusan bareng field `tingkat` |
| P-13 | Nama tes: campur Indonesia/Inggris antar file + prefix `j1`-`j8` + `A`/`B` di `test_attacks.py` + tiga rumusan untuk tes jenis tak dikenal yang sama | satu konvensi penamaan tes | 219 fungsi tes — tunggu kebijakan bahasa (§ Keputusan 1) |
| P-14 | `_payload` didefinisikan 4x dengan `rupiah_max` berbeda (0 / 2.000 / 1.000.000 / 10.000.000) + helper duplikat (`stub_log` 3x, `_token`/`_gate` 2x, `KirimTool` 2x) | fixture duplikat, nilai tidak konsisten | refactor tes (bukan rename semata) |
| P-15 | `DEFAULT_DSN` duplikat (`ledger.py:40`, `users.py:14`) + 3 nama DSN + literal DSN duplikat di tes | satu konstanta, satu tempat | refactor kecil |
| P-16 | `MAX_CHARS` (reader, 200) vs `_MAX_SPAN` (injection, 200) | dua nama untuk batas yang sama | samakan nama + dokumentasikan |
| P-17 | `spent_in_period` di `spending.py` mendelegasikan ke ledger yang tidak punya metodenya (fallback in-memory via `hasattr`) | perilaku benar tapi arsitektur menggantung | putuskan: tambah `spent_in_period` ke Ledger, atau hapus delegasi |
| P-18 | `is_subset`, `owner_of`, `build_hook` tidak diekspor di `__init__` padahal dipakai lintas paket | ekspor `__init__` tidak selaras dengan pemakaian | tambah ekspor / dokumentasikan privat |
| P-19 | `turn`, `sink`/`make_ledger_sink`, `chassis`/`PuspaChassis`, `Galat` (docstring) vs `*Error` (class), `salinan`, `hilang`, `awalan`, `judul`/`angka`/`konteks`, `ringkasan`, `jalankan_kode`, `_tolak`/`alasan`/`daftar_scope`, `pola`/`tingkat`/`span` | istilah baru yang tidak ada di revisi 7 dan bukan istilah teknis standar | masing-masing butuh: masukkan ke glosarium revisi 8, atau ganti ke istilah standar |
| P-20 | `BYOK` tidak ada di revisi 7 (tidak pernah diekspansi di file demo sebelum audit ini) + `surrogate` (`hsurr:...`) tidak dijelaskan | istilah baru di demo | sudah dijelaskan di MATRIKS_BYOK.md + LAPORAN_DEMO.md oleh audit ini; masukkan ke glosarium revisi 8 bila BYOK jadi bagian produk |
| P-21 | `baca_file` (mock) vs `read_file` (asli) — F-11 | bukti mock kurang langsung dibandingkan | regenerate 9 `mock_logs/` + 2 dokumen + tes |
| P-22 | Kunci JSON `biaya_demo.json` campur bahasa + check-id campur bahasa (`cek_a` vs `check_b`) + `kalengan` vs `canned` di nama class | konsistensi bahasa di artefak demo | regenerate bukti demo / rename class (tunggu kebijakan bahasa) |

## Temuan yang DITOLAK setelah verifikasi (bukan temuan)

| # | Klaim auditor | Hasil verifikasi |
| --- | --- | --- |
| R-1 | F-26: "41 tes / 244 passed" salah, yang benar 32 fungsi / 219 fungsi | SALAH. `pytest --collect-only` mengumpulkan **41 tes** dari `test_byok_provider.py` (32 fungsi, 2 di antaranya terparametrisasi) dan **248 total** (244 passed + 4 skipped). LAPORAN_DEMO benar. |
| R-2 | `spent_in_period` memanggil metode Ledger yang tidak ada (`spending.py:233-239`) | SALAH. File hanya 105 baris; kode di `:72-80` memakai `hasattr` guard + fallback in-memory. Perilaku benar. Lihat P-17 untuk sisi arsitekturnya. |
| R-3 | README menyebut Pemeriksa dan Kantor tanpa definisi | SALAH. `grep` membuktikan README tidak memuat kedua kata itu sama sekali. Keduanya muncul di CONTRACTS/STATUS/kode sesuai definisi revisi 7. |
| R-4 | STATUS/ README/ CONTRACTS bertentangan soal 7 vs 8 jalur serangan | BUKAN pertentangan. Baris 6 STATUS adalah catatan bersejarah (7 jalur awal oleh subagen F); jalur ke-8 dicatat di baris 11. Diberi anotasi penjelas, angka bersejarah tidak diubah. |
| R-5 | `tombstone`/`fail-closed` istilah baru | DITOLAK. Keduanya didefinisikan di revisi 7. |
| R-6 | 31 jenis kejadian tidak cocok dengan definisi | DITOLAK. Skrip membandingkan `ledger.py`, kedua blok CHECK di `schema.sql`, CONTRACTS.md, dan definisi 2.3 — cocok persis 31 jenis. |
| R-7 | 6 policy RLS tidak cocok dengan klaim | DITOLAK. Dihitung langsung dari `schema.sql` — 6 policy, cocok. |
| R-8 | Aritmetika tes 203 salah | DITOLAK. 105+5+26+21+12+7+26+1 = 203, benar pada waktunya; kini 244 (+41 BYOK). |

## Pola yang diterima (tidak diubah)

- **Prosa Indonesia + identifier Inggris.** Komentar/docstring/dokumen
  berbahasa Indonesia, nama kode berbahasa Inggris untuk konsep teknis
  standar — ini pola yang konsisten di seluruh tree, bukan inkonsistensi.
  (`CannedProvider`, `canned-...`, `menunggu_persetujuan` sebagai nilai
  status internal demo, `isi` vs `content` di domain berbeda.)
- **Istilah revisi 7 dipakai apa adanya:** Buku Kerja, Pemeriksa, Pembaca
  terkarantina, alur, modul, Kantor, Puspa, Rekor Otonomi, kejadian, tugas,
  token izin, gerbang, batas belanja, angka yatim, vonis, kaset, pemicu,
  handoff, detak. Jangan diusulkan diganti — yang dijaga adalah
  konsistensi pemakaiannya.
- **Dua ejaan `sub_agen` vs `subagen`:** keduanya berasal dari revisi 7
  sendiri (definisi memakai keduanya di bagian berbeda). Perlu dibetulkan
  di revisi 8 definisi, bukan di kode.
- **`requirements.txt` tanpa version pin** dicatat sebagai observasi
  (§ Keputusan 4), bukan temuan penamaan.

## Bukti

- Baseline sebelum perbaikan: 244 passed, 4 skipped (suite solo,
  17 Nov 2026 ~19:33 WIB).
- Sesudah perbaikan: **244 passed, 4 skipped in 37.83s** (suite solo,
  `.venv/bin/python -m pytest tests -q`, 17 Nov 2026 ~21:50 WIB) — sama
  dengan baseline; tidak ada tes yang berubah hasil.
- Demo dry-run sesudah rename: **7/7 cek lolos** (run fe543761,
  mode=dry-run, provider=canned, 4.5 dtk, 17 Nov 2026 ~21:51 WIB;
  bukti `demo/verifikasi_demo-fe543761.json`). File bukti run ini memakai
  suffix `-fe543761`, jadi file bukti e3416931 (tanpa suffix) tidak tertimpa.
- Postgres sempat hilang total dari VM (paket apt lenyap setelah restart);
  dipulihkan dengan `apt-get update` + `apt-get install -y postgresql-16`
  + `pg_ctlcluster 16 main start` + `infra/setup_db.sql` (sesuai README).

## Batasan audit

- TIDAK ada rename modul/file/API publik/nama tabel/nama event yang
  dilakukan — semuanya di § Usulan.
- TIDAK ada push ke GitHub. TIDAK ada fase 1 yang dimulai. Definisi revisi
  7 dan checklist tidak diubah.
- 4 tes live-container tetap skip (Docker tidak bisa jalan di VM ini);
  bukan temuan penamaan.
