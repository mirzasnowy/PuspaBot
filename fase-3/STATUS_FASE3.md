# STATUS — Fase 3 MVP Puspa (Wedge ganda + Moat)

Definisi: revisi 7 (disetujui Nero 17 Nov 2026 ~17:42 WIB).
Timeline 17 minggu DIABAIKAN atas perintah Nero 21:53 WIB (tanggal
hanya urutan, bukan deadline). Kerja lokal di `build/fase-3/`;
JANGAN push ke GitHub; JANGAN mulai Fase 4.

## Angka (semua run SOLO, satu pytest dalam satu waktu)

| Suite | Hasil | Waktu |
| --- | --- | --- |
| Fase 3 (baru) | **62 passed** (dengan browser; 61 passed + 1 skipped tanpa env browser) | 17 Nov 2026 ~23:30 WIB |
| Fase 0 (verifikasi ulang) | **248 passed, 4 skipped** | 17 Nov 2026 ~22:50 WIB, solo |
| Fase 1 (verifikasi ulang) | **58 passed** | 17 Nov 2026 ~22:50 WIB, solo |
| Fase 2 (verifikasi ulang) | **73 passed** | 17 Nov 2026 ~22:45 WIB, solo |

4 skip Fase 0 = tes live-container Docker (kernel tolak `setns`) —
blokir lingkungan yang sama seperti Fase 0/1/2, bukan regresi.
Fase 0 naik 244 → 248 passed: 4 tes regresi registry baru
(keputusan 22, `get_definitions` difilter token, mendarat
17 Nov 2026 ~22:40 WIB) — bukan perubahan kode Fase 3.

Total baris kode baru Fase 3: ~3.500 baris Python (kode + tes; 10 modul
+ 10 file tes + skema 16 tabel).

## TEMUAN TERBESAR: syarat layanan ketiga marketplace melarang baca otomatis

Cek keputusan 15 — dibaca langsung dari halaman syarat resmi tiap
sumber (17 Nov 2026, tercatat di `cek_syarat_layanan` + `puspa/pricing/tos.py`):

| Sumber | Hasil | Pasal |
| --- | --- | --- |
| Tokopedia | **melarang_otomatis** | S&K B.12-13: dilarang memakai robot, macro, crawler dan/atau perangkat otomatis untuk crawling/scraping tanpa izin Tokopedia. Sumber: https://www.tokopedia.com/help/article/terms-conditions |
| Shopee | **melarang_otomatis** | Ketentuan Layanan bag. 3: dilarang mengakses dengan bot/emulator dan memantau/menyalin otomatis tanpa izin tertulis; jalur resmi yang diizinkan adalah Shopee Open Platform (butuh akun partner + otorisasi toko). |
| Blibli | **melarang_otomatis** | S&K 2.2: dilarang mengambil isi secara sistematis untuk membuat/menyusun koleksi, kompilasi, database atau direktori tanpa izin tertulis. Sumber: https://www.blibli.com/faq/tentang-blibli/syarat-ketentuan/ |
| PLN | layanan_resmi_terbatas | Cek tagihan layanan resmi pelanggan sendiri di layanan.pln.co.id; butuh login + captcha — jalur otomatis tidak dijamin, bukan larangan eksplisit. |
| PDAM | layanan_resmi_terbatas | Tiap daerah punya situs sendiri; cek tagihan layanan resmi pelanggan sendiri. MVP hanya PDAM milik Nero + pengguna uji (§4.2). |
| pulsa / paket data | layanan_resmi_terbatas | Cek lewat kanal resmi operator (aplikasi/UMB); tidak ada API publik umum. |

**Konsekuensi kode (bukan sekadar catatan):** `puspa/pricing/tos.py`
punya gerbang `boleh_baca` — pembacaan LIVE ketiga marketplace
DITAHAN sampai Nero memutuskan lanjut atau ganti sumber (keputusan
15). `bandingkan_harga(mode="live")` mengembalikan
`tidak_bisa_dicek` + alasan `syarat_layanan_menunggu_keputusan_nero`
TANPA satu pun upaya HTTP ke marketplace (diuji: pembaca dipanggil
0 kali). Kalau sumber memblokir (403/429/captcha), reader melaporkan
`sumber_memblokir` / `captcha_terdeteksi` — TANPA upaya menembus
blokir (§11). Jalur resmi yang sesuai syarat (Shopee Open Platform,
dan kemungkinan API resmi Tokopedia/Blibli) butuh akun partner +
persetujuan — bukan bagian MVP, perlu keputusan Nero.

## Yang dibangun (per scope definisi)

### 1. Wedge 1 — bandingkan harga (§4.1) — `puspa/pricing/compare.py`
- 3 Pembaca (satu per marketplace) + 1 Pemeriksa membaca ulang tiap
  angka (§2.5); pencocokan lewat Graf Identitas; semua angka yang
  tampil punya `bukti_id` (harga tanpa bukti tidak ditulis ke graf).
- Scope 9 kategori dikunci di kode (`catalog.KATEGORI_SCOPE`):
  fashion/sepatu/tas ditolak dengan pesan jelas, bukan dibangun versi
  diam-diam lemah (§5.2). Aturan masuk: nomor model bisa dicocokkan.
- Ragu → tanya user (kandidat ditampilkan), bukan tebak (§3.2).
- Output: harga termurah + toko + rating, harga tiap marketplace,
  selisih, tautan tiap listing, vonis tiap harga.
- Harga: banding dasar gratis; cek detail (riwayat 30 hari + harga
  terkini) = 1 kredit; 15 kredit gratis/bulan (`credits.py`).
- Input foto: belum — foto dibaca model vision Fase 2; wedge menerima
  nama barang / tautan produk (foto → nama barang oleh agen, sesuai
  §2.4; dicatat sebagai batas, bukan diklaim).

### 2. Graf Identitas dan Harga (§3.2) — `puspa/graphs/identity.py`
- Lapisan identitas: produk kanonik (merek, seri, nomor model,
  varian ternormalisasi: storage/warna/ram); tautan listing berstatus
  `otomatis` / `dikonfirmasi_user` / `ditolak_user`.
- Lapisan harga: deret waktu (harga, ongkir, rating toko, stok,
  `bukti_id`); `catat_harga` MENOLAK angka tanpa `bukti_id`.
- Pencocokan bertingkat: kunci model deterministik → kandidat + tanya
  user → tautan `dikonfirmasi_user` → TIDAK ADA tebakan senyap.
- API baca: harga terkini, riwayat 30 hari, kandidat identitas.
- Penyimpanan: Postgres (tabel + relasi), BUKAN graph DB eksternal —
  §7 terpenuhi; pola graf embedded LadybugDB hanya referensi pola
  (lihat REUSE.md). Tidak ada bukti Postgres tidak cukup.

### 3. Bibit katalog (§3.8) — `puspa/pricing/catalog.py`
- 14 baris bibit (13 produk unik) lintas 9 kategori; 10 barang uji
  default (keputusan 7: daftar final menunggu konfirmasi Nero).
- CATATAN JUJUR: bibit ditulis dari spesifikasi publik yang dikenal,
  BUKAN hasil tarikan langsung dari halaman spesifikasi resmi merek
  saat build (tarikan live ditahan gerbang ToS). Bibit ditandai
  `sumber_bibit="bibit_awal"` di STATUS ini supaya bisa diganti hasil
  tarikan resmi begitu Nero memutuskan sumber (keputusan 15).

### 4. Watchlist + sampler harga (§3.8) — `puspa/pricing/watchlist.py`
- User menandai barang; sampler membaca berkala dengan anggaran
  maks 20/hari/pengguna (ditegakkan kode — anggaran habis → barang
  dilewati + dilaporkan, bukan dibaca diam-diam); alert harga turun
  bila harga ≤ ambang.
- BATAS DICATAT: parser cron penuh BELUM ada (catatan Fase 2: pemicu
  digeser +1 hari). Sampler memakai pemicu `harga_turun`/`jadwal`
  Fase 2 apa adanya (sekali sehari per barang); cron per jam tetap
  Fase 4. Penjadwal Fase 2 tidak diubah (aturan kerja fase: jangan
  edit paket Fase 2 kecuali perbaikan bug).

### 5. Cache bukti bersama (§3.8) — `puspa/pricing/cache.py`
- TTL 30 menit, dipakai ulang antar pengguna oleh Pembaca; tingkat
  cache hit dicatat (`statistik_hit`, tanpa target di MVP).
- Pemeriksa TIDAK PERNAH pakai cache: jalur Pemeriksa memanggil
  reader langsung; diuji (baca_ulang tetap dipanggil 3/3 walau cache
  terisi).

### 6. Lapor Salah (§3.8) — `puspa/moat/report.py`
- Tiap angka punya laporan salah: tercatat di tabel `laporan_salah`
  + kejadian `laporan_salah` di Buku Kerja (jenis sudah ada, §2.3) +
  satu pembacaan `Tidak bisa dipastikan` ditulis ke `pembacaan_sumber`
  sehingga Skor Keandalan sumbernya turun + baca ulang diminta.
  Diuji: skor Shopee 100% → 98% sesudah 1 laporan dari 50.

### 7. Wedge 2 — autopilot tagihan (§4.2) — `puspa/billing/billing.py`
- Provider MVP: PLN, PDAM, pulsa, paket data. TIDAK ADA BPJS
  (ditolak tegas, diuji).
- Prosedur dari Graf Alur Layanan; kalau belum ada, agen menemukannya
  + menulis sebagai prosedur percobaan, baru menjalankan.
- Nomor pelanggan TIDAK PERNAH masuk graf: `tulis_prosedur` menolak
  langkah yang memuat pola nomor pelanggan/HP (diuji).
- Output: jumlah + jatuh tempo + status (angka dari bukti), pengingat
  sebelum jatuh tempo (N hari sebelum, default 3), siapkan bayar
  mode aman = DRAF saja (nominal + tujuan + `isi_hash` untuk
  persetujuan terikat isi; TIDAK ADA eksekusi bayar).
- Situs uji lokal (`fixture.py`, stdlib saja): PLN Rp132.500 jatuh
  tempo 2026-11-20 belum_bayar; PDAM Rp87.500; pulsa Rp50.000; paket
  data Rp75.000. Nilai ini data uji, bukan tagihan nyata siapa pun.

### 8. Graf Alur Layanan (§3.3) — `puspa/graphs/service.py`
- Di atas tabel `prosedur` Fase 1 (siklus percobaan → terverifikasi
  3 jalan sukses min 2 agen → rusak 2 gagal berturut-turut).
- Tambahan Fase 3: canary harian (hanya prosedur terverifikasi;
  gagal → prosedur rusak; hasil dicatat di `canary_jalan`); pakai
  ulang lintas agen dicatat (`pemakaian_prosedur`); perbaikan =
  versi baru lolos 3 uji sebelum menggantikan + rollback ke versi
  lama (snapshot di `prosedur_versi`); prosedur `rusak` tidak
  dijalankan (agen bilang tidak bisa cek).
- Pola sub-agen juga disimpan di sini: `simpan_pola_subagen`
  (`bandingkan_harga`: 3 Pembaca + 1 Pemeriksa; `cek_tagihan`:
  Pembaca + Pemeriksa) + `daftar_pola` lintas agen.

### 9. Skor Keandalan (§3.4) — `puspa/moat/reliability.py`
- Per sumber (marketplace x kategori) dan per prosedur: %
  Terverifikasi dari 50 pembacaan terakhir + umur pembacaan terakhir.
- Dipakai: (a) skor < 80% → semua hasil sumber itu maksimal
  Belum pasti (ditegakkan di `compare.py`, diuji); (b) label jujur
  panel Bukti ("Shopee, kategori TV: 92% terverifikasi dari 50
  pembacaan terakhir"); (c) bahan laporan bukti bisnis (metrik).
- Panel Skor Keandalan di PWA ada di urutan potong #1 (datanya tetap
  dihitung) — panelnya minimal: label panel tersedia sebagai fungsi,
  belum dipasang ke template PWA (Fase 2 tidak diubah).

### 10. Metrik moat gerbang 8 (§3.6 + §3.8) — `puspa/moat/metrics.py`
- Terukur lokal: cakupan pencocokan otomatis 100% di tes gerbang
  (3/3 tautan otomatis; 66,7% di tes metrik dengan 1 tautan
  dikonfirmasi_user — target ≥ 60% lolos di keduanya); 4 prosedur
  terverifikasi + masing-masing dipakai ulang agen lain (lolos);
  canary mendeteksi 2 dari 2 suntikan (lolos); Skor Keandalan
  terhitung untuk setiap sumber yang dipakai (lolos).
- Tertunda jujur: rasio token minggu terakhir vs minggu pertama
  (butuh masa uji 2 minggu — kode pengukur + pencatat token jadi);
  metrik alur aktif 3.8 (5/10 pengguna punya satu alur aktif) adalah
  FASE 4 — fondasi pustaka alur tercatat (`library.py`: alur sukses
  + Permintaan Blok), metriknya TIDAK diklaim di Fase 3.

### 11. Pembaca: HTTP fetch + Playwright (§2.8) — `puspa/pricing/readers.py`
- `HttpReader` (httpx, client disuntikkan; tes pakai MockTransport):
  parser generik JSON-LD `Product`/`Offer` + meta
  `product:price:amount`; blokir 403/429/captcha dilaporkan, tanpa
  menembus; domain di luar allowlist ditolak.
- `PlaywrightReader` (browser read-only): paket `playwright==1.65.2`
  (MIT) TERINSTAL di venv Fase 0. Unduhan browser Chromium revisi
  1255 dari CDN Playwright GAGAL 3x (`Download failure, code=1`,
  percobaan 17 Nov ~22:30, ~22:40, dan ~23:00 WIB, terakhir 7 menit).
  AKAR MASALAH TERDIAGNOSIS: CDN Playwright mengarahkan ke
  `playwright.download.prss.microsoft.com`, dan proxy egress
  mengembalikan `400 Bad Request` (`GatewayExceptionResponse`) untuk
  host itu — dibuktikan `curl` langsung (redirect 307 → 400).
  Sementara `storage.googleapis.com` bisa dijangkau (200 OK).
- SOLUSI YANG JALAN: Chrome for Testing 159.0.8154.6 (revisi yang
  sama dengan yang diminta Playwright 1.65.2) diunduh dari
  `storage.googleapis.com` (131 MB, berhasil) ke
  `~/.cache/ms-playwright/chrome-for-testing-159/`; `PlaywrightReader`
  memakai seam env `PUSPA_CHROMIUM_EXECUTABLE` untuk menunjuk binary
  itu (`p.chromium.launch(executable_path=...)`). DIBUKTIKAN:
  reader membaca situs uji lokal dan mengekstrak Rp132.500
  (`test_playwright_reader_baca_situs_uji_bila_browser_ada`, lolos
  bila env diset; skip bila tidak ada binary — bukan gagal).
  Revisi 1223 yang sudah ada di cache TIDAK dipakai (tidak cocok
  dengan Playwright 1.65.2). Tanpa env, `baca()` mengembalikan
  `browser_gagal` secara jujur; wedge memakai HTTP fetch sebisanya.
  TIDAK ADA hasil baca yang dikarang.

## Gerbang yang tersentuh
- **Gerbang 2 (tugas acuan): PARSIAL.** J4 (bandingkan harga laptop)
  + J5 (cek tagihan PLN + siapkan bayar) kini punya logika wedge +
  bukti + prosedur — diuji ujung-ke-ujung di fixture (tes gerbang
  Fase 3). Putaran hidup dengan model + sumber live menunggu
  keputusan 15 + nomor Nero + pengguna uji.
- **Gerbang 5 (bandingkan harga akurat): PARSIAL (mekanisme).**
  Mekanisme akurat teruji di fixture (Pemeriksa menangkap harga salah
  yang disuntik; vonis + bukti_id di tiap harga). Ukur 95% di 20
  perbandingan live TERTUNDA — live ditahan ToS (keputusan 15).
- **Gerbang 6 (bandingkan harga pemakaian): PARSIAL.** Kredit cek
  detail jadi + diuji (1 kredit, 15/bulan, habis → ditolak).
  Pemakaian nyata (20 perbandingan/minggu, 1 orang bayar 1 kredit)
  butuh masa uji + sumber live.
- **Gerbang 7 (autopilot tagihan): PARSIAL.** Prosedur 4 provider +
  pengingat + siapkan bayar (draf) jadi + teruji di fixture; 100%
  angka sama dengan provider + 100% pengingat terkirim butuh nomor
  live Nero + host selalu hidup (VPS — langkah Nero).
- **Gerbang 8 (graf moat tiga lapis): PARSIAL (terukur lokal).**
  Kedua graf terbukti ditulis wedge + dibaca cek berikutnya (query
  langsung di tes); metrik 3.6 sebagian lolos (cakupan, 4 prosedur +
  pakai ulang, canary 2/2, keandalan semua sumber), sebagian tertunda
  (rasio token — butuh masa uji); metrik 3.8 alur aktif adalah Fase 4.
- **Gerbang 11 (agen + sub-agen): PARSIAL.** Pola 3 Pembaca +
  1 Pemeriksa tersimpan di graf + dipakai lintas agen; Pemeriksa
  menangkap harga salah yang disuntik (diuji di wedge, bukan hanya
  kaset). Handoff macet / Hentikan / hapus Puspa tetap dari Fase 2.
- **Gerbang 12 (tulang punggung + bukti): PARSIAL.** 100% angka wedge
  punya `bukti_id` (ditegakkan `catat_harga` + `compare`); angka
  yatim ditahan di renderer Fase 2. Kaset Fase 3 (putar ulang wedge
  tanpa jaringan) belum dibuat — tindak lanjut wrap-up.

## Butuh Nero (langkah manusia, bukan blokir kode)
1. **Keputusan 15 — PALING MENDESAK.** Ketiga marketplace melarang
   baca otomatis di syarat layanannya. Nero memutuskan: (a) lanjut
   lewat jalur resmi (Shopee Open Platform + API resmi Tokopedia/
   Blibli — butuh akun partner + persetujuan, di luar MVP),
   (b) ganti sumber, atau (c) lanjut baca atas permintaan user
   dengan risiko yang Nero terima secara eksplisit. Sampai ada
   keputusan, wedge harga live tetap ditahan gerbang (bukan diblokir
   diam-diam — agen bilang tidak bisa cek + alasannya).
2. **Keputusan 5** (nomor pelanggan PLN/PDAM + nomor HP untuk canary
   + uji tagihan live) — dibutuhkan sejak Fase 2; belum diberi.
   Canary + uji live menunggu nomor ini.
3. **Keputusan 7** (daftar barang uji final): 10 barang default sudah
   disiapkan dari bibit katalog — menunggu konfirmasi Nero (atau
   diganti daftar pilihannya).
4. **VPS Contabo Singapura** (~$6,99/bulan) untuk host selalu hidup +
   penjadwal + pengingat (keputusan 2, gerbang 7). Canary harian +
   sampler berkala mekanismenya jadi; jadwal hariannya jalan saat
   penjadwal berjalan di host.
5. **Publikasi aplikasi Google OAuth** ke "In production" (browser
   Nero; keputusan 19) + callback login PWA final — bukan blokir
   wedge (wedge memakai baca web + fixture), tapi tugas acuan 2, 3, 6
   yang butuh Gmail/Drive live bergantung padanya.

## Konflik definisi vs build (dicatat, definisi tidak diubah)
1. **Verifikasi fixture tagihan vs Pemeriksa penuh.** `periksa`
   Fase 2 menolak sumber di luar allowlist (lapis 1) — situs uji
   lokal (localhost) tidak lolos. Mode fixture memakai perbandingan
   deterministik baca-ulang (Pemeriksa tetap membaca ulang lewat
   callable independen, nilainya dibandingkan; vonis Terverifikasi /
   Belum pasti / Tidak bisa dipastikan sama). Jalur LIVE tetap memakai
   `periksa` penuh (allowlist + baca ulang + keputusan berbahasa).
   Bukan pengurangan keamanan di jalur live; fixture ditandai jelas
   (`sumber_url` fixture + klaim `tagihan <provider>` dari data uji).
2. **Bibit katalog bukan tarikan langsung halaman resmi.** Lihat §3
   di atas — bibit awal ditulis dari spesifikasi publik yang dikenal;
   tarikan resmi menunggu keputusan 15.
3. **Parser cron penuh tetap Fase 4.** Sampler memakai pemicu Fase 2
   apa adanya (sekali sehari); bukan pura-pura cron per jam.
4. **Panel Skor Keandalan PWA minimal.** Datanya dihitung (label
   panel tersedia); pemasangan ke template PWA belum dilakukan karena
   aturan kerja fase melarang edit paket Fase 2 — panel ada di urutan
   potong #1, jadi ini sesuai definisi.

## Pola repo besar yang diadaptasi (detail: `../REUSE.md`)
- **LadybugDB** (MIT): pola graf embedded → graf di atas Postgres
  (node + edge sebagai baris relasional). BUKAN dipakai sebagai
  library — hanya polanya.
- **RAGFlow** (Apache-2.0): pola mesin yang menyimpan langkah kerja
  sebagai data yang bisa dipakai ulang → Graf Alur Layanan
  (prosedur + canary + pakai ulang). BUKAN dipakai sebagai library;
  dipakai sebagai komponen moat, bukan produk dokumen.
- Tidak ada kode yang disalin plek-ketiplek; tidak ada dependensi
  baru kecuali Playwright (MIT); tidak ada fork; tidak ada klaim
  LangGraph di mana pun (orkestrasi = AgentLoop nanobot + siklus
  tugas Puspa sendiri — koreksi atas kalimat survei yang salah,
  lihat "Catatan konsistensi" di bawah).

## Catatan konsistensi (dari Luna, 17 Nov 2026 ~22:46 WIB)
- **Perubahan kontrak registry Fase 0 (keputusan 22):** kode Fase 3
  TIDAK memanggil `PuspaRegistry.get_definitions` / `bind_turn` /
  `prepare_call` sama sekali (grep di `puspa/` + `tests/` kosong) —
  perubahan visibilitas tool tidak memengaruhi Fase 3. Kalau Fase 4
  memakai registry, ikat token dulu (`bind_turn`) sebelum berharap
  ada definisi tool.
- **Sisa celah `prepare_call`:** pesan error tool tidak dikenal masih
  mencantumkan daftar `tool_names` lengkap — Fase 3 TIDAK
  memperkenalkan kembali pola ini (tidak ada pesan error di Fase 3
  yang membocorkan daftar tool); penutupan celahnya tindak lanjut
  wrap-up (jatah Fase 2).
- **REUSE.md Fase 0 belum mencatat pengaruh desain** (OpenBot,
  E2B/Firecracker, agentskills, ECC, hermes) — bukan tugas Fase 3;
  dicatat sebagai tindak lanjut wrap-up (audit lengkap di
  `build/fase-0/REUSE-AUDIT.md`).
- **Poles CLI (jatah Fase 2):** log loguru nanobot menyela spinner
  saat pemakaian CLI interaktif — Fase 3 tidak memakai CLI interaktif
  dalam pengujian, jadi tidak ditemui langsung; dicatat sebagai
  tindak lanjut wrap-up, bukan scope Fase 3.
- **LangGraph:** nol import, nol klaim di Fase 3 (grep kosong).

## Yang TIDAK dibangun (sengaja, sesuai definisi)
- Jaringan anti-penipuan + buku besar harga laku (§3.7) — tetap
  tidak dibangun.
- Fashion + barang non-standar di wedge (§4.1, §6) — ditolak dengan
  pesan jelas, bukan dibangun diam-diam.
- BPJS (§4.2) — ditolak tegas.
- Langflow (Fase 4), voice Pipecat/LiveKit (pasca-MVP).
- Pembayaran QRIS penuh / eksekusi bayar (§6) — hanya mode bayar
  aman (draf).
- Kaset Fase 3, panel Skor Keandalan PWA penuh, parser cron penuh —
  tindak lanjut (lihat atas).

## Insiden / catatan lingkungan selama Fase 3
- Postgres tetap hidup sepanjang Fase 3 (tidak ada insiden hilang
  total seperti Fase 1).
- Run pytest paralel TIDAK dipakai (semua angka dari run solo).
- Playwright: unduhan Chromium 1255 dari CDN Playwright gagal 3x —
  akar masalah terdiagnosis (proxy egress menolak host
  `playwright.download.prss.microsoft.com`, 400); solusi jalan lewat
  Chrome for Testing 159 dari `storage.googleapis.com` + seam env
  `PUSPA_CHROMIUM_EXECUTABLE` (reader membaca situs uji lokal,
  Rp132.500 terekstrak — teruji).
- Fase 0 naik 244 → 248 passed karena 4 tes regresi registry baru
  (keputusan 22) — diverifikasi, bukan artefak run.
