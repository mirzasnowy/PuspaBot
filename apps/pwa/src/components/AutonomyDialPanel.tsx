import { useEffect, useState } from "react";
import {
  fetchAutonomyDial,
  setDialCeiling,
  type CeilingName,
  type DialAreaState,
  type DialState,
} from "../api/client";

/**
 * The per-area autonomy dial (P-02, plan 12.7), shown on the
 * Settings screen.
 *
 * Five areas (Money, Outgoing messages, Delete, Schedule,
 * Shopping), three positions each: Ask first, Balanced,
 * Autonomous. Every area starts at Ask first. A raise is only
 * offered after evidence — 10 accepted drafts in a row — and
 * the panel shows the streak and the threshold next to the
 * offer, so the user sees what earned it. Money is locked to
 * Ask first and its other positions are disabled. Lowering is
 * always allowed.
 *
 * All figures come from the server (`GET /api/autonomy-dial`
 * and `POST /api/autonomy-dial/{area}/ceiling`); the client
 * never re-derives a streak or a stage.
 */
const CEILING_LABELS: Record<CeilingName, string> = {
  ask_first: "Tanya dulu",
  balanced: "Seimbang",
  autonomous: "Mandiri",
};

const CEILINGS: CeilingName[] = ["ask_first", "balanced", "autonomous"];

const STAGE_LABELS: Record<string, string> = {
  propose: "Tanya dulu",
  do_and_show: "Jalan dan tampilkan",
  do_silently: "Jalan diam-diam",
};

export function AutonomyDialPanel({
  loadDial = fetchAutonomyDial,
  saveCeiling = setDialCeiling,
}: {
  loadDial?: () => Promise<DialState>;
  saveCeiling?: (area: string, ceiling: CeilingName) => Promise<DialAreaState>;
}) {
  const [state, setState] = useState<DialState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busyArea, setBusyArea] = useState("");

  useEffect(() => {
    let cancelled = false;
    loadDial()
      .then((dial) => {
        if (cancelled) return;
        if (!dial || !Array.isArray(dial.areas)) {
          throw new Error("dial otonomi tidak bisa dibaca dari server");
        }
        setState(dial);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(
            `Gagal memuat dial otonomi: ${err instanceof Error ? err.message : "error tak dikenal"}`,
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadDial]);

  async function changeCeiling(area: DialAreaState, ceiling: CeilingName) {
    if (area.ceiling === ceiling) return;
    setBusyArea(area.area);
    setStatus("");
    setError("");
    try {
      const moved = await saveCeiling(area.area, ceiling);
      setState((current) =>
        current === null
          ? current
          : {
              ...current,
              areas: current.areas.map((item) => (item.area === area.area ? moved : item)),
            },
      );
      setStatus(`${area.label} kini ${CEILING_LABELS[ceiling]}.`);
    } catch (err) {
      setError(
        `Gagal mengubah ${area.label}: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setBusyArea("");
    }
  }

  function raiseDisabled(area: DialAreaState, ceiling: CeilingName): boolean {
    if (area.locked) return true;
    if (busyArea !== "") return true;
    const order = CEILINGS.indexOf(ceiling);
    const current = CEILINGS.indexOf(area.ceiling);
    if (order <= current) return false;
    // A raise needs the suggestion for exactly that next step.
    return area.suggestion?.to_ceiling !== ceiling;
  }

  return (
    <section>
      <h2>Dial otonomi</h2>
      <p className="muted">
        Seberapa mandiri Puspa di tiap area. Semua area mulai dari Tanya dulu; naik hanya
        ditawarkan setelah {state?.threshold_text ?? "10 draf diterima berturut-turut"}. Uang
        terkunci di Tanya dulu dan tidak pernah mandiri.
      </p>
      {loading ? <p aria-live="polite" className="muted">Memuat dial otonomi…</p> : null}
      {error ? (
        <p aria-live="polite" className="error">
          {error}
        </p>
      ) : null}
      {status ? (
        <p aria-live="polite" className="muted">
          {status}
        </p>
      ) : null}
      {state?.areas.map((area) => (
        <fieldset key={area.area} disabled={busyArea === area.area}>
          <legend>
            {area.label}
            {area.locked ? " — Terkunci: selalu tanya dulu" : ""}
          </legend>
          <p className="muted">{area.description}</p>
          <p className="muted">
            {`Saat ini: ${STAGE_LABELS[area.effective_stage] ?? area.effective_stage} · ${area.streak} / ${area.threshold} draf diterima berturut-turut`}
          </p>
          {area.suggestion ? (
            <p>
              {`Siap naik ke ${CEILING_LABELS[area.suggestion.to_ceiling]}: ${area.suggestion.threshold_text} (${area.suggestion.streak} tercapai).`}
            </p>
          ) : null}
          {CEILINGS.map((ceiling) => (
            <label key={ceiling} style={{ display: "inline-block", marginRight: "1rem" }}>
              <input
                type="radio"
                name={`dial-${area.area}`}
                value={ceiling}
                checked={area.ceiling === ceiling}
                disabled={raiseDisabled(area, ceiling)}
                onChange={() => void changeCeiling(area, ceiling)}
              />{" "}
              {CEILING_LABELS[ceiling]}
            </label>
          ))}
        </fieldset>
      ))}
    </section>
  );
}
