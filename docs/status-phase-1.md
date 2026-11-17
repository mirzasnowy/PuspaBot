# STATUS — Fase 1 MVP Puspa (Persona + Memori + Onboarding)

Definisi: revisi 7 (disetujui Nero 17 Nov 2026 ~17:42 WIB).
Tanggal di definisi diabaikan atas perintah Nero 21:53 WIB (hanya
urutan, bukan deadline). Kerja lokal di `build/fase-1/`; JANGAN push
ke GitHub; JANGAN mulai Fase 2.

## Angka (semua run SOLO, satu pytest dalam satu waktu)

| Suite | Hasil | Waktu |
| --- | --- | --- |
| Fase 1 (baru) | **58 passed** | 17 Nov 2026 ~15:07 WIB (pasca-pemulihan Postgres, lihat bawah) |
| Fase 0 (verifikasi ulang) | **244 passed, 4 skipped** | 17 Nov 2026 ~15:07 WIB, solo |
| Onboarding sisi sistem (4 langkah) | **0,29 detik** (target 5 menit) | diukur 17 Nov 2026 ~15:07 WIB |
| Backup + restore ke DB bersih | **0,3 detik**, 14 tabel pulih, rantai hash utuh | diukur 17 Nov 2026 ~15:07 WIB |

4 skip Fase 0 = tes live-container Docker (kernel tolak `setns`) —
blokir lingkungan yang sama seperti Fase 0, bukan regresi.

## Yang dibangun (per scope definisi)

### 1. Memori 3 lapis (§2.7) — `puspa/memory/`
- **Profil** (`profile.py`): fakta + merge deterministik UNION;
  asal `diucapkan_user` / `dikonfirmasi_user` / `disimpulkan`;
  fakta disimpulkan masuk Usulan (bukan profil) sampai dikonfirmasi /
  ditolak; fakta baru menggantikan versi lama TANPA menghapus riwayat
  (status `diganti` + `berlaku_sampai`, konflik dikembalikan sebagai
  `konflik_dengan`); berlaku dari/sampai, satu versi berlaku; lihat /
  koreksi / hapus (hapus = status `ditolak`, riwayat tetap ada).
- **Episodik** (`episodic.py`): riwayat chat + tugas, index vektor
  pgvector (`vector(384)` + index HNSW `idx_episodik_embedding`,
  terverifikasi ada di DB); jalur tulis + jalur baca jalan.
  Embedding: `HashEmbedding` deterministik (lihat "Keputusan" di bawah).
- **Prosedural** (`procedural.py`): ditulis OTOMATIS dari tugas yang
  berhasil, tanpa editor; siklus hidup `percobaan` -> `terverifikasi`
  (3 jalan sukses, min 2 agen) -> `rusak` (2 gagal berturut-turut);
  nomor pelanggan tidak pernah masuk prosedur. BATAS: Graf Alur
  Layanan penuh (canary, pakai ulang lintas agen) dibangun Fase 3 —
  di sini jalur tulis + penyimpanan + status dasar.
- **Pelingkupan** (C56): profil inti (agen_id NULL, dipegang Puspa)
  terlihat semua agen; fakta per agen hanya agen itu; episodik /
  prosedur per user + agen; RLS per tabel (diuji sebagai `puspa_app`).
- **Irisan** (`slices.py`, C57): sub-agen tidak menyentuh penyimpanan;
  agen memberi irisan (query maks 500 karakter + kolom tertutup
  {profil, episodik, prosedural} + token_ref); pemberian dicatat di
  tabel audit `pemberian_irisan`. KONFLIK: definisi minta dicatat DI
  BUKU KERJA, tapi himpunan jenis kejadian tertutup (31 jenis) tidak
  punya `irisan_diberikan` — lihat "Konflik" di bawah.
- **Konsolidasi saat sepi** (`consolidate.py`, C60): job deterministik
  (ekstraktif, tanpa model, Rp0) meringkas episodik lama per tugas;
  tiap ringkasan menunjuk `kejadian_ids` asal; pola berhasil
  dipromosikan ke prosedural; episode yang sudah diringkas tidak
  diulang. Pemanggil penjadwal saat idle adalah Fase 2 (2.14).

### 2. Persona (§2.16) — `puspa/persona/`
- 3 contoh gaya (`santai`, `rapi`, `campur`) + opsional tempel 10-20
  pesan sendiri (maks 20, tertua dibuang; tanpa impor chat WhatsApp).
- Koreksi ("lebih santai", "jangan pakai gue", ...) disimpan sebagai
  fakta `dikonfirmasi_user` dan langsung berlaku (`get_persona`).
- Harness uji buta gerbang 3 (`blind_test.py`): 20 item (10 asli +
  10 persona), acak deterministik (seed), lembar uji + penilaian
  (ambang 60%). Contoh jadi di `blind-test-contoh/` (20 item, 10/10).
  UJI MANUSIANYA MENUNGGU NERO (5 teman belum menjawab) — tidak ada
  hasil yang dikarang.

### 3. Onboarding (§2.16) — `puspa/onboarding/`
- 4 langkah: nama+gaya, notif+uji kirim, konektor (boleh nanti),
  tugas pertama terbimbing. Terukur end-to-end: **0,29 detik sisi
  sistem** (waktu manusia membaca/mengetik tidak termasuk — angka ini
  adalah waktu kode, bukan waktu manusia; target 5 menit adalah target
  alur manusia dan baru bisa diukur saat uji pengguna).
- Langkah 2 JUJUR: Web Push Fase 2 belum ada, `StubNotifier`
  melaporkan `terkirim=False` (bukan pura-pura terkirim).
- Langkah 4 STUB TERUKUR: wedge bandingkan harga/tagihan Fase 3 belum
  ada; stub memanggil jalur nyata (validasi, Buku Kerja
  `tugas_dibuat`+`tugas_selesai`, episodik, ukur waktu) dan
  mengembalikan `terbukti=False` + alasan. Tidak ada harga/tagihan
  yang dikarang.
- Sesudah tugas pertama: 3 templat alur bawaan ditawarkan (daftar
  saja; alur kustom Fase 4).

### 4. Login Google (keputusan 16, §2.15) — `puspa/auth/`
- OAuth 2.0 authorization code + PKCE S256, state anti-CSRF, scope
  minimal login (`openid email profile`); tukar code, refresh,
  revoke, verifikasi ID token RS256 (JWKS, iss/aud/exp); token
  disimpan di vault terenkripsi Fase 0 (`google_oauth`); sesi per
  perangkat via `sessions.py` Fase 0 (dipakai ulang, tanpa perubahan).
- Akun Google-only: baris `pengguna` dibuat dengan hash kata sandi
  acak yang tidak diketahui siapa pun (kolom `password_hash` NOT NULL
  di skema Fase 0); login kata sandi tidak dipakai lagi.
- Tautan email cadangan (`email_link.py`): token HMAC sekali pakai,
  TTL 15 menit, replay ditolak.
- BLOKIR: publikasi app ke "In production" butuh browser Nero
  (status Testing = refresh token kedaluwarsa 7 hari). Kode lengkap +
  diuji (HTTP mock + JWKS RSA asli); publikasi = langkah Nero tertunda.

### 5. Cadangan + persetujuan data (§2.17) — `puspa/backup/`, `puspa/consent/`
- Cadangan: `pg_dump -Fc` -> enkripsi Fernet (kunci hanya dari env
  `PUSPA_BACKUP_KEY`, tanpa default) -> `build/fase-1/backups/`
  (di luar DB); retensi 14 hari (prune otomatis); manifest JSON.
  Penjadwalan harian otomatis menunggu penjadwal Fase 2 (2.14).
- Uji restore pertama DIJALANKAN (tes `test_restore_ke_db_bersih` +
  run manual): restore ke DB bersih 0,3 detik, 14 tabel, rantai hash
  Buku Kerja terverifikasi utuh. Bukti: `backups/puspa-20261117-150740.dump.enc`
  (+ manifest `.json`).
- Persetujuan: teks `v1-2026-11-17` (6 hal wajib: data apa, di mana,
  berapa lama, siapa bisa melihat, ekspor/hapus, graf bersama tanpa
  identitas); dicatat di tabel `persetujuan_data` + Buku Kerja
  (jenis `persetujuan_data` sudah ada di ledger).
- Ekspor akun: profil + episodik (tanpa vektor) + prosedur +
  ringkasan + persetujuan + onboarding + daftar sesi — TANPA rahasia
  vault (diuji: token tidak ikut ekspor).
- Hapus akun: menghapus memori/prosedur/contoh/onboarding/
  persetujuan + akun (sesi + vault via `users.delete_user` Fase 0);
  rantai hash tetap utuh (tidak ada UPDATE/DELETE di `kejadian`).
  BATAS: isi kejadian Buku Kerja milik user yang dihapus TETAP ADA —
  lihat "Konflik".

## Keputusan yang dicatat
1. **Embedding gratis: `HashEmbedding` (Rp0).** Deterministik dari
   hash token, tanpa model, tanpa jaringan, tanpa dependensi baru.
   Kualitas semantik terbatas (kemiripan = tumpang-tindih kata).
   Seam pengganti sudah disiapkan: `EpisodicStore` menerima embedder
   apa pun (`embed` + `dim`); kandidat naik kelas: BAAI/bge-small
   lokal via fastembed, atau embedding murah OpenRouter. Ganti dimensi
   butuh migrasi kolom `vector(N)`.
2. **pgvector 0.6.0** diinstal via apt (`postgresql-16-pgvector`);
   ekstensi aktif di `puspa_test`; index HNSW episodik terpasang.
3. **Ringkasan konsolidasi deterministik** (ekstraktif, tanpa model):
   Rp0, bisa diuji. Ringkasan berbasis model = peningkatan Fase 2+.
4. **Tidak ada dependensi baru.** Fase 1 memakai yang sudah ada di
   venv Fase 0 (psycopg, cryptography, argon2-cffi, httpx, pytest).
5. **Kebijakan bahasa** (perintah Nero 21:53 WIB): identifier Inggris,
   nilai domain Indonesia (revisi 7), prosa Indonesia. 22 usulan
   rename P-1–P-22 TIDAK diterapkan (menunggu kata Nero).
6. **Render persona deterministik sementara** (`render_reply`):
   transformasi aturan (gue->aku dsb.) sebagai pengganti render model
   sampai Fase 2. Harness uji buta memakai transformasi ini — hasil
   uji buta manusia nanti menguji persona transformasi, bukan persona
   model; dicatat supaya tidak disalahartikan.

## Konflik definisi vs build (dicatat, definisi tidak diubah)
1. **C57 — irisan dicatat di Buku Kerja.** Jenis kejadian tertutup
   (31 jenis, §2.3) tidak punya jenis untuk pemberian irisan.
   Sementara: tabel audit `pemberian_irisan` (ber-RLS, bisa diaudit),
   kolom `kejadian_id` disiapkan untuk menautkan begitu jenisnya ada.
   Perlu: tambah jenis `irisan_diberikan` (revisi 8 / Fase 2, butuh
   migrasi CHECK skema Fase 0).
2. **C129 — hapus akun vs isi Buku Kerja.** Definisi 2.3 melarang
   UPDATE/DELETE di `kejadian`, dan 2.17 minta hapus akun menghapus
   memori dkk. dengan rantai tetap utuh lewat tombstone. Fase 1
   menghapus semua data user DI LUAR Buku Kerja; `isi` kejadian yang
   memuat data user tetap ada. Perlu: jenis kejadian tombstone /
   kebijakan redaksi (revisi 8 / Fase 2).
3. **C126 — "dump tiap hari".** Mekanisme backup + prune + restore
   jadi dan teruji; penjadwalan harian otomatis butuh penjadwal Fase 2.
4. **C122 — "5 menit".** Yang terukur di Fase 1 adalah waktu sisi
   sistem (0,29 detik). Waktu manusia (membaca, mengetik, memilih)
   baru terukur saat uji pengguna (gerbang 15: 8 dari 10 pengguna uji
   sampai hasil berbukti pertama dalam 5 menit).

## Gerbang yang tersentuh
- **Gerbang 3 (Persona blind A/B): PARSIAL.** Harness + 20 item contoh
  jadi dan teruji; uji manusia 5 teman TERTUNDA (menunggu Nero).
- **Gerbang 15 (Operasional + onboarding): PARSIAL.** Persetujuan
  dicatat (11/11 belum terukur — butuh pengguna uji); restore ke host
  bersih berhasil dalam 30 menit (terukur 0,3 detik di VM ini);
  onboarding sistem terukur; 0 token Google kedaluwarsa selama 4
  minggu uji BELUM terukur (butuh masa uji + publikasi production).
- **Gerbang 8 (Graf moat):** belum — prosedur tersimpan, graf penuh
  Fase 3.

## Butuh Nero (langkah manusia, bukan blokir kode)
1. **Publikasi aplikasi Google OAuth** ke "In production" (browser
   Nero; keputusan 19). Tanpa ini refresh token kedaluwarsa 7 hari.
2. **Uji buta gerbang 3**: 5 teman juri + jawaban mereka.
3. **Tinjauan hukum** teks persetujuan v1 (keputusan 20) sebelum
   pengguna di luar lingkaran dekat.
4. **Keputusan 5** (nomor pelanggan PLN/PDAM/HP) — dibutuhkan Fase 2,
   bukan Fase 1.

## Insiden lingkungan selama Fase 1
- **VM restart di tengah kerja** (uptime 0 menit saat dicek ~15:04
  WIB): Postgres 16 hilang TOTAL (paket apt + cluster + data).
  Dipulihkan: `apt-get update` + instal `postgresql-16` +
  `postgresql-16-pgvector` + `pg_ctlcluster 16 main start` +
  `infra/setup_db.sql` (sebagai postgres, file di-chmod 644 dulu)
  + `CREATE EXTENSION vector`. Semua file workspace utuh.
  Kedua suite diverifikasi ulang hijau SESUDAH pemulihan
  (Fase 1: 58 passed; Fase 0: 244 passed, 4 skipped).
  Pelajaran: Postgres di VM ini tidak tahan restart — cadangan
  terenkripsi Fase 1 adalah mitigasi yang tepat; jadwal backup
  harian otomatis (Fase 2) penting.
- **Run pytest paralel = false-failure** (dikonfirmasi lagi): run
  awal Fase 0 menunjukkan 7 failed + 8 error saat dua pytest jalan
  bersamaan di DB yang sama; run solo hijau. Semua angka di dokumen
  ini dari run SOLO.

## Pola repo besar yang diadaptasi (detail: `../REUSE.md`)
- **mem0** (Apache-2.0): fakta ber-asal + scoped per user; disimpulkan
  -> Usulan (bukan profil). Ditulis ulang sebagai `profile.py`.
- **Graphiti** (Apache-2.0): fakta temporal (berlaku dari/sampai);
  prosedur sebagai templat tanpa data pribadi. Ditulis ulang.
- **Letta** (Apache-2.0): sleep-time compute / konsolidasi saat idle.
  Ditulis ulang sebagai `consolidate.py` (deterministik).
- **elizaOS** (MIT): persona sebagai data (character file).
  Ditulis ulang sebagai fakta profil + contoh gaya.
- Tidak ada kode yang disalin plek-ketiplek; tidak ada dependensi
  baru; tidak ada fork.
