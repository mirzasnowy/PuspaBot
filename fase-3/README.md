# Fase 3 — Wedge ganda + Moat (MVP Puspa, revisi 7)

Definisi: revisi 7 (disetujui Nero 17 Nov 2026 ~17:42 WIB).
Timeline 17 minggu DIABAIKAN atas perintah Nero 21:53 WIB (tanggal
hanya urutan, bukan deadline). Kerja lokal di `build/fase-3/`;
JANGAN push ke GitHub; JANGAN mulai Fase 4.

## Yang dibangun

- **Wedge 1 — bandingkan harga** (`puspa/pricing/`): 3 Pembaca
  (Tokopedia, Shopee, Blibli) + 1 Pemeriksa; pencocokan lewat Graf
  Identitas; scope 9 kategori dikunci; fashion ditolak dengan pesan
  jelas; kredit cek detail 1 kredit, 15 gratis/bulan.
- **Graf Identitas dan Harga** (`puspa/graphs/identity.py`): produk
  kanonik + tautan listing (`otomatis`/`dikonfirmasi_user`/
  `ditolak_user`) + deret waktu harga (tanpa `bukti_id` ditolak).
- **Bibit katalog** (`puspa/pricing/catalog.py`): 14 baris bibit
  (13 produk unik, 9 kategori) + 10 barang uji default.
- **Watchlist + sampler** (`puspa/pricing/watchlist.py`): anggaran
  baca maks 20/hari/pengguna; alert harga turun.
- **Cache bukti bersama** (`puspa/pricing/cache.py`): TTL 30 menit;
  Pemeriksa tetap baca ulang independen (tidak pernah pakai cache).
- **Wedge 2 — autopilot tagihan** (`puspa/billing/`): PLN, PDAM,
  pulsa, paket data (TIDAK ADA BPJS); prosedur dari Graf Alur
  Layanan; pengingat jatuh tempo; siapkan bayar mode aman (draf saja).
- **Graf Alur Layanan** (`puspa/graphs/service.py`): siklus
  percobaan → terverifikasi → rusak → perbaikan (3 uji) + rollback;
  canary harian; pakai ulang lintas agen; pola sub-agen tersimpan.
- **Skor Keandalan** (`puspa/moat/reliability.py`): per sumber
  (marketplace x kategori) + per prosedur; < 80% → maksimal
  Belum pasti; label panel Bukti.
- **Lapor Salah** (`puspa/moat/report.py`): tercatat di Buku Kerja
  (`laporan_salah`) + menurunkan skor sumber + memicu baca ulang.
- **Metrik moat gerbang 8** (`puspa/moat/metrics.py`): cakupan
  pencocokan otomatis, 4 prosedur terverifikasi + dipakai ulang,
  rasio token, keandalan semua sumber; metrik alur aktif (Fase 4)
  dilaporkan `tertunda`, tidak diklaim.
- **Pustaka alur** (`puspa/moat/library.py`): fondasi Fase 4
  (alur sukses + Permintaan Blok).
- **Cek syarat layanan** (`puspa/pricing/tos.py`): hasil cek + gerbang
  baca live (lihat STATUS_FASE3.md — temuan terbesar fase ini).

## Menjalankan tes

Semua run SOLO (satu pytest dalam satu waktu — tabrakan TRUNCATE di
DB `puspa_test` yang sama). Untuk semua HTTP keluar pakai
`NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1`.

```sh
cd build/fase-3
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -m pytest tests -q
```

Angka terakhir: Fase 3 **62 passed** (dengan browser Chromium lewat
`PUSPA_CHROMIUM_EXECUTABLE`; 61 passed + 1 skipped tanpa env browser);
Fase 0 248 passed + 4 skipped; Fase 1 58 passed; Fase 2 73 passed
(semua run solo).

## Catatan jujur (ringkas; detail di STATUS_FASE3.md)

- Pembacaan LIVE ketiga marketplace DITAHAN gerbang ToS: syarat
  layanan ketiganya melarang pembacaan otomatis tanpa izin. Wedge
  harga teruji penuh di mode fixture; live menunggu keputusan Nero
  (keputusan 15).
- Tagihan live menunggu nomor pelanggan Nero (keputusan 5); canary +
  uji memakai situs uji lokal.
- Playwright: paket terinstal; browser Chromium tersedia lewat
  Chrome for Testing 159 + env `PUSPA_CHROMIUM_EXECUTABLE`
  (unduhan CDN Playwright diblokir proxy egress — lihat
  STATUS_FASE3.md). Tanpa env, reader gagal terlihat (`browser_gagal`).
- Orkestrasi Puspa = AgentLoop nanobot + siklus tugas Puspa sendiri.
  LangGraph TIDAK dipakai dan tidak diklaim di mana pun.
