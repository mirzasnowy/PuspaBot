import { useEffect, useState } from "react";
import {
  artifactDownloadUrl,
  fetchArtifacts,
  fetchReplay,
  fetchReplayTasks,
  type ArtifactSummary,
  type Bootstrap,
  type ReplayTaskSummary,
  type ReplayTimeline,
} from "../api/client";

const STATUS_LABEL: Record<string, string> = {
  running: "Berjalan",
  completed: "Selesai",
  failed: "Gagal",
  stopped: "Dihentikan",
};

const KIND_LABEL: Record<string, string> = {
  gif: "GIF",
  markdown: "Markdown",
  text: "Teks",
  json: "JSON",
  image: "Gambar",
  file: "File",
};

type Tab = "artifacts" | "runs";

/** Human file size: 3 B, 2 KB, 5 MB — whole units, no decimals. */
export function formatBytes(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.round(sizeBytes / 1024)} KB`;
  return `${Math.round(sizeBytes / (1024 * 1024))} MB`;
}

/**
 * Artifacts + run-logs browser (CowAgent research).
 *
 * One screen, two tabs. The Artifacts tab lists the files a
 * run produced (name, kind, size, time, run) with an open and
 * a download link per file. The Run Logs tab lists the same
 * runs step by step from the ledger (the replay timeline):
 * each step shows what the agent did, why, and what came of
 * it, with its status and time.
 *
 * Honest limit: this is a browser, not a live tail — it shows
 * what is already recorded. A run that never produced a file
 * has an empty artifacts tab, and a run with no ledger events
 * has no log to show. There is no editor here and no live
 * streaming log; both are follow-ups, not this screen.
 */
export function ArtifactsScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const agents = bootstrap.agents ?? [];
  const [agentId, setAgentId] = useState(agents[0]?.agent_id ?? "");
  const [tab, setTab] = useState<Tab>("artifacts");
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [runs, setRuns] = useState<ReplayTaskSummary[]>([]);
  const [taskId, setTaskId] = useState("");
  const [timeline, setTimeline] = useState<ReplayTimeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!agentId) {
      setArtifacts([]);
      setRuns([]);
      setTaskId("");
      setTimeline(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [nextArtifacts, nextRuns] = await Promise.all([
          fetchArtifacts(agentId),
          fetchReplayTasks(agentId),
        ]);
        if (cancelled) return;
        setArtifacts(nextArtifacts);
        setRuns(nextRuns);
        setTaskId(nextRuns[0]?.task_id ?? "");
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Gagal memuat artefak dan log run.");
          setArtifacts([]);
          setRuns([]);
          setTaskId("");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  useEffect(() => {
    if (!taskId) {
      setTimeline(null);
      return;
    }
    let cancelled = false;
    async function loadTimeline() {
      try {
        const next = await fetchReplay(agentId, taskId);
        if (cancelled) return;
        setTimeline(next);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Gagal memuat log run.");
          setTimeline(null);
        }
      }
    }
    loadTimeline();
    return () => {
      cancelled = true;
    };
  }, [agentId, taskId]);

  return (
    <div>
      <h1>Artefak &amp; Log Run</h1>
      <p className="muted">
        Lihat file yang dihasilkan agent dan langkah-langkah tiap run. Tab Artefak menampilkan
        file (nama, jenis, ukuran, waktu, run) dengan tautan buka dan unduh; tab Log Run
        menampilkan langkah-langkah run dari Buku Kerja, lengkap dengan alasan dan hasilnya.
        Ini penampil, bukan live tail: yang tampil hanya yang sudah tercatat.
      </p>
      {agents.length === 0 ? (
        <p className="muted">Belum ada agen untuk ditampilkan.</p>
      ) : (
        <>
          <label>
            Agen{" "}
            <select
              value={agentId}
              onChange={(event) => {
                setAgentId(event.target.value);
                setTimeline(null);
                setLoading(true);
              }}
            >
              {agents.map((agent) => (
                <option key={agent.agent_id} value={agent.agent_id}>
                  {agent.name}
                </option>
              ))}
            </select>
          </label>
          <div role="tablist" aria-label="Artefak dan log run">
            <button
              type="button"
              role="tab"
              id="artifacts-tab"
              aria-selected={tab === "artifacts"}
              aria-controls="artifacts-panel"
              className={tab === "artifacts" ? undefined : "button-secondary"}
              onClick={() => setTab("artifacts")}
            >
              Artefak
            </button>{" "}
            <button
              type="button"
              role="tab"
              id="runs-tab"
              aria-selected={tab === "runs"}
              aria-controls="runs-panel"
              className={tab === "runs" ? undefined : "button-secondary"}
              onClick={() => setTab("runs")}
            >
              Log Run
            </button>
          </div>
          {loading && artifacts.length === 0 && runs.length === 0 ? (
            <p className="muted">Memuat…</p>
          ) : null}
          {error ? (
            <p aria-live="polite" className="error">
              {error}
            </p>
          ) : null}
          <div
            role="tabpanel"
            id="artifacts-panel"
            aria-labelledby="artifacts-tab"
            hidden={tab !== "artifacts"}
          >
            {tab === "artifacts" && artifacts.length === 0 && !loading ? (
              <p className="muted">
                Agen ini belum menghasilkan artefak. Artefak pertama muncul saat sebuah run
                diekspor (misalnya GIF replay) atau saat agent menyimpan sebuah file.
              </p>
            ) : null}
            {artifacts.map((artifact) => (
              <div key={artifact.artifact_id} className="card">
                <p>
                  <strong>{artifact.name}</strong>
                </p>
                <p className="muted">
                  {KIND_LABEL[artifact.kind] ?? artifact.kind}
                  {" · "}
                  {formatBytes(artifact.size_bytes)}
                  {" · "}
                  {artifact.created_at}
                  {artifact.run_id ? ` · run ${artifact.run_id}` : ""}
                </p>
                <p>
                  <a
                    href={artifactDownloadUrl(agentId, artifact.artifact_id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Buka
                  </a>{" "}
                  <a
                    href={artifactDownloadUrl(agentId, artifact.artifact_id)}
                    download={artifact.name}
                  >
                    Unduh
                  </a>
                </p>
              </div>
            ))}
          </div>
          <div
            role="tabpanel"
            id="runs-panel"
            aria-labelledby="runs-tab"
            hidden={tab !== "runs"}
          >
            {tab === "runs" && (
              <>
                <label>
                  Run{" "}
                  <select
                    value={taskId}
                    disabled={runs.length === 0}
                    onChange={(event) => setTaskId(event.target.value)}
                  >
                    {runs.map((run) => (
                      <option key={run.task_id} value={run.task_id}>
                        {run.task_id} · {run.event_count} langkah ·{" "}
                        {STATUS_LABEL[run.status] ?? run.status}
                      </option>
                    ))}
                  </select>
                </label>
                {runs.length === 0 && !loading ? (
                  <p className="muted">Agen ini belum punya run yang tercatat di Buku Kerja.</p>
                ) : null}
                {timeline ? (
                  <>
                    <p>
                      Status run:{" "}
                      <strong>{STATUS_LABEL[timeline.status] ?? timeline.status}</strong>
                      {" · "}
                      {timeline.step_count} langkah
                      {" · "}
                      {timeline.chain_ok
                        ? "rantai hash utuh"
                        : `rantai hash PUTUS (${timeline.broken_event_ids.length} langkah)`}
                    </p>
                    {timeline.steps.map((step) => (
                      <div key={step.event_id} className="card">
                        <p>
                          <strong>
                            Langkah {step.index + 1}: {step.type}
                          </strong>
                        </p>
                        <p className="muted">
                          {step.created_at}
                          {step.tool ? ` · tool ${step.tool}` : ""}
                        </p>
                        <p>
                          <strong>Alasan:</strong> {step.reason}
                        </p>
                        <p>
                          <strong>Hasil:</strong> {step.result ?? "(belum ada hasil tercatat)"}
                        </p>
                      </div>
                    ))}
                  </>
                ) : null}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
