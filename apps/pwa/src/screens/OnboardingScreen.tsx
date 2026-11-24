import { useEffect, useRef, useState } from "react";
import {
  completeStep1,
  completeStep2,
  completeStep3,
  fetchOnboardingStatus,
  isOnboardingDone,
  isPhaseADone,
  markPhaseADone,
  nextStep,
  runFirstTask,
  startOnboarding,
  CONNECTOR_CHOICES,
  FIRST_TASK_TYPES,
  ONBOARDING_DATA_SOURCES,
  ONBOARDING_NEEDS,
  ONBOARDING_PURPOSES,
  SPEAKING_STYLES,
  describeConnector,
  describeFirstTaskType,
  type ConnectorChoice,
  type FirstTaskResult,
  type FirstTaskType,
  type OnboardingStatus,
} from "../state/onboarding";
import { createAgentFile, type AgentFileResult } from "../api/client";
import { AgentAvatarPhoto, useAvatarStatus } from "../components/AgentAvatar";
import { AgentAvatarChoices } from "../components/AgentAvatarChoices";
import { avatarPhotoSrc } from "../state/avatar";
import { Icon } from "../components/icons";

/**
 * Onboarding (G28 + Space port, Nero 23 Nov): the first-run
 * guided conversation, extended, not duplicated.
 *
 * Phase A (new): welcome (the Space's onboarding screen) →
 * purpose → personalization → data transparency → agent name +
 * auto-written agent file (SOUL.md / IDENTITY.md / USER.md,
 * generated from the answers via POST /api/onboarding/agent-file
 * and shown on screen, so the file can be checked against the
 * answers). Phase A also records G28 step 1 (name + style), so
 * the user is never asked twice.
 *
 * Phase B (G28, unchanged): notification test, connector
 * choice, guided first task. Two honest limits are shown in
 * the open, not hidden:
 * - Step 2: Web Push is not built yet, so the notification test
 *   is recorded as NOT sent. The screen says so.
 * - Step 4: the price-compare / bill wedges are not built yet,
 *   so the first task is a measured stub and its result is
 *   reported as unverified. The screen says so.
 */
type Phase = "welcome" | "purpose" | "personalize" | "data" | "agent" | "g28";

const PHASE_STEPS: { phase: Phase; label: string }[] = [
  { phase: "welcome", label: "Selamat datang" },
  { phase: "purpose", label: "Tujuan" },
  { phase: "personalize", label: "Personalisasi" },
  { phase: "data", label: "Data" },
  { phase: "agent", label: "Nama agent" },
];

export function OnboardingScreen() {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [phase, setPhase] = useState<Phase>("welcome");

  // Phase A answers.
  const [purposes, setPurposes] = useState<string[]>([]);
  const [needs, setNeeds] = useState<string[]>([]);
  const [needsText, setNeedsText] = useState("");
  const [dataConfirmed, setDataConfirmed] = useState(false);
  const [agentName, setAgentName] = useState("Puspa");
  const [agentFile, setAgentFile] = useState<AgentFileResult | null>(null);
  const [fileTab, setFileTab] = useState<"soul" | "identity" | "user">("soul");
  // The avatar photo (Nero, 24 Nov): the preview shows the same
  // photo the chat island and bubbles will show.
  const avatar = useAvatarStatus();

  const [name, setName] = useState("");
  const [style, setStyle] = useState("santai");
  const [nameError, setNameError] = useState("");
  const nameRef = useRef<HTMLInputElement>(null);

  const [connector, setConnector] = useState<ConnectorChoice>("nanti");

  const [taskType, setTaskType] = useState<FirstTaskType>("compare_prices");
  const [target, setTarget] = useState("");
  const [targetError, setTargetError] = useState("");
  const targetRef = useRef<HTMLInputElement>(null);
  const [firstTaskResult, setFirstTaskResult] = useState<FirstTaskResult | null>(null);
  // Step 2's outcome outlives step 2: later step responses do not
  // carry `notification_sent`, so it is kept in its own state and
  // the honest "not sent" note stays visible until onboarding ends.
  const [notificationSent, setNotificationSent] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        let current = await fetchOnboardingStatus();
        if (!current.started) current = await startOnboarding();
        if (!cancelled) {
          setStatus(current);
          setName(current.name ?? "");
          setStyle(current.style ?? "santai");
          if (isOnboardingDone(current)) setPhase("g28");
          else if (isPhaseADone()) setPhase("g28");
          else setPhase("welcome");
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            `Gagal memuat onboarding: ${err instanceof Error ? err.message : "error tak dikenal"}`,
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const step = nextStep(status);
  const done = isOnboardingDone(status);

  function fail(err: unknown, fallback: string) {
    setError(`${fallback}: ${err instanceof Error ? err.message : "error tak dikenal"}`);
  }

  function toggle(list: string[], id: string, setList: (v: string[]) => void) {
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  /** Phase A finish: write the agent file, then record G28 step 1. */
  async function submitAgentFile() {
    if (busy) return;
    if (!agentName.trim()) {
      setError("Nama agent tidak boleh kosong.");
      return;
    }
    if (!name.trim()) {
      setError("Isi dulu namamu di langkah personalisasi.");
      setPhase("personalize");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const needsCombined = [...needs, needsText.trim()].filter(Boolean).join(", ");
      const file = await createAgentFile({
        agent_name: agentName.trim(),
        user_name: name.trim(),
        style,
        purposes,
        needs: needsCombined,
        data_sources: ONBOARDING_DATA_SOURCES.map((s) => s.id),
      });
      setAgentFile(file);
      // Phase A already collected name + style: record G28 step 1
      // with the same answers instead of asking again.
      const updated = await completeStep1({ name: name.trim(), style });
      setStatus(updated);
      markPhaseADone();
    } catch (err) {
      fail(err, "Gagal menulis file agent");
    } finally {
      setBusy(false);
    }
  }

  function continueToG28() {
    setPhase("g28");
  }

  async function submitStep1(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!name.trim()) {
      setNameError("Nama tidak boleh kosong.");
      nameRef.current?.focus();
      return;
    }
    setNameError("");
    setBusy(true);
    setError("");
    try {
      setStatus(await completeStep1({ name: name.trim(), style }));
    } catch (err) {
      fail(err, "Gagal menyimpan langkah 1");
    } finally {
      setBusy(false);
    }
  }

  async function submitStep2() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await completeStep2();
      if (result.notification_sent !== undefined) {
        setNotificationSent(result.notification_sent);
      }
      setStatus(result);
    } catch (err) {
      fail(err, "Gagal menguji notifikasi");
    } finally {
      setBusy(false);
    }
  }

  async function submitStep3(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setStatus(await completeStep3(connector));
    } catch (err) {
      fail(err, "Gagal menyimpan langkah 3");
    } finally {
      setBusy(false);
    }
  }

  async function submitStep4(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!target.trim()) {
      setTargetError("Target tidak boleh kosong.");
      targetRef.current?.focus();
      return;
    }
    setTargetError("");
    setBusy(true);
    setError("");
    try {
      const result = await runFirstTask({ event_type: taskType, target: target.trim() });
      setFirstTaskResult(result);
      setStatus(result.status);
    } catch (err) {
      fail(err, "Gagal menjalankan tugas pertama");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="page">
        <p className="muted">Memuat onboarding…</p>
      </div>
    );
  }

  if (done) {
    return (
      <div className="page">
        <div className="card">
          <strong>Onboarding selesai</strong>
          <p>
            Halo{status?.name ? `, ${status.name}` : ""}. Gaya bicara:{" "}
            {status?.style ?? "santai"}. Konektor: {status?.connector ?? "nanti"}.
            {status?.duration_seconds != null
              ? ` Selesai dalam ${Math.round(status.duration_seconds)} detik.`
              : ""}
          </p>
          <p>
            <a className="btn primary block" href="/chat">
              Mulai chat dengan Puspa
            </a>
          </p>
        </div>
        {firstTaskResult ? (
          <div className="card" style={{ marginTop: 12 }}>
            <strong>Tugas pertama selesai (belum terverifikasi)</strong>
            <p>{firstTaskResult.reason}</p>
            <p>
              Setelah ini, tiga alur bawaan tersedia:{" "}
              {firstTaskResult.offered_workflow_templates.join(", ")}.
            </p>
          </div>
        ) : null}
      </div>
    );
  }

  const phaseIndex = PHASE_STEPS.findIndex((p) => p.phase === phase);

  return (
    <div className="page">
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}

      {phase !== "g28" && (
        <div className="onb-steps" aria-label="Kemajuan onboarding">
          {PHASE_STEPS.map((p, i) => (
            <span
              key={p.phase}
              className={i < phaseIndex ? "done" : i === phaseIndex ? "now" : ""}
            >
              {p.label}
            </span>
          ))}
        </div>
      )}

      {notificationSent === false ? (
        <p>
          Uji notifikasi tercatat sebagai tidak terkirim: Web Push belum dibangun, jadi
          pengingat untuk sekarang hanya tampil di aplikasi.
        </p>
      ) : null}

      {phase === "welcome" && (
        <div style={{ textAlign: "center" }}>
          <img
            src="/static/puspa-mascot-hero.png"
            alt="Maskot Puspa"
            style={{ width: 180, height: 180, borderRadius: "50%" }}
          />
          <h1 className="page">Selamat Datang</h1>
          <p className="muted">
            Asisten pribadi yang kamu jalankan sendiri — cek harga, bayar tagihan, dan
            ingat semuanya untukmu.
          </p>
          <div className="card" style={{ textAlign: "left" }}>
            <div className="kv">
              <span>
                <Icon name="bolt" size={18} /> Cek harga &amp; bayar tagihan
              </span>
            </div>
            <div className="kv">
              <span>
                <Icon name="memory" size={18} /> Ingatan yang bisa kamu atur
              </span>
            </div>
            <div className="kv">
              <span>
                <Icon name="users" size={18} /> Tim agen yang kerja bareng
              </span>
            </div>
          </div>
          <button
            className="btn primary block"
            type="button"
            onClick={() => setPhase("purpose")}
            style={{ marginTop: 12 }}
          >
            Mulai Sekarang
          </button>
          <a className="btn ghost block" href="/auth/google" style={{ marginTop: 8 }}>
            Masuk dengan Google
          </a>
          <p className="foot-note">
            Gratis 15 kredit/bulan · Tanpa kartu kredit · Mode lokal (self-host) atau
            cloud
          </p>
        </div>
      )}

      {phase === "purpose" && (
        <div>
          <h1 className="page">Untuk apa Puspa dipakai?</h1>
          <p className="muted">Pilih semua yang cocok — ini jadi bekal agentmu.</p>
          <div className="choice-grid">
            {ONBOARDING_PURPOSES.map((purpose) => (
              <button
                key={purpose.id}
                type="button"
                className={`choice-card${purposes.includes(purpose.id) ? " on" : ""}`}
                aria-pressed={purposes.includes(purpose.id)}
                onClick={() => toggle(purposes, purpose.id, setPurposes)}
              >
                <b>{purpose.label}</b>
                <span>{purpose.description}</span>
              </button>
            ))}
          </div>
          <button
            className="btn primary block"
            type="button"
            onClick={() => setPhase("personalize")}
            disabled={purposes.length === 0}
            style={{ marginTop: 12 }}
          >
            Lanjut
          </button>
        </div>
      )}

      {phase === "personalize" && (
        <div>
          <h1 className="page">Personalisasi</h1>
          <p className="muted">Ceritakan sedikit tentangmu, supaya Puspa bicara dengan caramu.</p>
          <div className="card stacked-form">
            <label>
              Nama kamu <span aria-hidden="true">*</span>
              <input
                ref={nameRef}
                className="input"
                type="text"
                value={name}
                autoComplete="name"
                required
                aria-invalid={nameError ? true : undefined}
                onChange={(event) => {
                  setName(event.target.value);
                  if (nameError) setNameError("");
                }}
              />
            </label>
            {nameError ? (
              <p id="onboarding-name-error" role="alert">
                {nameError}
              </p>
            ) : null}
            <fieldset>
              <legend>Gaya bicara</legend>
              {SPEAKING_STYLES.map((option) => (
                <label key={option.id} style={{ display: "block", marginBottom: "0.5rem" }}>
                  <input
                    type="radio"
                    name="speaking-style"
                    value={option.id}
                    checked={style === option.id}
                    onChange={() => setStyle(option.id)}
                  />{" "}
                  {option.name}
                  <span className="muted"> — {option.description}</span>
                </label>
              ))}
            </fieldset>
            <fieldset>
              <legend>Kebutuhanmu (boleh lebih dari satu)</legend>
              <div className="choice-grid">
                {ONBOARDING_NEEDS.map((need) => (
                  <button
                    key={need}
                    type="button"
                    className={`choice-card${needs.includes(need) ? " on" : ""}`}
                    aria-pressed={needs.includes(need)}
                    onClick={() => toggle(needs, need, setNeeds)}
                  >
                    <b>{need}</b>
                  </button>
                ))}
              </div>
              <input
                className="input"
                type="text"
                value={needsText}
                onChange={(e) => setNeedsText(e.target.value)}
                placeholder="Lainnya, tulis sendiri…"
                style={{ marginTop: 8 }}
              />
            </fieldset>
          </div>
          <button
            className="btn primary block"
            type="button"
            onClick={() => {
              if (!name.trim()) {
                setNameError("Nama tidak boleh kosong.");
                nameRef.current?.focus();
                return;
              }
              setPhase("data");
            }}
            style={{ marginTop: 12 }}
          >
            Lanjut
          </button>
        </div>
      )}

      {phase === "data" && (
        <div>
          <h1 className="page">Data apa saja yang dipakai?</h1>
          <p className="muted">
            Puspa hanya memakai data di bawah ini — dan hanya yang kamu pakai. Kata sandi
            dan kode OTP tidak pernah dibaca agent.
          </p>
          <div className="card">
            {ONBOARDING_DATA_SOURCES.map((source) => (
              <div className="kv" key={source.id}>
                <span>
                  <b>{source.label}</b>
                  <br />
                  <small style={{ color: "var(--muted)" }}>{source.description}</small>
                </span>
              </div>
            ))}
          </div>
          <label className="card" style={{ display: "flex", gap: 10, marginTop: 12 }}>
            <input
              type="checkbox"
              checked={dataConfirmed}
              onChange={(e) => setDataConfirmed(e.target.checked)}
            />
            <span>Aku mengerti data apa yang dipakai Puspa.</span>
          </label>
          <button
            className="btn primary block"
            type="button"
            onClick={() => setPhase("agent")}
            disabled={!dataConfirmed}
            style={{ marginTop: 12 }}
          >
            Lanjut
          </button>
        </div>
      )}

      {phase === "agent" && (
        <div>
          <h1 className="page">Beri nama agentmu</h1>
          <p className="muted">
            Nama ini dipakai di avatar dan header chat, dan ditulis ke file agentmu —
            otomatis, kamu tidak perlu menulis file sendiri.
          </p>
          <div className="card stacked-form">
            <label>
              Nama agent
              <input
                className="input"
                type="text"
                value={agentName}
                onChange={(e) => setAgentName(e.target.value)}
                placeholder="Puspa"
              />
            </label>
            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <AgentAvatarPhoto
                state="idle"
                photoSrc={avatarPhotoSrc(avatar)}
                size={56}
              />
              <span className="muted">
                Ini avatar {agentName.trim() || "Puspa"} — bergerak saat ia bekerja,
                diam saat idle.
              </span>
            </div>
            <h3 style={{ margin: "14px 0 4px" }}>Pilih avatar agent</h3>
            <p className="muted" style={{ margin: "0 0 6px" }}>
              Bunga Puspa default, atau satu dari empat bunga ini — avatar yang sama
              dipakai di header chat dan di bubble chat.
            </p>
            <AgentAvatarChoices />
            {!agentFile && (
              <button
                className="btn primary block"
                type="button"
                onClick={() => void submitAgentFile()}
                disabled={busy || !agentName.trim()}
              >
                {busy ? "Menulis file agent…" : "Buat File Agent"}
              </button>
            )}
          </div>
          {agentFile && (
            <div className="card" style={{ marginTop: 12 }}>
              <b>File agentmu sudah ditulis</b>
              <p className="muted" style={{ margin: "6px 0 10px" }}>
                Dari jawaban onboardingmu — cek isinya, cocokkan dengan jawabanmu.
              </p>
              <div className="seg" role="tablist" aria-label="File agent">
                {(["soul", "identity", "user"] as const).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={fileTab === tab}
                    className={fileTab === tab ? "on" : ""}
                    onClick={() => setFileTab(tab)}
                  >
                    {tab === "soul"
                      ? "SOUL.md"
                      : tab === "identity"
                        ? "IDENTITY.md"
                        : "USER.md"}
                  </button>
                ))}
              </div>
              <pre className="agent-md">{agentFile[fileTab]}</pre>
              <button
                className="btn primary block"
                type="button"
                onClick={continueToG28}
                style={{ marginTop: 12 }}
              >
                Lanjut ke Notifikasi
              </button>
            </div>
          )}
        </div>
      )}

      {phase === "g28" && (
        <div>
          <p aria-live="polite" className="muted">
            {step ? `Langkah ${step} dari 4.` : "Onboarding selesai."}
          </p>
          {step === 1 && (
            <form onSubmit={submitStep1} aria-labelledby="onboarding-step1-heading">
              <h2 id="onboarding-step1-heading">Langkah 1: nama dan gaya bicara</h2>
              <div className="card stacked-form">
                <label htmlFor="onboarding-name">
                  Nama kamu <span aria-hidden="true">*</span>
                  <input
                    ref={nameRef}
                    id="onboarding-name"
                    name="name"
                    type="text"
                    className="input"
                    value={name}
                    autoComplete="name"
                    enterKeyHint="next"
                    required
                    aria-invalid={nameError ? true : undefined}
                    aria-describedby={nameError ? "onboarding-name-error" : undefined}
                    onChange={(event) => {
                      setName(event.target.value);
                      if (nameError) setNameError("");
                    }}
                  />
                </label>
                {nameError ? (
                  <p id="onboarding-name-error" role="alert">
                    {nameError}
                  </p>
                ) : null}
                <fieldset>
                  <legend>Pilih satu gaya bicara</legend>
                  {SPEAKING_STYLES.map((option) => (
                    <label key={option.id} style={{ display: "block", marginBottom: "0.5rem" }}>
                      <input
                        type="radio"
                        name="speaking-style"
                        value={option.id}
                        checked={style === option.id}
                        onChange={() => setStyle(option.id)}
                      />{" "}
                      {option.name}
                      <span className="muted"> — {option.description}</span>
                    </label>
                  ))}
                </fieldset>
                <button type="submit" className="btn primary block" disabled={busy}>
                  {busy ? "Menyimpan…" : "Lanjut ke langkah 2"}
                </button>
              </div>
            </form>
          )}
          {step === 2 && (
            <div className="card">
              <h2>Langkah 2: uji notifikasi</h2>
              <p>
                Puspa menguji apakah notifikasi bisa menjangkau kamu. Uji ini dicatat apa
                adanya: kalau belum bisa terkirim, layar ini mengatakannya, bukan pura-pura
                terkirim.
              </p>
              {status?.test_notification ? (
                <p className="muted">{status.test_notification}</p>
              ) : null}
              <button
                type="button"
                className="btn primary block"
                onClick={() => void submitStep2()}
                disabled={busy}
              >
                {busy ? "Menguji…" : "Izinkan notifikasi + uji kirim"}
              </button>
            </div>
          )}
          {step === 3 && (
            <form onSubmit={submitStep3} aria-labelledby="onboarding-step3-heading">
              <h2 id="onboarding-step3-heading">Langkah 3: sambungkan konektor</h2>
              <fieldset>
                <legend>Pilih konektor pertama (boleh dilewati)</legend>
                {CONNECTOR_CHOICES.map((choice) => (
                  <label key={choice} style={{ display: "block", marginBottom: "0.5rem" }}>
                    <input
                      type="radio"
                      name="connector"
                      value={choice}
                      checked={connector === choice}
                      onChange={() => setConnector(choice)}
                    />{" "}
                    {choice === "nanti" ? "Nanti saja" : choice}
                    <span className="muted"> — {describeConnector(choice)}</span>
                  </label>
                ))}
              </fieldset>
              <button type="submit" className="btn primary block" disabled={busy}>
                {busy ? "Menyimpan…" : "Lanjut ke langkah 4"}
              </button>
            </form>
          )}
          {step === 4 && (
            <div>
              <form onSubmit={submitStep4} aria-labelledby="onboarding-step4-heading">
                <h2 id="onboarding-step4-heading">Langkah 4: tugas pertama terbimbing</h2>
                <fieldset>
                  <legend>Pilih tugas pertama</legend>
                  {FIRST_TASK_TYPES.map((type) => (
                    <label key={type} style={{ display: "block", marginBottom: "0.5rem" }}>
                      <input
                        type="radio"
                        name="first-task-type"
                        value={type}
                        checked={taskType === type}
                        onChange={() => setTaskType(type)}
                      />{" "}
                      {describeFirstTaskType(type)}
                    </label>
                  ))}
                </fieldset>
                <div>
                  <label htmlFor="onboarding-target">
                    {taskType === "compare_prices" ? "Barang yang dicari" : "Tagihan yang dicek"}{" "}
                    <span aria-hidden="true">*</span>
                  </label>
                  <input
                    ref={targetRef}
                    id="onboarding-target"
                    name="target"
                    type="text"
                    className="input"
                    value={target}
                    placeholder={taskType === "compare_prices" ? "Asus Vivobook 14" : "PLN"}
                    enterKeyHint="done"
                    required
                    aria-invalid={targetError ? true : undefined}
                    aria-describedby={targetError ? "onboarding-target-error" : undefined}
                    onChange={(event) => {
                      setTarget(event.target.value);
                      if (targetError) setTargetError("");
                    }}
                  />
                  {targetError ? (
                    <p id="onboarding-target-error" role="alert">
                      {targetError}
                    </p>
                  ) : null}
                </div>
                <button type="submit" className="btn primary block" disabled={busy}>
                  {busy ? "Menjalankan…" : "Jalankan tugas pertama"}
                </button>
              </form>
            </div>
          )}
          {firstTaskResult ? (
            <div className="card">
              <strong>Tugas pertama selesai (belum terverifikasi)</strong>
              <p>{firstTaskResult.reason}</p>
              <p>
                Setelah ini, tiga alur bawaan tersedia:{" "}
                {firstTaskResult.offered_workflow_templates.join(", ")}.
              </p>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
