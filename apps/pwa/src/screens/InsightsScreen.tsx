import { useCallback, useEffect, useState } from "react";
import { formatRupiah } from "../api/client";
import { HintBanner } from "../components/HintBanner";
import { WeeklyReport } from "../components/WeeklyReport";
import {
  fetchInsights,
  hasUsage,
  INSIGHT_DAY_OPTIONS,
  type InsightsReport,
} from "../state/insights";

/**
 * Insights screen (G28): cost and token totals from the usage
 * ledger, over the last 7 / 30 / 90 days.
 *
 * All figures come from the server (`GET /api/insights`); the
 * client never re-derives a cost. Totals are exact Rupiah
 * labels, and the per-1k-token rate is the server's four-decimal
 * label -- small amounts are never shown as an approximate
 * "~$0.00". A user with no recorded turns sees honest zeros,
 * not invented data.
 */
export function InsightsScreen() {
  const [days, setDays] = useState<number>(7);
  const [report, setReport] = useState<InsightsReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (windowDays: number) => {
    setLoading(true);
    setError("");
    try {
      setReport(await fetchInsights(windowDays));
    } catch (err) {
      setReport(null);
      setError(
        `Gagal memuat insights: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    load(days).catch(() => {
      if (!cancelled) setError("Gagal memuat insights.");
    });
    return () => {
      cancelled = true;
    };
  }, [days, load]);

  return (
    <div>
      <h1>Insights</h1>
      {report && !loading ? (
        <HintBanner
          screen="insights"
          billingRows={report.turns}
          taskCostIdr={
            report.top_costly_tasks.length > 0
              ? Math.max(...report.top_costly_tasks.map((task) => task.cost_idr))
              : 0
          }
        />
      ) : null}
      <p className="muted">
        Total token dan biaya dari catatan pemakaian Puspa. Angka di sini hanya menghitung
        putaran yang tercatat; tidak ada estimasi dolar dan tidak ada pembulatan yang
        menyembunyikan biaya kecil.
      </p>

      <fieldset>
        <legend>Rentang waktu</legend>
        {INSIGHT_DAY_OPTIONS.map((option) => (
          <label key={option} style={{ display: "inline-block", marginRight: "1rem" }}>
            <input
              type="radio"
              name="insights-days"
              value={option}
              checked={days === option}
              onChange={() => setDays(option)}
            />{" "}
            {option} hari
          </label>
        ))}
      </fieldset>

      <p aria-live="polite" className="muted">
        {loading
          ? "Memuat insights…"
          : error
            ? error
            : report
              ? `${report.turns} putaran tercatat dalam ${report.window_days} hari terakhir.`
              : ""}
      </p>

      {error && !loading ? (
        <button type="button" onClick={() => void load(days)}>
          Coba lagi
        </button>
      ) : null}

      {report && !loading && !hasUsage(report) ? (
        <div className="card">
          <strong>Belum ada pemakaian tercatat</strong>
          <p>
            Puspa belum mencatat satu putaran pun dalam {report.window_days} hari terakhir.
            Begitu kamu memakai Puspa, token dan biayanya muncul di sini.
          </p>
        </div>
      ) : null}

      {report && !loading && hasUsage(report) ? (
        <div>
          <div className="card">
            <strong>Total biaya: {report.cost.label}</strong>
            <p>
              {report.turns} putaran, {report.tokens.total.toLocaleString("id-ID")} token
              (masuk {report.tokens.input.toLocaleString("id-ID")}, keluar{" "}
              {report.tokens.output.toLocaleString("id-ID")}, cache{" "}
              {report.tokens.cached.toLocaleString("id-ID")}).
            </p>
            <p>
              Rata-rata {report.cost.avg_per_turn_label} per putaran
              {report.cost.per_1k_tokens_label
                ? `, ${report.cost.per_1k_tokens_label}`
                : ", tanpa tarif per 1k token (tidak ada token tercatat)"}
              . Batas biaya per tugas: {formatRupiah(report.task_cost_ceiling_idr)}.
            </p>
            {report.tool_errors > 0 ? (
              <p>Error tool tercatat: {report.tool_errors}.</p>
            ) : null}
          </div>

          <div
            tabIndex={0}
            role="region"
            aria-labelledby="insights-model-caption"
            className="card"
          >
            <table>
              <caption id="insights-model-caption">Biaya per model</caption>
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col">Putaran</th>
                  <th scope="col">Token</th>
                  <th scope="col">Biaya</th>
                  <th scope="col">Pangsa</th>
                </tr>
              </thead>
              <tbody>
                {report.per_model.map((entry) => (
                  <tr key={entry.model}>
                    <th scope="row">{entry.model}</th>
                    <td>{entry.turns}</td>
                    <td>{entry.tokens.toLocaleString("id-ID")}</td>
                    <td>{formatRupiah(entry.cost_idr)}</td>
                    <td>{(entry.share * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {report.per_driver.length > 0 ? (
            <div className="card">
              <strong>Biaya per driver</strong>
              <ul>
                {report.per_driver.map((entry) => (
                  <li key={entry.driver}>
                    {entry.driver}: {formatRupiah(entry.cost_idr)} ({entry.turns} putaran,{" "}
                    {(entry.share * 100).toFixed(1)}%)
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {report.tool_usage.length > 0 ? (
            <div className="card">
              <strong>Pemakaian tool</strong>
              <ul>
                {report.tool_usage.map((entry) => (
                  <li key={entry.tool}>
                    {entry.tool}: {entry.uses} kali
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {report.module_usage.length > 0 ? (
            <div className="card">
              <strong>Pemakaian modul</strong>
              <ul>
                {report.module_usage.map((entry) => (
                  <li key={entry.module_id}>
                    {entry.module_id}: {entry.turns} putaran, {formatRupiah(entry.cost_idr)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {report.top_costly_tasks.length > 0 ? (
            <div className="card">
              <strong>Tugas termahal</strong>
              <ul>
                {report.top_costly_tasks.map((task) => (
                  <li key={task.task_id} title={task.task_id}>
                    {task.task_id.slice(0, 8)}…: {formatRupiah(task.cost_idr)} (
                    {task.turns} putaran, {task.tokens.toLocaleString("id-ID")} token)
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div
            tabIndex={0}
            role="region"
            aria-labelledby="insights-activity-caption"
            className="card"
          >
            <table>
              <caption id="insights-activity-caption">Aktivitas per hari</caption>
              <thead>
                <tr>
                  <th scope="col">Tanggal</th>
                  <th scope="col">Putaran</th>
                  <th scope="col">Token</th>
                  <th scope="col">Biaya</th>
                </tr>
              </thead>
              <tbody>
                {report.activity_per_day.map((day) => (
                  <tr key={day.date}>
                    <th scope="row">{day.date}</th>
                    <td>{day.turns}</td>
                    <td>{day.tokens.toLocaleString("id-ID")}</td>
                    <td>{formatRupiah(day.cost_idr)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      <WeeklyReport days={days} />
    </div>
  );
}
