import { useEffect, useState } from "react";
import {
  createTask,
  fetchGallery,
  fetchHubSkills,
  fetchMockups,
  installHubSkill,
  previewHubSkill,
  publishGalleryWork,
  rateGalleryWork,
  type GalleryWork,
  type MockupSummary,
} from "../../api/client";
import { PageHead, RowItem, useToast } from "./shared";
import { MockupScreen } from "../MockupScreen";
import { BUILTIN_MODULES } from "../../state/modules";

/**
 * Kanvas Desain (B7): the full-screen workspace. It opens from
 * Chat ("Buat Desain") in a new tab, never as a bottom tab, and
 * the shell hides while it is open (body.workspace).
 */
export function StudioScreen() {
  const [saved, setSaved] = useState<MockupSummary[]>([]);
  const [published, setPublished] = useState<string[]>([]);
  const { toast, show } = useToast();

  useEffect(() => {
    document.body.classList.add("workspace");
    fetchMockups()
      .then((body) => setSaved(body.mockups))
      .catch(() => undefined);
    return () => {
      document.body.classList.remove("workspace");
    };
  }, []);

  async function publish(mockup: MockupSummary) {
    try {
      await publishGalleryWork(mockup.id);
      setPublished((prev) => [...prev, mockup.id]);
      show(`"${mockup.title}" terbit di Galeri.`);
    } catch {
      show("Gagal menerbitkan. Coba lagi.");
    }
  }

  return (
    <div className="page">
      <MockupScreen />
      {saved.length > 0 && (
        <section className="card" style={{ marginTop: 12 }}>
          <b>Terbitkan ke Galeri</b>
          <p className="muted" style={{ margin: "6px 0 10px" }}>
            Mockup yang tersimpan bisa diterbitkan sebagai karya di Galeri.
          </p>
          {saved.map((mockup) => (
            <div className="kv" key={mockup.id}>
              <span>{mockup.title}</span>
              {published.includes(mockup.id) ? (
                <a className="btn ghost sm" href="/galeri">
                  Lihat di Galeri
                </a>
              ) : (
                <button
                  className="btn dark sm"
                  type="button"
                  onClick={() => void publish(mockup)}
                >
                  Publikasikan
                </button>
              )}
            </div>
          ))}
        </section>
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

/**
 * Bangun Modul: name + trigger + steps. There is no module
 * registry API, so saving hands the spec to Puspa as a task —
 * the agent builds the module through chat, and the module
 * appears in the Modul list afterwards.
 */
export function ModuleBuilderScreen() {
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState("");
  const [steps, setSteps] = useState("");
  const [busy, setBusy] = useState(false);
  const { toast, show } = useToast();

  async function save() {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      const spec = [
        `Buat modul bernama "${name.trim()}"`,
        trigger.trim() ? `Pemicu: ${trigger.trim()}.` : "",
        steps.trim() ? `Langkah: ${steps.trim()}` : "",
      ]
        .filter(Boolean)
        .join(" ");
      await createTask(spec);
      show("Modul dikirim ke Puspa untuk dibuat.");
      setName("");
      setTrigger("");
      setSteps("");
    } catch {
      show("Gagal mengirim. Coba lagi.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHead title="Bangun Modul" back />
      <div className="card stacked-form">
        <label>
          Nama modul
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Contoh: Pengingat Tagihan"
          />
        </label>
        <label>
          Pemicu
          <input
            className="input"
            value={trigger}
            onChange={(e) => setTrigger(e.target.value)}
            placeholder="Contoh: setiap tanggal 25"
          />
        </label>
        <label>
          Langkah-langkah
          <textarea
            className="input"
            rows={4}
            value={steps}
            onChange={(e) => setSteps(e.target.value)}
            placeholder="Contoh: cek tagihan PLN, lalu minta persetujuan saya"
          />
        </label>
        <button
          className="btn primary block"
          type="button"
          onClick={() => void save()}
          disabled={busy || !name.trim()}
        >
          Simpan lewat Chat
        </button>
      </div>
      {toast && <div className="toast">{toast}</div>}
      <p className="foot-note">
        Modul dibuat lewat chat dari blok yang sudah bersertifikat — tanpa
        kode.
      </p>
      <h2>Modul bawaan</h2>
      {BUILTIN_MODULES.map((m) => (
        <div className="card" key={m.id} style={{ marginBottom: 12 }}>
          <b>{m.name}</b>
          <p style={{ color: "var(--ink-2)", margin: "6px 0 10px" }}>
            {m.description}
          </p>
          <span className="tag green">Tersedia</span>{" "}
          <a className="btn ghost sm" href="/chat">
            Buka di Chat
          </a>
        </div>
      ))}
    </div>
  );
}

/**
 * Galeri Karya (B16): published works with real one-rating-per-
 * user stars. There is no creator-support endpoint, so no
 * "Dukung" button is shown; support in credits stays post-beta.
 */
export function GalleryScreen() {
  const [works, setWorks] = useState<GalleryWork[] | null>(null);
  const [error, setError] = useState("");
  const [rated, setRated] = useState<Record<string, number>>({});
  const { toast, show } = useToast();

  useEffect(() => {
    fetchGallery()
      .then((body) => setWorks(body.works))
      .catch(() => setError("Gagal memuat galeri. Coba lagi."));
  }, []);

  async function rate(work: GalleryWork, rating: number) {
    try {
      await rateGalleryWork(work.id, rating);
      setRated((prev) => ({ ...prev, [work.id]: rating }));
      show(`Kamu memberi ${rating} bintang untuk "${work.title}".`);
    } catch {
      show("Gagal memberi rating. Coba lagi.");
    }
  }

  return (
    <div className="page">
      <PageHead title="Galeri Karya" back />
      {error && <div className="warnbox">{error}</div>}
      {works && works.length === 0 && (
        <p className="muted">
          Belum ada karya yang diterbitkan. Buat desain di Kanvas, lalu
          terbitkan ke sini.
        </p>
      )}
      {works?.map((work) => (
        <div className="card" key={work.id} style={{ marginBottom: 12 }}>
          <b>{work.title}</b>
          <div style={{ color: "var(--ink-2)", fontSize: 13, marginTop: 4 }}>
            {work.template} ·{" "}
            {work.average_rating != null
              ? `${work.average_rating.toFixed(1)} dari ${work.rating_count} rating`
              : "Belum ada rating"}
          </div>
          <div className="stars" style={{ marginTop: 8 }}>
            {[1, 2, 3, 4, 5].map((star) => (
              <button
                key={star}
                type="button"
                aria-label={`Beri ${star} bintang`}
                onClick={() => void rate(work, star)}
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  fontSize: 22,
                  color:
                    (rated[work.id] ?? Math.round(work.average_rating ?? 0)) >=
                    star
                      ? "#F59E0B"
                      : "var(--muted)",
                  padding: 2,
                }}
              >
                ★
              </button>
            ))}
          </div>
          <a
            className="btn ghost sm"
            style={{ marginTop: 8 }}
            href="/studio"
            target="_blank"
            rel="noreferrer"
          >
            Remix di Kanvas
          </a>
        </div>
      ))}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

/** Marketplace: hub skills from the real hub API + Bangun Modul link. */
export function MarketplaceScreen() {
  const [skills, setSkills] = useState<
    {
      id: string;
      name: string;
      description: string;
      source: string;
      price_credits: number;
      installed: boolean;
    }[] | null
  >(null);
  const [error, setError] = useState("");
  const [installed, setInstalled] = useState<string[]>([]);
  const { toast, show } = useToast();

  useEffect(() => {
    fetchHubSkills("")
      .then((body) =>
        setSkills(
          body.skills.map((skill) => ({
            id: skill.id,
            name: skill.name,
            description: skill.description ?? "",
            source: skill.source,
            price_credits: skill.price_credits,
            installed: skill.installed,
          })),
        ),
      )
      .catch(() => setError("Gagal memuat marketplace. Coba lagi."));
  }, []);

  async function install(skill: NonNullable<typeof skills>[number]) {
    try {
      // Install is preview-then-install: the server refuses
      // content that changed since the preview the user saw.
      const preview = await previewHubSkill(skill.source);
      await installHubSkill(skill.source, preview.sha256);
      setInstalled((prev) => [...prev, skill.id]);
      show("Skill dipasang.");
    } catch {
      show("Gagal memasang. Coba lagi.");
    }
  }

  return (
    <div className="page">
      <PageHead title="Marketplace" back />
      {error && <div className="warnbox">{error}</div>}
      {!skills && !error && <p className="muted">Memuat skill…</p>}
      {skills?.map((skill) => (
        <div className="card" key={skill.id} style={{ marginBottom: 12 }}>
          <b>{skill.name}</b>
          {skill.description && (
            <p style={{ color: "var(--ink-2)", margin: "6px 0 10px" }}>
              {skill.description}
            </p>
          )}
          {skill.installed || installed.includes(skill.id) ? (
            <span className="tag green">Terpasang</span>
          ) : (
            <button
              className="btn dark sm"
              type="button"
              onClick={() => void install(skill)}
            >
              Pasang
              {skill.price_credits > 0
                ? ` · ${skill.price_credits} kredit`
                : ""}
            </button>
          )}
        </div>
      ))}
      <div className="card" style={{ marginTop: 12 }}>
        <RowItem icon="box" title="Bangun Modul" href="/modul" />
      </div>
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
