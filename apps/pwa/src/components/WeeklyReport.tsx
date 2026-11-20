import { useEffect, useState } from "react";
import { fetchWeeklyReport, type WeeklyReport as WeeklyReportData } from "../api/client";

/**
 * The weekly report (P-02, plan 12.7), shown on the Insights
 * screen: tasks completed, tasks corrected, and cost incurred
 * in the window, with links to the audit log for every counted
 * event.
 *
 * Time and money saved are shown as "belum diukur" (not
 * measured), not as zero: no savings baseline is recorded yet,
 * so the server reports null and the client says so plainly
 * instead of inventing a figure.
 *
 * All figures come from the server (`GET /api/weekly-report`);
 * the client never re-derives a count.
 */
export function WeeklyReport({
  days,
  loadReport = fetchWeeklyReport,
}: {
  days: number;
  loadReport?: (days: number) => Promise<WeeklyReportData>;
}) {
  const [report, setReport] = useState<WeeklyReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadReport(days)
      .then((data) => {
        if (cancelled) return;
        if (!data || typeof data.tasks_completed !== "number" || !Array.isArray(data.linked_events)) {
          throw new Error("rapor mingguan tidak bisa dibaca dari server");
        }
        setReport(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setReport(null);
          setError(
            `Gagal memuat rapor mingguan: ${err instanceof Error ? err.message : "error tak dikenal"}`,
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days, loadReport]);

  return (
    <section>
      <h2>Rapor mingguan</h2>
      {loading ? <p aria-live="polite" className="muted">Memuat rapor mingguan…</p> : null}
      {error ? (
        <p aria-live="polite" className="error">
          {error}
        </p>
      ) : null}
      {report && !loading ? (
        <div>
          <p>
            {report.tasks_completed} tugas selesai · {report.tasks_corrected} tugas dikoreksi ·{" "}
            {report.approvals_granted} persetujuan diterima, {report.approvals_rejected}{" "}
            ditolak · {report.tasks_stopped} dihentikan, {report.tasks_failed} gagal.
          </p>
          <p>
            Biaya dalam {report.window_days} hari terakhir:{" "}
            {report.cost_label ?? "belum diketahui"}.
          </p>
          <p className="muted">
            Waktu dan uang yang dihemat: belum diukur. {report.savings_note}
          </p>
          <p>
            <a href={report.audit_trail_url}>Lihat audit lengkap</a>
          </p>
          {report.linked_events.length > 0 ? (
            <ul>
              {report.linked_events.map((event) => (
                <li key={event.event_id}>
                  <a href={event.receipt_url}>
                    {event.type} — {event.task_id}
                  </a>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
