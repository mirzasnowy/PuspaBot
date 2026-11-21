/**
 * Diagnostics screen: one-click redacted export for bug reports.
 *
 * The download is a plain link to `GET /api/diagnostics/export`:
 * the server builds the bundle (recent log lines, the message
 * trace, recent ledger events) and redacts every secret before
 * sending it, so the file is safe to paste into a public bug
 * report. Nothing here fetches or renders the bundle itself —
 * the point is the download, not another dashboard.
 */
export function DiagnosticsScreen() {
  return (
    <div>
      <h1>Diagnostik</h1>
      <p className="muted">
        Satu klik untuk mengunduh berkas diagnostik yang aman dibagikan ke laporan bug: log
        terakhir, jejak pesan (langkah-langkah bot), dan kejadian log audit — semuanya sudah
        disensor dulu.
      </p>
      <p>
        <a href="/api/diagnostics/export" download="puspa-diagnostics.json">
          Unduh diagnostik (JSON)
        </a>
      </p>
      <p className="muted">
        Yang tidak pernah ikut: token, API key, kata sandi, header Authorization, alamat email,
        dan isi brankas. Semuanya diganti «redacted» sebelum berkas ditulis, tapi bentuk lognya
        tetap utuh. Unduhan ini tidak merekam isi pesan chat — hanya nama langkah, jenis
        kejadian, dan durasinya.
      </p>
    </div>
  );
}
