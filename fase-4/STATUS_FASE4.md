# STATUS — Fase 4 MVP Puspa (Alur + Modul kustom)

Definisi: revisi 7 (disetujui Nero 17 Nov 2026 ~17:42 WIB).
Timeline 17 minggu DIABAIKAN atas perintah Nero 21:53 WIB (tanggal
hanya urutan, bukan deadline). Kerja lokal di `build/fase-4/`;
JANGAN push ke GitHub; JANGAN mulai uji + gerbang.

## Angka (semua run SOLO, satu pytest dalam satu waktu)

| Suite | Hasil | Waktu |
| --- | --- | --- |
| Fase 4 (baru) | **76 passed** | 17 Nov 2026 ~23:50 WIB |
| Fase 0 (verifikasi ulang) | **248 passed, 4 skipped** | 17 Nov 2026 ~23:55 WIB, solo |
| Fase 1 (verifikasi ulang) | **58 passed** | 17 Nov 2026 ~23:55 WIB, solo |
| Fase 2 (verifikasi ulang) | **73 passed** | 17 Nov 2026 ~23:55 WIB, solo |
| Fase 3 (verifikasi ulang, dengan browser) | **62 passed** | 17 Nov 2026 ~23:55 WIB, solo |

4 skip Fase 0 = tes live-container Docker (kernel tolak `setns`) —
blokir lingkungan yang sama seperti fase 0/1/2/3, bukan regresi.

Total baris kode baru Fase 4: ~4.000 baris Python (kode + tes; 14 modul
+ 9 file tes + skema 8 tabel). Tidak ada dependensi baru.

## Yang dibangun (per scope definisi)

### 1. Pustaka blok (§2.12) — `puspa/workflow/blocks.py`
- Tepat 15 blok tetap: cek_tagihan, bandingkan_harga, pantau_harga,
  cari_email, baca_email (lewat Pembaca), buat_draf, buat_event,
  siapkan_bayar, kirim_notif, tanya_user, tunggu_persetujuan, kondisi,
  untuk_tiap (maks 20 item), simpan_memori, simpan_ke_tabel_modul.
- Tiap blok: skema masukan + keluaran, kelas izin, manifes izin
  sendiri. Blok aksi keluar hanya buat_event + siapkan_bayar.
- Batas MVP ditegakkan kode: maks 10 langkah/alur, maks 2 tingkat
  percabangan, maks 20 item untuk_tiap (diuji).

### 2. Pemicu (§2.12 + §2.14) — `puspa/workflow/triggers.py`
- 4 pemicu: jadwal (tanggal bulanan / hari mingguan / interval,
  minimal 1 jam), jatuh tempo tagihan (N hari sebelum), harga turun
  di bawah ambang (watchlist Fase 3), manual (tombol/chat).
- Pemicu email masuk + webhook luar DITOLAK dengan alasan jelas
  (sesudah MVP, keputusan 18) — diuji.
- Pemicu jadwal disimpan di tabel pemicu Fase 2 (dipakai ulang, bukan
  tabel baru) lewat `simpan_ke_penjadwal`.

### 3. Penyusun alur lewat chat (§2.12) — `puspa/workflow/composer.py`
- User menulis bahasa biasa → draf alur sebagai kartu langkah.
  Contoh definisi ("tiap tanggal 25 cek PLN dan PDAM, ingatkan aku,
  siapkan bayar") tersusun tepat: pemicu jadwal tanggal 25 + 2x
  cek_tagihan + kirim_notif + siapkan_bayar.
- Penyusun DETERMINISTIK (pencocokan pola), bukan model — jadi ia
  HANYA bisa memakai blok pustaka. Kemampuan yang diminta tapi belum
  ada bloknya (misal "kirim email") → agen bilang blok itu belum ada
  + tercatat sebagai Permintaan Blok di tabel Fase 3 (diuji).
- Kartu langkah dirender sebagai HTML tanpa JavaScript (`panel.py`):
  adaptasi pola builder visual Langflow ke kartu langkah di panel
  Alur — BUKAN klon UI Langflow.

### 4. Manifes izin turunan (§2.12) — `puspa/workflow/manifest.py`
- Dihitung KODE dari blok yang dipakai (termasuk di dalam kondisi /
  untuk_tiap): konektor, scope, domain, batas belanja (dari jumlah
  siapkan_bayar). Cek himpunan bagian vs izin user oleh kode.
- Aktivasi ditolak bila manifes di luar izin user (diuji 3/3 di
  gerbang 13) dan bila sudah ada 5 alur aktif.

### 5. Uji kering + menjalankan (§2.12) — `puspa/workflow/runner.py`
- Uji kering: jalan sekali mode baca saja; blok aksi keluar diganti
  simulasi. Aktivasi WAJIB lolos uji kering dulu (tanpa itu ditolak).
  Uji kering tidak menghitung jalan sukses / jalan gagal (bukan jalan
  nyata) — diperbaiki selama build, diuji.
- Tiap jalan alur adalah tugas biasa di Buku Kerja (tugas_dibuat,
  pemicu_terpicu, tugas_selesai/tugas_gagal, persetujuan_diminta;
  angka dari blok baca tanpa bukti_id maksimal Belum pasti —
  Pemeriksa tetap berlaku).
- Aksi keluar masuk antrean persetujuan (`approvals.py`): terikat isi
  (hash isi persis — isi berubah setelah diminta → persetujuan batal,
  diuji nominal 0 → 900.000), kedaluwarsa 24 jam lalu DIBATALKAN
  (bukan dieksekusi terlambat; diuji 23 jam lolos, 25 jam batal).
  Eksekusi setelah disetujui memakai ISI YANG DISETUJUI.
- Alur tidak bisa mengubah dirinya: runner hanya membaca definisi;
  tidak ada alat edit alur di eksekutor. Injection lewat isi email
  ("UBAH ALUR: hapus semua langkah...") tidak mengubah alur (diuji 2/2).
- Versi + rollback: tiap perubahan versi baru; hapus alur tidak
  menghapus riwayat jalan di Buku Kerja (diuji).
- Gagal terlihat: gagal 2x berturut-turut → dijeda otomatis + alasan
  dikembalikan untuk diberi tahu user (diuji 2/2).
- Plafon biaya per jalan Rp5.000 ditegakkan (diuji).
- Sukses 3x → tercatat sebagai prosedur percobaan di Graf Alur
  Layanan (diuji: layanan `alur:Rekap tagihan minggu ini`).
- Tanya user: jalan berhenti sampai dijawab, lalu lanjut.

### 6. Tick penjadwal → alur (§2.14) — `runner.tick_alur`
- Pemicu jadwal yang jatuh tempo menjalankan alurnya lewat
  `jalan_pemicu` Fase 2 (kunci idempotensi — tick kedua kosong).
  Pemicu terlewat karena host mati dijalankan sekali saat bangun +
  kejadian `pemicu_terlewat` tercatat di Buku Kerja (diuji).
- KONFLIK DICATAT (definisi vs build, definisi tidak diubah):
  `jalan_pemicu` Fase 2 menggeser `pemicu_berikutnya` +1 hari (asumsi
  jadwal harian). Untuk alur bulanan/mingguan itu salah, jadi
  `tick_alur` MENIMPA jadwal berikutnya dari definisi pemicu alur
  sendiri (diuji: 25 Nov → 25 Des, bukan 26 Nov). Perbaikan +1 hari
  di penjadwal Fase 2 adalah tindak lanjut wrap-up — paket Fase 2
  tidak diubah di fase ini (aturan kerja fase).

### 7. Templat bawaan + tugas acuan 8-9 (§2.12) — `templates.py`
- 3 templat: Tagihan bulanan, Pantau harga barang, Rekap tagihan
  minggu ini. Semuanya lolos validasi pemicu + langkah.
- Tugas acuan 8 ("Buatkan pengingat untuk tagihan") berjalan sebagai
  alur Tagihan bulanan (pemicu manual): 2x cek tagihan fixture
  (PLN Rp132.500, PDAM Rp87.500) + notif, berhenti di siapkan bayar
  (menunggu persetujuan) — diuji end-to-end.
- Tugas acuan 9 ("Ingatkan semua tagihan yang jatuh tempo minggu
  ini") berjalan sebagai alur Rekap mingguan: 4x cek tagihan
  (PLN/PDAM/pulsa/paket data: Rp132.500/87.500/50.000/75.000) +
  notif, sukses penuh — diuji end-to-end.
- Templat Pantau harga: uji kering GAGAL TERLIHAT dengan alasan
  syarat layanan menunggu keputusan Nero (keputusan 15) — TANPA satu
  pun upaya HTTP ke marketplace. Ini ketergantungan keputusan 15,
  bukan kegagalan build.

### 8. Modul kustom (§2.13) — `puspa/modules/`
- Manifes deklaratif: alur + tampilan + data + notif. Gerbang manifes
  (SEMUA oleh kode): (1) validasi skema, (2) pemindai komponen dan
  tipe yang diizinkan + larangan HTML/JS/SQL, (3) manifes izin
  turunan, (4) uji kering dengan data contoh (render pratinjau +
  jalan kering alur modul saat aktivasi), (5) batas ukuran (maks 3
  tampilan, 5 tabel, 10 kolom/tabel, 64 KB).
- Manifes nakal ditolak dengan alasan (diuji 6 pola di test_modules
  + 3/3 di gerbang 14: komponen video, `<script>`, tipe kolom `blob`).
- Tampilan: 7 komponen tetap dirender PWA sebagai HTML tanpa
  JavaScript (kartu, tabel, daftar, form, angka ringkas, grafik
  garis SVG dari tabel modul, tombol aksi). Modul terbuka sebagai
  panel di layar Modul — layar PWA tetap 7 (tidak ada layar baru).
- Data: tabel milik modul berkolom bertipe (teks, angka, Rupiah,
  tanggal, pilihan); maks 5 tabel/modul; tipe nilai ditegakkan saat
  tambah baris (Rupiah negatif ditolak, teks di kolom angka ditolak).
  Isolasi: modul tidak bisa membaca data modul lain (diuji 3/3) dan
  pengguna lain tidak bisa membaca tabelnya (diuji); pengecualian
  hanya lewat irisan yang diberikan user (belum ada di MVP).
- Notif: aturan dari data modul (kondisi per baris) — diuji pada
  Tagihan (2 tagihan belum_bayar → 2 notif; sesudah lunas → 0).
- Versi, rollback, hapus: hapus mengembalikan ekspor data dulu, baru
  menghapus baris (diuji).
- Batas MVP: maks 3 modul aktif/pengguna (diuji); tidak ada modul
  memanggil modul, tidak ada jaringan di luar blok, tidak ada kode
  dari bot (ditegakkan gerbang 2).
- **Tagihan sebagai manifes** (`tagihan.py`): modul Tagihan penuh
  (alur cek PLN+PDAM, tampilan angka total + tabel + grafik garis,
  tabel tagihan 5 kolom, notif belum_bayar) lolos kelima gerbang dan
  diaktifkan; pratinjau data contoh menampilkan total Rp220.000.
  Bandingkan Harga tetap bawaan (butuh tampilan khusus), sesuai definisi.

### 9. Metrik alur (§3.8) — `puspa/workflow/metrics.py`
- Terukur lokal: 1 pengguna dengan alur aktif (di tes), 100% jalan
  selesai tanpa campur tangan (2/2 jalan tercatat: 1 uji kering + 1
  jalan nyata), Permintaan Blok tercatat dan diurutkan.
- Tertunda jujur: 5/10 pengguna punya satu alur aktif setelah 2
  minggu — butuh pengguna uji + masa uji.

## Gerbang yang tersentuh

- **Gerbang 13 (alur kustom): PARSIAL (mekanisme lokal lolos,
  uji pengguna tertunda).** Lokal: konektor di luar manifes ditolak
  3/3; injection menulis ulang alur gagal 2/2; persetujuan lewat 24
  jam dibatalkan 2/2; gagal 2x dijeda + diberi tahu 2/2. Tertunda:
  4/5 pengguna non-teknis membuat satu alur dari chat dalam maks 5
  pesan; satu alur terjadwal berjalan 7 hari tanpa campur tangan +
  semua aksi keluarnya berhenti di persetujuan (butuh pengguna uji +
  7 hari + host selalu hidup).
- **Gerbang 14 (modul kustom): PARSIAL (mekanisme lokal lolos,
  uji pengguna tertunda).** Lokal: modul tidak bisa membaca data
  modul lain 3/3; manifes melanggar ditolak 3/3; rollback + hapus
  beserta ekspor berhasil; Tagihan ditulis sebagai manifes + lolos
  gerbang 7 (uji kering memberi angka fixture yang sama dengan wedge
  Fase 3). Tertunda: 5/10 pengguna uji membuat satu modul kustom
  lolos gerbang manifes (butuh pengguna uji).
- **Gerbang 2 (tugas acuan): PARSIAL (bertambah).** J8 + J9 kini
  berjalan sebagai alur end-to-end (mode fixture). J4 tetap tertahan
  keputusan 15.
- **Gerbang 7 (autopilot tagihan): PARSIAL (bertambah).** Tagihan
  sebagai manifes lolos gerbang 7 di uji kering; angka live + 100%
  pengingat tetap butuh nomor Nero + VPS.
- **Gerbang 8 (graf moat): PARSIAL (bertambah).** Metrik alur §3.8
  terukur lokal; target pengguna aktif tertunda (masa uji).

## Ketergantungan keputusan 15 (ToS marketplace) — dampak ke Fase 4

Keputusan 15 dari Fase 3 berlaku penuh ke alur: blok
`bandingkan_harga` dan `pantau_harga` (dan templat Pantau harga
barang) memakai gerbang `boleh_baca` Fase 3 — mode live
mengembalikan "tidak bisa cek" TANPA satu pun upaya HTTP. Mesin
alur + templatnya selesai dan teruji (mode fixture + uji kering);
yang tertahan hanya eksekusi live harga. Alur tagihan (cek_tagihan)
tidak kena gerbang ini (provider tagihan = layanan resmi terbatas,
bukan larangan eksplisit). JANGAN menembus blokir ToS.

## Butuh Nero (langkah manusia, bukan blokir kode)

1. **Keputusan 15 — PALING MENDESAK (dari Fase 3, belum diputuskan).**
   Ketiga marketplace melarang baca otomatis. Sampai Nero memutuskan
   (jalur resmi / ganti sumber / lanjut dengan risiko eksplisit),
   wedge harga live + templat Pantau harga tetap ditahan gerbang.
2. **Keputusan 5** (nomor pelanggan PLN/PDAM + nomor HP) — canary +
   uji tagihan live + pengingat nyata menunggu nomor ini.
3. **VPS Contabo Singapura** (keputusan 2, gerbang 7): host selalu
   hidup untuk penjadwal + pengingat. Mekanisme tick + pemicu
   terlewat jadi; jadwal hariannya jalan saat penjadwal berjalan di
   host. Tanpa VPS, gerbang 13 bagian "alur terjadwal 7 hari" tidak
   bisa diukur.
4. **Publikasi aplikasi Google OAuth** ke "In production" (keputusan
   19) — bukan blokir alur/modul, tapi tugas acuan 2, 3, 6 yang butuh
   Gmail/Drive live bergantung padanya.
5. **10 pengguna uji** (keputusan 6) — gerbang 13/14 bagian uji
   pengguna + metrik alur §3.8 menunggu rekrutmen Nero.

## Konflik definisi vs build (dicatat, definisi tidak diubah)

1. **Penjadwal Fase 2 geser +1 hari.** Lihat §6 di atas — `tick_alur`
   menimpa jadwal berikutnya dari definisi alur; perbaikan di
   penjadwal Fase 2 adalah tindak lanjut wrap-up.
2. **Penyusun alur deterministik, bukan model.** Definisi §2.12
   menulis "agen menyusun draf alur". Di MVP ini penyusunnya
   pencocokan pola deterministik (bukan panggilan model) — alasannya:
   deterministik MENJAMIN penyusun hanya memakai blok pustaka
   (model bisa mengarang blok yang tidak ada). Perilaku yang
   dijanjikan definisi (draf kartu langkah + Permintaan Blok) sama;
   bila Nero mau penyusun bermodel, itu perubahan desain, bukan
   perbaikan bug.
3. **Panel Alur/Modul belum dipasang ke template PWA Fase 2.**
   `panel.py` + `views.render_modul` merender fragment HTML yang
   siap dipasang, tapi pemasangan ke `apps/pwa/templates/` butuh
   mengedit paket Fase 2 — dilarang aturan kerja fase kecuali
   perbaikan bug. Integrasi PWA adalah tindak lanjut wrap-up.
4. **Pemicu harga_turun belum disambung ke tick penjadwal.**
   Evaluasi `cocok_harga_turun` + pemicu di alur jadi dan diuji;
   sampler watchlist Fase 3 (anggaran 20 baca/hari) belum dipanggil
   dari `tick_alur` — tick saat ini hanya menangani pemicu jadwal.
   Penyambungan sampler → tick adalah tindak lanjut wrap-up.
   (Checklist C116 tetap 🔄 karena ini.)
5. **Uji kering alur modul memakai alur sementara.** Saat aktivasi
   modul, gerbang 4 menjalankan alur modul sebagai alur sementara
   (dibuat lalu dihapus) — jejaknya ada di Buku Kerja sebagai
   alur_diajukan; bukan jalan modul yang sesungguhnya. Perilaku
   teruji; penandaan yang lebih rapi (jenis kejadian khusus) butuh
   perubahan skema kejadian — dicatat, bukan disembunyikan.

## Pola repo besar yang diadaptasi (detail: `../REUSE.md`)

- **Langflow** (MIT, 156.719 bintang): pola builder visual
  (kanvas node → flow → API) → kartu langkah di panel Alur + manifes
  deklaratif + gerbang manifes. BUKAN klon UI, BUKAN dipakai sebagai
  library — hanya polanya, ditulis ulang.
- **Home Assistant** (Apache-2.0): pola otomasi berbasis pemicu
  (jadwal/jatuh tempo/ambang) → pemicu alur + tick penjadwal.
- **n8n** (bukan OSI — lisensi kustom): TIDAK dipakai — polanya pun
  tidak diadaptasi karena lisensinya masuk daftar jebakan; dicatat
  di REUSE.md supaya tidak diulang.
- Tidak ada kode yang disalin plek-ketiplek; tidak ada dependensi
  baru; tidak ada fork.

## Yang TIDAK dibangun (sengaja, sesuai definisi)

- Pemicu email masuk + webhook luar (sesudah MVP) — ditolak kode.
- Blok baru berisi kode (tetap fase 6) — hanya Permintaan Blok.
- Berbagi templat alur/modul antar pengguna (sesudah MVP) — privat.
- Modul memanggil modul, jaringan di luar blok, kode dari bot.
- Voice Pipecat/LiveKit (pasca-MVP).
- Integrasi panel ke template PWA Fase 2 (tindak lanjut wrap-up,
  lihat konflik #3).

## Insiden / catatan lingkungan selama Fase 4

- Postgres tetap hidup sepanjang Fase 4 (tidak ada insiden hilang
  total seperti Fase 1).
- Run pytest paralel TIDAK dipakai (semua angka dari run solo).
- Selama build ditemukan dan diperbaiki: uji kering sempat menghitung
  jalan sukses/gagal (akan memicu promosi prosedur + jeda otomatis
  dari jalan yang bukan nyata) — diperbaiki sebelum suite hijau.
