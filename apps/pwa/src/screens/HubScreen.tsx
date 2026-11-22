import { useEffect, useState } from "react";
import {
  fetchHubSkills,
  installHubSkill,
  previewHubSkill,
  type HubPreview,
  type HubSkill,
} from "../api/client";

/**
 * Skill marketplace hub (COW-03): browse, search and one-click
 * install over the local skill registry.
 *
 * The list is local only (skills shipped with Puspa plus skills
 * already installed on this machine); the hosted hub server is
 * private by the D-08 split and is not shown here. Installing
 * from GitHub, ClawHub or a URL is always two steps, following
 * CowAgent's own rule: preview first (name, licence, content
 * hash), install only after the user confirms that exact
 * preview. A failed install says so in plain words — the
 * screen never reports a success it did not get.
 */
export function HubScreen() {
  const [query, setQuery] = useState("");
  const [skills, setSkills] = useState<HubSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [preview, setPreview] = useState<HubPreview | null>(null);
  const [installing, setInstalling] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function load(nextQuery: string) {
    setLoading(true);
    setError(null);
    try {
      const body = await fetchHubSkills(nextQuery);
      setSkills(body.skills);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat daftar skill.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load("");
  }, []);

  async function onSearch(event: React.FormEvent) {
    event.preventDefault();
    await load(query);
  }

  async function onPreview(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = source.trim();
    if (!trimmed) {
      setNotice("Isi dulu sumber skillnya.");
      return;
    }
    setNotice(null);
    setPreview(null);
    try {
      const found = await previewHubSkill(trimmed);
      setPreview(found);
      if (!found.license) {
        setNotice(
          `Skill ${found.name} tidak punya lisensi yang terdeteksi, jadi pemasangan akan ditolak.`,
        );
      }
    } catch (err) {
      setNotice(
        `Gagal mengambil pratinjau: ${err instanceof Error ? err.message : "kesalahan tak diketahui"}`,
      );
    }
  }

  async function onInstall() {
    if (!preview) return;
    setInstalling(true);
    setNotice(null);
    try {
      const result = await installHubSkill(source.trim(), preview.sha256);
      setNotice(`Terpasang: ${result.installed.skill_id} v${result.installed.version}.`);
      setPreview(null);
      setSource("");
      await load(query);
    } catch (err) {
      setNotice(
        `Gagal memasang: ${err instanceof Error ? err.message : "kesalahan tak diketahui"}`,
      );
    } finally {
      setInstalling(false);
    }
  }

  return (
    <div>
      <h1>Hub Skill</h1>
      <p className="muted">
        Jelajahi skill yang sudah ada di perangkat ini, cari yang cocok, lalu pasang dalam satu
        klik. Skill baru bisa dipasang dari GitHub, ClawHub, atau tautan SKILL.md langsung.
      </p>
      <form onSubmit={onSearch}>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Cari skill…"
          aria-label="Cari skill"
        />
        <button type="submit">Cari</button>
      </form>
      <h2>Pasang dari sumber lain</h2>
      <form onSubmit={onPreview}>
        <input
          type="text"
          value={source}
          onChange={(event) => setSource(event.target.value)}
          placeholder="owner/repo, clawhub:owner/skill, atau URL SKILL.md"
          aria-label="Sumber skill"
          size={60}
        />
        <button type="submit">Lihat dulu</button>
      </form>
      {preview ? (
        <div>
          <p>
            <strong>{preview.name}</strong> ({preview.slug}) — lisensi{" "}
            {preview.license ?? "tidak diketahui"}.
          </p>
          <p className="muted">{preview.description}</p>
          <button type="button" onClick={onInstall} disabled={installing || !preview.license}>
            {installing ? "Memasang…" : "Pasang skill ini"}
          </button>
        </div>
      ) : null}
      {notice ? <p role="status">{notice}</p> : null}
      <h2>Skill di perangkat ini</h2>
      {loading ? (
        <p className="muted">Memuat…</p>
      ) : error ? (
        <p role="alert">{error}</p>
      ) : skills.length === 0 ? (
        <p className="muted">Belum ada skill yang cocok.</p>
      ) : (
        <ul>
          {skills.map((skill) => (
            <li key={skill.id}>
              <strong>{skill.name}</strong> v{skill.version} · {skill.origin} · {skill.license}
              <br />
              {skill.description}
              <br />
              <span className="muted">
                Sumber: {skill.source}
                {skill.installed ? " · sudah terpasang" : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
