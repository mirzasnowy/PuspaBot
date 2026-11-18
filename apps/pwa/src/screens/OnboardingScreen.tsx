import { useEffect, useRef, useState } from "react";
import {
  completeStep1,
  completeStep2,
  completeStep3,
  fetchOnboardingStatus,
  isOnboardingDone,
  nextStep,
  runFirstTask,
  startOnboarding,
  CONNECTOR_CHOICES,
  FIRST_TASK_TYPES,
  SPEAKING_STYLES,
  describeConnector,
  describeFirstTaskType,
  type ConnectorChoice,
  type FirstTaskResult,
  type FirstTaskType,
  type OnboardingStatus,
} from "../state/onboarding";

/**
 * Onboarding screen (G28): the first-run guided conversation.
 *
 * One conversation, four steps: name + speaking style, a
 * notification test, a connector choice, and a guided first
 * task. There is no upfront tour: the user answers one step at
 * a time, and each step is saved to the server before the next
 * one opens.
 *
 * Two honest limits are shown in the open, not hidden:
 * - Step 2: Web Push is not built yet, so the notification test
 *   is recorded as NOT sent. The screen says so.
 * - Step 4: the price-compare / bill wedges are not built yet,
 *   so the first task is a measured stub and its result is
 *   reported as unverified. The screen says so.
 */
export function OnboardingScreen() {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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

  return (
    <div>
      <h1>Selamat datang di Puspa</h1>
      <p className="muted">
        Pengenalan singkat, sekitar 5 menit: empat langkah, lalu satu tugas pertama yang
        terbimbing. Tidak ada tur panjang di depan; kamu jawab satu langkah, Puspa
        menyimpan, lalu lanjut.
      </p>

      <p aria-live="polite" className="muted">
        {loading
          ? "Memuat onboarding…"
          : error
            ? error
            : done
              ? "Onboarding selesai."
              : `Langkah ${step} dari 4.`}
      </p>

      {notificationSent === false ? (
        <p>
          Uji notifikasi tercatat sebagai tidak terkirim: Web Push belum dibangun, jadi
          pengingat untuk sekarang hanya tampil di aplikasi.
        </p>
      ) : null}

      {loading ? null : done ? (
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
            <a href="/chat">Mulai chat dengan Puspa</a>
          </p>
        </div>
      ) : step === 1 ? (
        <form onSubmit={submitStep1} aria-labelledby="onboarding-step1-heading">
          <h2 id="onboarding-step1-heading">Langkah 1: nama dan gaya bicara</h2>
          <div>
            <label htmlFor="onboarding-name">
              Nama kamu <span aria-hidden="true">*</span>
            </label>
            <input
              ref={nameRef}
              id="onboarding-name"
              name="name"
              type="text"
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
            {nameError ? (
              <p id="onboarding-name-error" role="alert">
                {nameError}
              </p>
            ) : null}
          </div>
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
                <br />
                <span className="muted">Contoh: “{option.examples[0]}”</span>
              </label>
            ))}
          </fieldset>
          <button type="submit" disabled={busy}>
            {busy ? "Menyimpan…" : "Lanjut ke langkah 2"}
          </button>
        </form>
      ) : step === 2 ? (
        <div className="card">
          <h2>Langkah 2: uji notifikasi</h2>
          <p>
            Puspa menguji apakah notifikasi bisa menjangkau kamu. Uji ini dicatat apa
            adanya: kalau belum bisa terkirim, layar ini mengatakannya, bukan pura-pura
            terkirim.
          </p>
          {status?.test_notification ? <p className="muted">{status.test_notification}</p> : null}
          <button type="button" onClick={submitStep2} disabled={busy}>
            {busy ? "Menguji…" : "Izinkan notifikasi + uji kirim"}
          </button>
        </div>
      ) : step === 3 ? (
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
          <button type="submit" disabled={busy}>
            {busy ? "Menyimpan…" : "Lanjut ke langkah 4"}
          </button>
        </form>
      ) : step === 4 ? (
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
                value={target}
                placeholder={
                  taskType === "compare_prices" ? "Asus Vivobook 14" : "PLN"
                }
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
            <button type="submit" disabled={busy}>
              {busy ? "Menjalankan…" : "Jalankan tugas pertama"}
            </button>
          </form>
        </div>
      ) : null}

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
  );
}
