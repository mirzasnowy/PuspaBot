import { useEffect, useState } from "react";
import {
  answerMission,
  cancelMission,
  createMission,
  fetchMissions,
  runMission,
  type Mission,
  type MissionStep,
} from "../api/missions";

function stepLabel(status: MissionStep["status"]): string {
  switch (status) {
    case "done":
      return "Selesai";
    case "active":
      return "Berjalan";
    case "failed":
      return "Gagal";
    case "skipped":
      return "Dilewati";
    default:
      return "Menunggu";
  }
}

function missionLabel(status: Mission["status"]): string {
  switch (status) {
    case "waiting":
      return "Menunggu jawabanmu";
    case "done":
      return "Selesai";
    case "failed":
      return "Gagal";
    case "stopped":
      return "Dihentikan";
    default:
      return "Berjalan";
  }
}

function openQuestion(mission: Mission): MissionStep | null {
  return mission.steps.find((s) => s.status === "active" && s.question && !s.answer) ?? null;
}

export function MissionsSection() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [goal, setGoal] = useState("");
  const [stepsText, setStepsText] = useState("");
  const [creating, setCreating] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchMissions()
      .then((list) => {
        if (!cancelled) setMissions(list.missions);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Gagal memuat misi.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onCreate(event: React.FormEvent) {
    event.preventDefault();
    const goalValue = goal.trim();
    const steps = stepsText
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
    if (!goalValue || steps.length === 0 || creating) return;
    setCreating(true);
    setError(null);
    try {
      const mission = await createMission({ goal: goalValue, steps });
      setMissions((prev) => [mission, ...prev]);
      setGoal("");
      setStepsText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal membuat misi.");
    } finally {
      setCreating(false);
    }
  }

  async function refresh(missionId: string, work: () => Promise<Mission | unknown>) {
    setBusyId(missionId);
    setError(null);
    try {
      await work();
      const list = await fetchMissions();
      setMissions(list.missions);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memperbarui misi.");
    } finally {
      setBusyId(null);
    }
  }

  async function onAnswer(mission: Mission, answer: string, go: boolean) {
    await refresh(mission.id, () => answerMission(mission.id, answer, go));
    if (go) setAnswers((prev) => ({ ...prev, [mission.id]: "" }));
  }

  return (
    <div>
      <h2>Misi</h2>
      <p className="muted">
        Satu tujuan multi-langkah yang dikerjakan agen di latar belakang. Centang
        di bawah ini adalah daftar langkahnya; langkah yang butuh keputusan
        bertanya dulu padamu, bukan menebak sendiri.
      </p>
      <form onSubmit={onCreate} className="composer">
        <input
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Contoh: riset harga laptop"
          aria-label="Tujuan misi"
          disabled={creating}
        />
        <textarea
          value={stepsText}
          onChange={(e) => setStepsText(e.target.value)}
          placeholder={"Langkah, satu per baris.\nContoh:\nCari tiga harga\nBandingkan\nBeli"}
          aria-label="Langkah misi, satu per baris"
          disabled={creating}
        />
        <button type="submit" disabled={creating || goal.trim().length === 0}>
          {creating ? "Membuat…" : "Buat misi"}
        </button>
      </form>
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      {missions.length === 0 ? (
        <p className="muted">Belum ada misi.</p>
      ) : (
        missions.map((mission) => {
          const question = openQuestion(mission);
          const busy = busyId === mission.id;
          return (
            <div key={mission.id} className="card">
              <strong>{mission.title}</strong>
              <p className="muted">
                Status: {missionLabel(mission.status)} · {mission.goal}
              </p>
              <ul>
                {mission.steps.map((step, index) => (
                  <li key={`${mission.id}-${index}`}>
                    {step.title} — {stepLabel(step.status)}
                    {step.answer ? ` · jawaban: ${step.answer}` : ""}
                    {step.result ? <p className="muted">{step.result}</p> : null}
                  </li>
                ))}
              </ul>
              {question ? (
                <div>
                  <p>
                    <strong>Pertanyaan:</strong> {question.question}
                  </p>
                  {question.options.map((option) => (
                    <button
                      key={option}
                      type="button"
                      disabled={busy}
                      onClick={() => onAnswer(mission, option, true)}
                    >
                      {option}
                    </button>
                  ))}
                  <input
                    value={answers[mission.id] ?? ""}
                    onChange={(e) =>
                      setAnswers((prev) => ({ ...prev, [mission.id]: e.target.value }))
                    }
                    placeholder="Atau jawab bebas…"
                    aria-label="Jawaban bebas"
                    disabled={busy}
                  />
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onAnswer(mission, answers[mission.id] ?? "", true)}
                  >
                    Lanjutkan
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onAnswer(mission, answers[mission.id] ?? "", false)}
                  >
                    Berhenti
                  </button>
                </div>
              ) : null}
              {mission.status === "active" && !question ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => refresh(mission.id, () => runMission(mission.id))}
                >
                  {busy ? "Mengantre…" : "Jalankan langkah berikut"}
                </button>
              ) : null}
              {mission.status === "active" || mission.status === "waiting" ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => refresh(mission.id, () => cancelMission(mission.id))}
                >
                  Batalkan misi
                </button>
              ) : null}
            </div>
          );
        })
      )}
    </div>
  );
}
