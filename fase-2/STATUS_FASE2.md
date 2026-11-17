# STATUS — Fase 2 MVP Puspa (Runtime tim + PWA + Konektor + Penjadwal)

Definisi: revisi 7 (disetujui Nero 17 Nov 2026 ~17:42 WIB).
Timeline 17 minggu DIABAIKAN atas perintah Nero 21:53 WIB (tanggal
hanya urutan, bukan deadline). Kerja lokal di `build/fase-2/`;
JANGAN push ke GitHub; JANGAN mulai Fase 3.

## Angka (semua run SOLO, satu pytest dalam satu waktu)

| Suite | Hasil | Waktu |
| --- | --- | --- |
| Fase 2 (baru) | **73 passed** | 17 Nov 2026 ~22:40 WIB |
| Fase 0 (verifikasi ulang) | **244 passed, 4 skipped** | 17 Nov 2026 ~22:35 WIB, solo |
| Fase 1 (verifikasi ulang) | **58 passed** | 17 Nov 2026 ~22:36 WIB, solo |

4 skip Fase 0 = tes live-container Docker (kernel tolak `setns`) —
blokir lingkungan yang sama seperti Fase 0/1, bukan regresi.
Total baris kode baru Fase 2: ~2.600 baris Python + 11 template
HTML + 1 service worker + 10 kaset acuan.

Bukti run PWA live (uvicorn 127.0.0.1:8123, 17 Nov 2026 ~22:45 WIB):
7 layar HTTP 200 (chat 0,16 dtk, sisanya 0,01-0,02 dtk), SSE
mengalir (`data: {"jenis": "kantor"...}` + detak), `POST /api/chat`
OK, `POST /api/tugas` selesai (`status: selesai, tahap: sajikan`),
`/sw.js` 200, `/manifest.json` OK.

## Yang dibangun (per scope definisi)

### 1. Runtime tim (§2.2, §2.4) — `puspa/runtime/`
- **Registry agen** (`agents.py`): maks 3 agen; Puspa wajib tidak
  bisa dihapus — ditolak di API (`hapus_agen`) DAN di database
  (trigger `trg_tolak_hapus_puspa` di `agen_meta`; DELETE langsung
  melempar exception, diuji). Agen baru mulai tanpa konektor; Puspa
  mulai dengan Gmail, Drive, Calendar.
- **Kontrak handoff** (`handoff.py`): skema tetap 10 field,
  divalidasi saat dibuat; mesin status diajukan → diterima →
  berjalan → diserahkan → lolos/ditolak; keluaran tidak lolos skema
  = ditolak; 1 percobaan ulang lalu lapor user; detak 15 detik,
  macet bila tanpa detak 30 detik atau lewat batas waktu (default
  45 detik); lingkaran A→B→A ditolak kode; maks 3 handoff antar-agen
  per tugas.
- **Anggaran** (`budget.py`): maks 25 langkah/agen, 15/sub-agen;
  anggaran sub-agen tidak melebihi sisa agennya; plafon Rp2.000 per
  tugas (tugas berhenti + melapor saat terlampaui); kalibrasi dari
  Buku Kerja (`kalibrasi_dari_ledger`: rata-rata/median/maks per
  tugas; jujur kosong bila belum ada data biaya).
- **Kolam sub-agen** (`subagents.py`): maks 4 per agen, 6 di seluruh
  sistem, selebihnya antre (antrean maju saat satu selesai); satu
  level saja (sub-agen membuat sub-agen ditolak); token sub-agen
  harus subset token agennya (`is_subset` Fase 0, diperiksa kode);
  Hentikan mematikan semua sub-agen tugas + mencabut token
  (`revoke_all_for_tugas`) — target < 5 detik (diuji: `detik` hasil
  < 5 untuk 3 sub-agen).
- **Siklus tugas** (`tasks.py`): 5 tahap Terima → Rencana →
  Jalankan → Periksa → Sajikan; tujuan ambigu → maks 1 pertanyaan;
  rencana aksi keluar berhenti di persetujuan sebelum jalan;
  pelaksana disuntikkan (runner bisa diuji tanpa model/jaringan);
  antrean Postgres `SELECT FOR UPDATE SKIP LOCKED`
  (`klaim_tugas_berikutnya`); tombstone hapus tugas (C129, lihat
  bawah).

### 2. Pemeriksa + Buku Bukti + angka yatim (§2.5) — `puspa/evidence/`
- **Bukti** (`evidence.py`): satu rekaman per angka (bukti_id,
  klaim, nilai, satuan, sumber_url, waktu_ambil, potongan_teks maks
  200 karakter, hash_halaman, tangkapan_layar TTL 30 hari,
  id_pembaca, id_pemeriksa, vonis); validasi deterministik lapis 1
  (parse angka, satuan, domain allowlist); simpan ke tabel `bukti`.
- **Pemeriksa** (`verifier.py`): independen (hanya terima klaim,
  baca ulang lewat callable sendiri); tiga lapis (deterministik,
  baca ulang, model hanya keputusan berbahasa — callable opsional,
  default bandingkan nama yang dinormalisasi); vonis Terverifikasi /
  Belum pasti / Tidak bisa dipastikan. Uji suntik harga salah:
  3 dari 3 ditangkap (bukan Terverifikasi).
- **Renderer angka yatim** (`renderer.py`): penanda
  `{label:bukti_id}` diisi kode dari Buku Bukti; angka yatim di
  draf ditahan → perbaikan 1x → kalau masih yatim dibuang +
  pernyataan "tidak bisa memastikan angkanya". Terukur 3 dari 3
  angka yatim ditahan/dibuang. Koreksi penting saat build: deteksi
  yatim harus di level DRAF (bukan hasil render), supaya angka yang
  sudah dirender dari bukti tidak salah ditandai yatim.
- **Skor Keandalan** (`evidence.py`): % Terverifikasi dari 50
  pembacaan terakhir; di bawah 80% → semua hasil sumber itu
  maksimal Belum pasti; ringkasan lencana ("5 terverifikasi, 1
  belum pasti").
- **Proyeksi** (`projections.py`): thread chat, Kantor (status live
  + biaya), audit, bukti, biaya — semua fungsi murni atas daftar
  kejadian ledger; bisa dibangun ulang kapan saja.

### 3. Pembaca terkarantina di runtime (§2.6)
- Konektor baca (`gmail_baca`, `drive_baca`) meneruskan isi mentah
  lewat `QuarantinedReader` Fase 0 (domain allowlist, teks bebas
  ditandai `[DATA: ...]`, maks 200 karakter). Draf balasan email:
  Pembaca menulis teks draf sebagai data, Pelaksana memanggil tool
  buat draf, kirim tetap lewat gerbang + persetujuan (ditegakkan di
  `GoogleConnector.gmail_kirim`: `disetujui=False` → ditolak).

### 4. Router model bertingkat (§2.4) — `puspa/router/router.py`
- Dibangun DI ATAS factory generik Fase 0
  (`demo/byok_provider.py`, `build_byok_provider`) — bukan tulis
  ulang. Lapisan pemilihan: perencana/penyaji = termurah yang lolos
  10 tugas acuan; pembaca/pencari = termurah; pemeriksa = keluarga
  berbeda dari pembuatnya; vision = termurah yang bisa membaca
  label (bila tidak ada → `None`, agen minta user menulis nama
  barang). Tarif katalog adalah konfigurasi (Rp/1.000 token, harga
  publik OpenRouter Nov 2026, dibulatkan) — bukan angka yang
  dijamin; kalibrasi ulang dari Buku Kerja.

### 5. PWA 7 layar (§2.1, §2.9, §2.10) — `puspa/pwa/app.py` + `apps/pwa/`
- FastAPI + Jinja + HTMX + service worker (keputusan 1). Update
  live lewat SSE (`/sse`, `StreamingResponse`, tanpa WebSocket).
  Layar: Chat (group chat, 6 jenis pesan lewat proyeksi, sub-agen
  terlipat, @mention, strip kerja, EventSource live), Tugas
  (antrean + Rencana + antrean persetujuan), Modul (2 bawaan +
  panel modul kustom — modulnya fase 3-4), Konektor (hubung/cabut
  per agen + scope), Memori (profil + Usulan + ekspor/hapus via
  Fase 1), Pengaturan (batas belanja Rp0, bahasa Indonesia, notif
  + uji kirim, akun+perangkat, persetujuan data, Rekor Otonomi),
  Kantor (kartu tim dari Buku Kerja — BUKAN animasi; lencana bukti;
  kontrol Ambil alih/Hentikan/Ulang; log handoff live; widget
  strip di chat). Tepat 7 layar (ditegakkan tes).
- Login Google (keputusan 16, fondasi Fase 1): `/auth/google`
  memakai `build_auth_url` Fase 1; callback mencatat bahwa
  `complete_google_login` Fase 1 yang menangani penukaran code.
  BATAS JUJUR: cookie sesi httpOnly belum dipasang di callback
  (callback mengembalikan JSON catatan, bukan menyelesaikan login);
  login penuh end-to-end menunggu client_id/secret + callback
  final — dicatat sebagai tindak lanjut, bukan diklaim selesai.
  Seam tes: `create_app(test_user_id=...)`.

### 6. Konektor (§2.8) — `puspa/connectors/`
- `GoogleConnector` (httpx, client disuntikkan; tes pakai
  `MockTransport`, tanpa jaringan): Gmail cari/baca/buat draf/kirim
  (kirim hanya setelah persetujuan), Drive cari/baca, Calendar baca
  event/buat event (buat hanya setelah persetujuan). Token dari
  vault Fase 1 (`load_tokens`); 401 → "sambungkan ulang".
- Pemberian per agen (`grants.py`): Puspa mulai dengan ketiganya,
  agen baru kosong; beri/cabut dari PWA; scope minimal per
  konektor.
- BLOKIR YANG DIKETAHUI: publikasi app Google OAuth ke
  "In production" butuh browser Nero (status Testing = refresh
  token kedaluwarsa 7 hari). Kode tuntas + diuji dengan status
  Testing; publikasi = langkah Nero tertunda (keputusan 19).

### 7. Penjadwal + host (§2.14) — `puspa/scheduler/`, `puspa/push/`
- Tabel `pemicu` + satu proses penjadwal (`tick`): pemicu tercatat
  di Buku Kerja (`pemicu_terpicu`) dengan kunci idempotensi
  (`pemicu_jalan` — satu pemicu tidak pernah menjalankan dua
  tugas, diuji); pemicu terlewat dijalankan sekali saat bangun +
  dilaporkan (`pemicu_terlewat`, diuji); detak penjadwal ke Kantor
  (mati > 2 menit → `sehat()` False, diuji).
- Job bawaan disambungkan: backup harian Fase 1 (C126 —
  `jadwalkan_backup_harian` mendaftarkan pemicu `backup_harian`;
  `jalankan_job_terjadwal` memanggil `create_backup` Fase 1) dan
  konsolidasi saat sepi Fase 1 (`consolidate_idle`).
  BATAS: jadwal berikutnya digeser +1 hari (jadwal harian);
  parser cron penuh adalah peningkatan Fase 4.
- Web Push VAPID (`puspa/push/push.py`): kunci P-256 dibuat
  sendiri, JWT ES256 (RFC 8292) dibuat sendiri dengan
  `cryptography`, enkripsi payload aes128gcm (RFC 8291) — diuji
  bolak-balik (enkripsi → dekripsi = isi asli); kirim lewat httpx
  (MockTransport, 201); push kosong (bangunkan saja) didukung.
  Peringatan iOS (hanya jalan bila PWA dipasang ke layar utama)
  ada di Pengaturan + onboarding menguji kirim.
- BLOKIR YANG DIKETAHUI: host selalu hidup butuh VPS Contabo
  Singapura (~$6,99/bulan + keputusan Nero); natbox trial 256 MB
  TIDAK dipakai (terlalu kecil). Penjadwal + push dibangun tuntas
  dan diuji lokal; host produksi = langkah Nero tertunda.

### 8. Uji kaset (§2.11) — `puspa/cassette/`, `cassettes/`
- Rekam dari `tool_selesai` ledger; `CassettePlayer` menggantikan
  tool (tanpa jaringan; kaset habis → error jujur, bukan karangan);
  `suntik_harga_salah` (kaset baru, asli tidak diubah); hapus kaset
  ikut tugas dihapus (file + baris DB).
- 10 kaset acuan dibuat (`cassettes/*.json`, generator
  `cassettes/buat_kaset_acuan.py`, data uji): putar ulang 10/10
  angka identik (target gerbang 12: minimal 9 dari 10).

### 9. Penyelarasan layout repo (§7)
- KEPUTUSAN: bertahap (bukan migrasi penuh sekaligus). Alasan:
  paket Fase 0/1 adalah base stabil (244 + 58 tes hijau) dan
  pemindahan file akan memecahkan impor + conftest ketiga suite.
- Yang dilakukan di Fase 2: kode baru ditulis DALAM struktur
  target — `apps/pwa/` (template + static PWA),
  `apps/api` diwakili endpoint FastAPI di `puspa/pwa/app.py`
  (satu deploy FastAPI, keputusan 1), `packages/*` dipetakan ke
  subpaket `puspa/*` (lihat pemetaan di bawah), `infra/` berisi
  `schema_fase2.sql`. `puspa/` tetap satu namespace
  (`extend_path`) di tiga direktori fase.
- Pemetaan lama → baru (peta jalan migrasi saat repo disatukan
  ke mirzasnowy/puspa-ai):

  | Lama (build fase 0/1) | Baru (§7) |
  | --- | --- |
  | `puspa/ledger/` | `packages/ledger/` (Buku Kerja + Bukti) |
  | `puspa/policy/` | `packages/policy/` (token + gerbang) |
  | `puspa/accounts/`, `puspa/auth/` | `packages/policy/` (akun) + `apps/api` (login) |
  | `puspa/quarantine/`, `puspa/sandbox/` | `packages/agent/` (karantina + sandbox) |
  | `puspa/chassis/` | `packages/agent/` (integrasi nanobot) |
  | `puspa/memory/`, `puspa/persona/`, `puspa/onboarding/` | `packages/agent/` (memori + persona) |
  | `puspa/backup/`, `puspa/consent/` | `packages/ledger/` (cadangan) + `apps/pwa` (persetujuan) |
  | `puspa/runtime/`, `puspa/evidence/`, `puspa/router/` (Fase 2) | `packages/agent/` + `packages/ledger/` + `packages/agent/` |
  | `puspa/connectors/`, `puspa/scheduler/`, `puspa/push/`, `puspa/cassette/` (Fase 2) | `packages/agent/` (konektor) + `packages/workflow/` (penjadwal) + `packages/agent/` (kaset) |
  | `puspa/pwa/`, `apps/pwa/` (Fase 2) | `apps/pwa/` |
  | `demo/byok_provider.py` (Fase 0) | `packages/agent/` (router BYOK) |

### 10. Keputusan (3 pertanyaan Fase 0 + konflik Fase 1)
- **(a) Pendaftaran agen Puspa: WAJIB.** Satu agen Puspa dibuat
  otomatis saat pengguna dibuat / login pertama
  (`ensure_puspa_agent`, idempotent). Alasan: pembaca bermilik
  Fase 0 menolak fail-closed kejadian dari agen yang belum
  terdaftar, jadi tanpa Puspa terdaftar semua kejadian tugas akan
  ditolak.
- **(b) Tabel `kejadian`: TIDAK perlu RLS.** Isolasi lewat
  registry agen + `require_owner` di pembaca bermilik sudah cukup;
  `kejadian` tidak punya kolom `user_id` dan menambahkannya akan
  mengubah skema append-only + rantai hash. Tabel Fase 2 yang
  ber-`user_id` SUDAH diberi RLS (kebijakan
  `current_setting('app.current_user_id')`).
- **(c) `puspa_app`: YA, diberi LOGIN + password.** Hanya bila env
  `PUSPA_APP_PASSWORD` diisi (tanpa env, tetap NOLOGIN seperti
  Fase 0; tidak ada password default). Diimplementasi di
  `puspa/runtime.create_schema`.
- **C57 (irisan di Buku Kerja): DIPUTUSKAN — tetap tabel audit.**
  Jenis kejadian tertutup 31 jenis TIDAK ditambah di Fase 2
  (menambah jenis butuh revisi 8 + migrasi CHECK skema Fase 0).
  Pemberian irisan tetap dicatat di tabel audit
  `pemberian_irisan` Fase 1 (ber-RLS, bisa diaudit). Perlu
  keputusan Nero di revisi 8 bila ingin jenis `irisan_diberikan`.
- **C129 (tombstone hapus akun): SELESAI.** Hapus tugas = append
  `tugas_dihentikan` dengan isi `{"dihapus": true}` (tanpa
  UPDATE/DELETE di `kejadian`; rantai hash tetap utuh) +
  kaset/blob ikut dihapus (`tombstone_tugas`, diuji).
- **C126 (backup harian): SELESAI (disambungkan).** Pemicu
  `backup_harian` terdaftar lewat penjadwal; eksekusi memanggil
  `create_backup` Fase 1. Jadwal backup nyata jalan saat penjadwal
  berjalan di host (VPS — langkah Nero).
- **C122 (5 menit onboarding): BATAS DICATAT.** Endpoint
  `/api/onboarding/waktu` merekam waktu onboarding dari PWA
  (untuk pengukuran gerbang 15). Waktu manusia tetap baru bisa
  diukur saat uji pengguna nyata (8 dari 10 dalam 5 menit).

## Gerbang yang tersentuh
- **Gerbang 2 (tugas acuan): PARSIAL.** 10 kaset acuan jadi +
  putar ulang 10/10 identik; putaran hidup 10 tugas acuan (dengan
  model + wedge) menunggu Fase 3 (wedge) + pengguna uji.
- **Gerbang 9 (performa): PARSIAL.** PWA load lokal 0,01-0,16 dtk
  (target < 3 dtk di 4G — belum diukur di HP 4 GB); spawn
  sub-agen di kolam instan (container < 3 dtk belum bisa diukur —
  Docker tidak jalan di VM ini, blokir lingkungan Fase 0).
- **Gerbang 10 (biaya): PARSIAL.** Plafon Rp2.000 ditegakkan +
  kalibrasi dari Buku Kerja jadi; burn Rp500 ribu/bulan belum
  terukur (butuh masa uji + host).
- **Gerbang 11 (agen + sub-agen): PARSIAL (terukur).** Buat agen
  ke-2 dari PWA (diuji); pola 3 Pembaca + 1 Pemeriksa di kolam
  (batas + kelas izin diuji); Pemeriksa tangkap harga salah 3/3;
  handoff macet terdeteksi (detak 30 dtk, batas 45 dtk) — lapor ke
  user dalam 2 menit dipenuhi lewat 1 percobaan ulang (total maks
  100 dtk); Puspa tidak bisa dihapus (API + DB trigger, diuji);
  token di luar ditolak 3/3; handoff melingkar ditolak; Hentikan
  < 5 detik (diuji). Belum: uji dengan model hidup + wedge
  perbandingan harga (Fase 3).
- **Gerbang 12 (tulang punggung + bukti): PARSIAL (terukur).**
  Angka yatim ditahan 3/3; rantai hash diubah terdeteksi 3/3;
  10 kaset diputar ulang 10/10 identik (target 9/10). Belum: uji
  matikan paksa 3/3 (0 aksi keluar ganda) — butuh host + proses
  nyata, dicatat sebagai tindak lanjut.
- **Gerbang 15 (operasional + onboarding): PARSIAL.** Penjadwal +
  detak + pemicu terlewat jadi; endpoint ukur waktu onboarding
  jadi; 0 token kedaluwarsa 4 minggu + 0 pengingat terlewat karena
  host mati BELUM terukur (butuh masa uji + VPS + publikasi OAuth).
- **Gerbang 7 (autopilot tagihan): BELUM.** 100% pengingat
  terkirim butuh host selalu hidup (VPS — langkah Nero).

## Butuh Nero (langkah manusia, bukan blokir kode)
1. **VPS Contabo Singapura** (~$6,99/bulan) untuk host selalu
   hidup + penjadwal + pengingat (keputusan 2). Tanpa ini gerbang
   7 tidak bisa lolos.
2. **Publikasi aplikasi Google OAuth** ke "In production" (browser
   Nero; keputusan 19). Tanpa ini refresh token kedaluwarsa 7 hari.
3. **Keputusan 5** (nomor pelanggan PLN/PDAM/HP untuk canary +
   uji) — dibutuhkan Fase 2/3; belum diberi.
4. **Login Google end-to-end**: client_id + client_secret +
   callback final di PWA (setelah butir 2) — kode fondasi Fase 1
   sudah ada.
5. **Keputusan 1, 4, 8, 13, 14, 15**: rekomendasi definisi sudah
   diikuti di build (FastAPI+Jinja+HTMX+SW; router murah BYO;
   25 langkah; plafon Rp2.000; Pemeriksa beda keluarga; syarat
   layanan dicek sebelum Fase 3) — tinggal konfirmasi Nero bila
   tidak setuju.

## Pola repo besar yang diadaptasi (detail: `../REUSE.md`)
- **LiteLLM** (MIT): router model bertingkat → `puspa/router/`.
- **agno / AgentOS** (Apache-2.0): eksekusi durable, approval,
  scheduling, observability → runtime + Buku Kerja + penjadwal.
- **OpenHands** (MIT): pengerasan sandbox → karantina + allowlist
  (dipakai ulang Fase 0, ditegakkan di konektor Fase 2).
- **opencode** (MIT): UX agen (chat/task/approval) → PWA.
- **Home Assistant** (Apache-2.0): konektor + otomasi terjadwal →
  konektor Google + penjadwal.
- **Supabase** (Apache-2.0): pola cloud (RLS + auth + realtime)
  → RLS Fase 2 + SSE sebagai ganti realtime.
- Tidak ada kode yang disalin plek-ketiplek; tidak ada dependensi
  baru kecuali FastAPI (MIT); tidak ada fork.

## Yang TIDAK dibangun (sengaja, sesuai definisi)
- Pola builder visual Langflow → UX builder alur/modul = Fase 4.
- Voice (Pipecat/LiveKit) → modul Suara TIDAK ada di definisi;
  dicatat pasca-MVP di REUSE.md.
- Playwright browser read-only (C67/C68): belum dibangun — baca
  halaman lewat Pembaca + HTTP; Playwright menyusul Fase 3
  (wedge butuh baca listing).
- Kolam sandbox hangat (C18): belum — spawn container tidak bisa
  diuji di VM ini; potong no. 3 bila terlambat (§9).
- Parser cron penuh: jadwal pemicu digeser +1 hari; cron penuh
  Fase 4.
- Jadwal salat/kiblat, WhatsApp/Telegram, fashion, QRIS penuh:
  tetap tidak dibangun (bagian 6).

## Insiden / catatan lingkungan selama Fase 2
- FastAPI belum ada di venv Fase 0: dipasang `fastapi==0.163.1`
  (MIT). Pip sempat menurunkan starlette ke 0.46.2 (konflik dengan
  sse-starlette 3.7.0 yang butuh >=0.49.1) — dihindari dengan
  memakai `StreamingResponse` bawaan Starlette untuk SSE, bukan
  sse-starlette. Suite Fase 0 tetap hijau sesudahnya (244 passed).
- `pkill -f uvicorn` saat mematikan server bukti-run sempat
  mematikan shell sendiri (pola cocok dengan baris perintah
  sendiri). Pelajaran: jangan `pkill -f` dengan pola yang ada di
  baris perintah sendiri.
- Postgres tetap hidup sepanjang Fase 2 (tidak ada insiden
  hilang total seperti Fase 1).
