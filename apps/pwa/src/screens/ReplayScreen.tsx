import { useEffect, useRef, useState } from "react";
import {
  fetchReplay,
  fetchReplayTasks,
  replayGifUrl,
  type Bootstrap,
  type ReplayTaskSummary,
  type ReplayTimeline,
} from "../api/client";

/** One step plays for as long as one GIF frame lasts. */
export const REPLAY_PLAY_MS = 800;

const STATUS_LABEL: Record<string, string> = {
  running: "Berjalan",
  completed: "Selesai",
  failed: "Gagal",
  stopped: "Dihentikan",
};

/**
 * Replay timeline of a bot's steps (P-10, plan 12.10).
 *
 * What you see is a task played back step by step, like a
 * video: each step shows what the bot did, why, and what came
 * of it, straight from the ledger. Scrub with the slider or the
 * step buttons to jump to any step, forward or back. The GIF
 * export downloads the whole replay as an animated GIF.
 *
 * Honest limit, same as the live view: there is no screen
 * recording here. The "screenshot" of every step is a frame
 * rendered from the step's own data (step number, event type,
 * tool, reason, result) — not a picture of a desktop, because
 * Puspa has no desktop to watch yet.
 */
export function ReplayScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const agents = bootstrap.agents ?? [];
  const [agentId, setAgentId] = useState(agents[0]?.agent_id ?? "");
  const [tasks, setTasks] = useState<ReplayTaskSummary[]>([]);
  const [taskId, setTaskId] = useState("");
  const [timeline, setTimeline] = useState<ReplayTimeline | null>(null);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!agentId) {
      setTasks([]);
      setTaskId("");
      setTimeline(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    async function loadTasks() {
      setLoading(true);
      try {
        const next = await fetchReplayTasks(agentId);
        if (cancelled) return;
        setTasks(next);
        setTaskId(next[0]?.task_id ?? "");
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Gagal memuat daftar tugas.");
          setTasks([]);
          setTaskId("");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadTasks();
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  useEffect(() => {
    if (!taskId) {
      setTimeline(null);
      setIndex(0);
      setPlaying(false);
      return;
    }
    let cancelled = false;
    async function loadTimeline() {
      setLoading(true);
      try {
        const next = await fetchReplay(agentId, taskId);
        if (cancelled) return;
        setTimeline(next);
        setIndex(0);
        setPlaying(false);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Gagal memuat replay.");
          setTimeline(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadTimeline();
    return () => {
      cancelled = true;
    };
  }, [agentId, taskId]);

  useEffect(() => {
    if (!playing || timeline === null) return;
    timer.current = setInterval(() => {
      setIndex((current) => {
        if (current + 1 >= timeline.step_count) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, REPLAY_PLAY_MS);
    return () => {
      if (timer.current !== null) clearInterval(timer.current);
    };
  }, [playing, timeline]);

  const stepCount = timeline?.step_count ?? 0;
  const step = timeline?.steps[index];
  const atEnd = stepCount > 0 && index >= stepCount - 1;

  function seek(next: number) {
    setPlaying(false);
    setIndex(Math.max(0, Math.min(stepCount - 1, next)));
  }

  return (
    <div>
      <h1>Replay</h1>
      <p className="muted">
        Putar ulang langkah-langkah bot seperti video: tiap langkah lengkap dengan alasan dan
        hasilnya, langsung dari Buku Kerja. Bukan rekaman layar: yang tampil adalah frame hasil
        render dari data kejadian, karena belum ada backend yang bisa menghasilkan tangkapan layar.
      </p>
      {agents.length === 0 ? (
        <p className="muted">Belum ada agen untuk diputar ulang.</p>
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
          </label>{" "}
          <label>
            Tugas{" "}
            <select
              value={taskId}
              disabled={tasks.length === 0}
              onChange={(event) => {
                setTaskId(event.target.value);
                setLoading(true);
              }}
            >
              {tasks.map((task) => (
                <option key={task.task_id} value={task.task_id}>
                  {task.task_id} · {task.event_count} langkah · {STATUS_LABEL[task.status] ?? task.status}
                </option>
              ))}
            </select>
          </label>
          {loading && timeline === null ? <p className="muted">Memuat…</p> : null}
          {error ? (
            <p aria-live="polite" className="error">
              {error}
            </p>
          ) : null}
          {timeline && step ? (
            <>
              <p>
                Status tugas: <strong>{STATUS_LABEL[timeline.status] ?? timeline.status}</strong>
                {" · "}
                {timeline.chain_ok
                  ? "rantai hash utuh"
                  : `rantai hash PUTUS (${timeline.broken_event_ids.length} langkah)`}
              </p>
              <p>
                Langkah {index + 1} dari {stepCount}
                {" · "}
                {step.type}
                {" · "}
                {step.created_at}
                {step.tool ? ` · tool ${step.tool}` : ""}
              </p>
              <p>
                <strong>Alasan:</strong> {step.reason}
              </p>
              <p>
                <strong>Hasil:</strong> {step.result ?? "(belum ada hasil tercatat)"}
              </p>
              <p className="muted">
                hash {step.hash.slice(0, 12) || "-"}
                {" · "}sebelumnya {step.previous_hash.slice(0, 12) || "-"}
              </p>
              <div>
                <button type="button" onClick={() => seek(index - 1)} disabled={index <= 0}>
                  ← Langkah sebelumnya
                </button>{" "}
                <button
                  type="button"
                  onClick={() => {
                    if (atEnd) {
                      setIndex(0);
                      setPlaying(true);
                    } else {
                      setPlaying((was) => !was);
                    }
                  }}
                  disabled={stepCount <= 1}
                >
                  {playing ? "Jeda" : atEnd ? "Putar ulang" : "Putar"}
                </button>{" "}
                <button type="button" onClick={() => seek(index + 1)} disabled={atEnd}>
                  Langkah berikutnya →
                </button>
              </div>
              <label>
                Geser ke langkah{" "}
                <input
                  type="range"
                  min={0}
                  max={stepCount - 1}
                  value={index}
                  aria-label="Geser ke langkah"
                  onChange={(event) => seek(Number(event.target.value))}
                />
              </label>
              <p>
                <a href={replayGifUrl(agentId, taskId)} download={`replay-${taskId}.gif`}>
                  Unduh GIF ({stepCount} frame)
                </a>
              </p>
            </>
          ) : null}
          {!loading && tasks.length === 0 && agentId ? (
            <p className="muted">Agen ini belum punya tugas yang tercatat di Buku Kerja.</p>
          ) : null}
        </>
      )}
    </div>
  );
}
