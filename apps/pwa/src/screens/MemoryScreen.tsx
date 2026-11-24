import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { Bootstrap } from "../api/client";
import {
  addGraphEdge,
  addGraphNode,
  ApiError,
  changeGraphEdge,
  deleteGraphEdge,
  deleteGraphNode,
  fetchGraphNode,
  fetchGraphPage,
  renameGraphNode,
  type GraphEdge,
  type GraphNodeDetail,
  type GraphPage,
} from "../api/client";
import { GraphView } from "../components/GraphView";
import { activeFacts, normalizeFacts, ORIGIN_LABELS, proposals } from "../state/memory";
import { download } from "./space/MemoryExtra";
import { PageHead, RowItem } from "./space/shared";
import { useLanguage } from "../state/language";

/** Catalog key per fact origin (the ORIGIN_LABELS value is the fallback). */
const ORIGIN_KEYS: Record<string, string> = {
  user_stated: "ui.memory.origin_user_stated",
  user_confirmed: "ui.memory.origin_user_confirmed",
  inferred: "ui.memory.origin_inferred",
  unknown: "ui.memory.origin_unknown",
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function MemoryScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const { t } = useLanguage();
  const facts = normalizeFacts(bootstrap.profile ?? []);
  const waiting = proposals(facts);
  const active = activeFacts(facts);

  const [page, setPage] = useState<GraphPage | null>(null);
  const [graphError, setGraphError] = useState("");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [detail, setDetail] = useState<GraphNodeDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [editError, setEditError] = useState("");
  const [editNote, setEditNote] = useState("");
  const [graphFullscreen, setGraphFullscreen] = useState(false);
  const [newNodeName, setNewNodeName] = useState("");
  const [newNodeKind, setNewNodeKind] = useState("");
  const [renameTo, setRenameTo] = useState("");
  const [edgeSubject, setEdgeSubject] = useState("");
  const [edgeRelation, setEdgeRelation] = useState("");
  const [edgeObject, setEdgeObject] = useState("");

  const refreshPage = useCallback(async () => {
    try {
      setPage(await fetchGraphPage({ limit: 50 }));
      setGraphError("");
    } catch (error) {
      setGraphError(errorText(error, t("ui.memory.load_failed", "Gagal memuat graf.")));
    }
  }, []);

  useEffect(() => {
    void refreshPage();
  }, [refreshPage]);

  const selectNode = useCallback(async (name: string) => {
    setSelectedName(name);
    setRenameTo(name);
    try {
      setDetail(await fetchGraphNode(name));
      setDetailError("");
    } catch (error) {
      setDetail(null);
      setDetailError(errorText(error, t("ui.memory.load_failed", "Gagal memuat graf.")));
    }
  }, []);

  const afterEdit = useCallback(
    async (note: string) => {
      setEditNote(note);
      setEditError("");
      try {
        const fresh = await fetchGraphPage({ limit: 50 });
        setPage(fresh);
        setGraphError("");
        // Renaming or deleting a node closes its note instead of
        // re-selecting a name that no longer exists.
        if (selectedName && fresh.nodes.some((node) => node.name === selectedName)) {
          await selectNode(selectedName);
        } else if (selectedName) {
          setSelectedName(null);
          setDetail(null);
        }
      } catch (error) {
        setGraphError(errorText(error, t("ui.memory.load_failed", "Gagal memuat graf.")));
      }
    },
    [selectNode, selectedName],
  );

  const runEdit = useCallback(
    async (work: () => Promise<unknown>, note: string) => {
      try {
        await work();
        await afterEdit(note);
      } catch (error) {
        setEditError(errorText(error, t("ui.memory.load_failed", "Gagal memuat graf.")));
        setEditNote("");
      }
    },
    [afterEdit],
  );

  const correctFact = (edge: GraphEdge, newObject: string) =>
    runEdit(
      () =>
        changeGraphEdge({
          subject: edge.subject,
          relation: edge.relation,
          object: edge.object,
          new_object: newObject,
          operation: "correct_fact",
        }),
      t("ui.memory.fact_corrected", "Fakta dikoreksi dan tercatat di buku kerja."),
    );

  /** The "+" in the graph's top bar (Nero, 24 Nov): jump to the
      add-node form below and focus its name field. */
  function focusAddNode() {
    const input = document.getElementById("new-node-name") as HTMLInputElement | null;
    try {
      input?.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch {
      // jsdom and old browsers: focus alone is enough.
    }
    input?.focus();
  }

  /** Export the graph as one Obsidian markdown file (Nero,
      24 Nov): [[wikilinks]] between notes, downloaded as
      puspa-graph.md. */
  async function exportObsidian() {
    setEditError("");
    try {
      const response = await fetch("/api/graph/export/obsidian");
      if (!response.ok) throw new Error(t("ui.memory.export_failed_status", "Ekspor gagal ({status}).", { status: response.status }));
      download("puspa-graph.md", await response.text(), "text/markdown");
      setEditNote(t("ui.memory.export_done", "Graf diekspor ke puspa-graph.md (format Obsidian, [[wikilinks]])."));
    } catch (error) {
      setEditNote("");
      setEditError(error instanceof Error ? error.message : t("ui.memory.export_failed", "Ekspor gagal."));
    }
  }

  return (
    <div className="page">
      <PageHead title={t("ui.memory.title", "Memori")} />
      <div className="card" style={{ marginBottom: 12 }}>
        <RowItem icon="edit" title={t("ui.memory.edit_knowledge", "Edit Pengetahuan")} href="/editpengetahuan" />
        <RowItem icon="profile" title="Persona" href="/persona" />
        <RowItem icon="download" title={t("ui.memory.export", "Ekspor Memori")} href="/ekspor" />
        <RowItem icon="shield" title={t("ui.memory.data_control", "Kontrol Data")} href="/datakontrol" />
      </div>
      <h2>{t("ui.memory.profile", "Profil")}</h2>
      {active.length === 0 ? (
        <p className="muted">{t("ui.memory.profile_empty", "Belum ada fakta profil. Puspa belajar dari percakapanmu.")}</p>
      ) : (
        active.map((f, i) => (
          <div key={`${f.key}-${i}`} className="card">
            <strong>{f.key}</strong>
            {f.value ? <span>: {f.value}</span> : null}
            <p className="muted">
              {t(ORIGIN_KEYS[f.origin] ?? "ui.memory.origin_unknown", ORIGIN_LABELS[f.origin])}
              {f.status !== "unknown" ? ` · ${f.status}` : ""}
            </p>
          </div>
        ))
      )}

      <h2>{t("ui.memory.proposals", "Usulan")}</h2>
      <p className="muted">
        {t("ui.memory.proposals_note", "Fakta yang disimpulkan agen menunggu konfirmasimu di sini. Konfirmasi dan koreksi Usulan berjalan lewat chat.")}
      </p>
      {waiting.length === 0 ? (
        <p className="muted">{t("ui.memory.proposals_empty", "Tidak ada Usulan menunggu.")}</p>
      ) : (
        waiting.map((f, i) => (
          <div key={`${f.key}-${i}`} className="card">
            <strong>{f.key}</strong>
            {f.value ? <span>: {f.value}</span> : null}
            <p className="muted">{t("ui.memory.proposal_pending", "Disimpulkan — menunggu konfirmasi.")}</p>
          </div>
        ))
      )}

      <h2>{t("ui.memory.graph", "Graf otak kedua")}</h2>
      <p className="muted">
        {t("ui.memory.graph_note", "Yang Puspa ketahui, sebagai simpul dan sisi. Klik simpul untuk melihat fakta dan sumbernya; koreksimu tercatat di buku kerja sebagai koreksi pengguna, jadi jawaban Puspa berikutnya berubah. Simpul kode hanya bisa dibaca.")}
      </p>
      {graphError ? <p role="alert">{graphError}</p> : null}
      {editError ? <p role="alert">{editError}</p> : null}
      {editNote ? <p>{editNote}</p> : null}
      {page ? (
        <>
          <p className="muted">
            {t("ui.memory.graph_counts", "{nodes} simpul, {edges} sisi", { nodes: page.total_nodes, edges: page.total_edges })}
            {page.total_nodes > page.nodes.length
              ? ` ${t("ui.memory.graph_showing", "— menampilkan {count} pertama", { count: page.nodes.length })}`
              : ""}
          </p>
          <GraphView
            nodes={page.nodes}
            edges={page.edges}
            selectedName={selectedName}
            onSelect={(name) => void selectNode(name)}
            onAddNode={focusAddNode}
            onFullscreen={() => setGraphFullscreen(true)}
            onExport={() => void exportObsidian()}
          />
          {graphFullscreen
            ? createPortal(
                <div
                  className="graph-note-overlay"
                  role="dialog"
                  aria-label={t("ui.memory.graph_fullscreen", "Graf pengetahuan fullscreen")}
                >
                  <div className="graph-note" style={{ maxWidth: 900 }}>
                    <button
                      type="button"
                      className="btn ghost graph-note-close"
                      onClick={() => setGraphFullscreen(false)}
                    >
                      {t("ui.memory.back", "← Kembali")}
                    </button>
                    <GraphView
                      nodes={page.nodes}
                      edges={page.edges}
                      selectedName={selectedName}
                      onSelect={(name) => void selectNode(name)}
                      onAddNode={() => {
                        // Close fullscreen first so the form below is
                        // actually visible when it gets focused.
                        setGraphFullscreen(false);
                        focusAddNode();
                      }}
                      onExport={() => void exportObsidian()}
                    />
                  </div>
                </div>,
                document.body,
              )
            : null}
        </>
      ) : (
        <p className="muted">{t("ui.memory.loading", "Memuat graf…")}</p>
      )}

      {detailError ? <p role="alert">{detailError}</p> : null}
      {detail
        ? createPortal(
            <div
              className="graph-note-overlay"
              role="dialog"
              aria-label={t("ui.memory.note_title", "Catatan {name}", { name: detail.name })}
            >
              <div className="graph-note">
                <button
                  type="button"
                  className="btn ghost graph-note-close"
                  onClick={() => {
                    setSelectedName(null);
                    setDetail(null);
                  }}
                >
                  {t("ui.memory.close_note", "← Tutup catatan")}
                </button>
                <div className="card">
              <h3>{detail.name}</h3>
              <p className="muted">
                {detail.kind ? `${t("ui.memory.kind_label", "Jenis:")} ${detail.kind} · ` : ""}
                {t("ui.memory.facts_count", "{count} fakta", { count: detail.fact_count })}
                {detail.is_code_node ? ` · ${t("ui.memory.code_node", "simpul kode, hanya bisa dibaca")}` : ""}
              </p>
              <h4>{t("ui.memory.facts", "Fakta")}</h4>
              {detail.facts.length === 0 ? (
                <p className="muted">{t("ui.memory.facts_empty", "Belum ada fakta untuk simpul ini.")}</p>
              ) : (
                <ul>
                  {detail.facts.map((fact) => (
                    <li key={`${fact.subject}|${fact.relation}|${fact.object}`}>
                      {fact.subject} — {fact.relation} — {fact.object}
                      {!detail.is_code_node ? (
                        <>
                          {" "}
                          <label>
                            {t("ui.memory.correct_to", "Koreksi jadi")}{" "}
                            <input
                              type="text"
                              defaultValue={fact.object}
                              aria-label={t("ui.memory.correct_object", "Koreksi objek untuk {subject} {relation} {object}", { subject: fact.subject, relation: fact.relation, object: fact.object })}
                              onKeyDown={(event) => {
                                if (event.key !== "Enter") return;
                                const value = (
                                  event.target as HTMLInputElement
                                ).value.trim();
                                if (value && value !== fact.object) void correctFact(fact, value);
                              }}
                            />
                          </label>{" "}
                          <button
                            type="button"
                            onClick={() =>
                              void runEdit(
                                () =>
                                  deleteGraphEdge({
                                    subject: fact.subject,
                                    relation: fact.relation,
                                    object: fact.object,
                                  }),
                                t("ui.memory.edge_deleted", "Sisi dihapus dan tercatat di buku kerja."),
                              )
                            }
                          >
                            {t("ui.memory.delete_edge", "Hapus sisi")}
                          </button>
                        </>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              <h4>{t("ui.memory.sources", "Sumber")}</h4>
              {detail.provenance.length === 0 ? (
                <p className="muted">{t("ui.memory.sources_empty", "Tidak ada sumber tercatat.")}</p>
              ) : (
                <ul>
                  {detail.provenance.map((source) => (
                    <li key={source.passage_id}>
                      <code>{source.passage_id}</code>: {source.text}
                    </li>
                  ))}
                </ul>
              )}
              {!detail.is_code_node ? (
                <>
                  <h4>{t("ui.memory.rename_node", "Ubah nama simpul")}</h4>
                  <label>
                    {t("ui.memory.new_name", "Nama baru")}{" "}
                    <input
                      type="text"
                      value={renameTo}
                      onChange={(event) => setRenameTo(event.target.value)}
                    />
                  </label>{" "}
                  <button
                    type="button"
                    onClick={() =>
                      void runEdit(
                        () => renameGraphNode(detail.name, renameTo.trim()),
                        t("ui.memory.node_renamed", "Simpul diubah namanya dan tercatat di buku kerja."),
                      )
                    }
                  >
                    {t("ui.memory.rename", "Ubah nama")}
                  </button>{" "}
                  <button
                    type="button"
                    onClick={() =>
                      void runEdit(
                        () => deleteGraphNode(detail.name),
                        t("ui.memory.node_deleted", "Simpul dihapus dan tercatat di buku kerja."),
                      )
                    }
                  >
                    {t("ui.memory.delete_node", "Hapus simpul")}
                  </button>
                </>
              ) : null}
                </div>
              </div>
        </div>,
            document.body,
          )
        : null}

      <h3>{t("ui.memory.add_node", "Tambah simpul")}</h3>
      <div className="card stacked-form">
        <label>
          {t("ui.memory.name_label", "Nama")}
          <input
            id="new-node-name"
            className="input"
            type="text"
            value={newNodeName}
            onChange={(event) => setNewNodeName(event.target.value)}
          />
        </label>
        <label>
          {t("ui.memory.kind_input", "Jenis")}
          <input
            className="input"
            type="text"
            value={newNodeKind}
            onChange={(event) => setNewNodeKind(event.target.value)}
          />
        </label>
        <button
          className="btn dark block"
          type="button"
          aria-label={t("ui.memory.add_node", "Tambah simpul")}
          onClick={() =>
            void runEdit(
              () =>
                addGraphNode({
                  name: newNodeName.trim(),
                  kind: newNodeKind.trim() || null,
                }),
              t("ui.memory.node_added", "Simpul ditambahkan dan tercatat di buku kerja."),
            )
          }
        >
          +
        </button>
      </div>

      <h3>{t("ui.memory.add_edge", "Tambah sisi")}</h3>
      <div className="card stacked-form">
        <label>
          {t("ui.memory.subject", "Subjek")}
          <input
            className="input"
            type="text"
            value={edgeSubject}
            onChange={(event) => setEdgeSubject(event.target.value)}
          />
        </label>
        <label>
          {t("ui.memory.relation", "Relasi")}
          <input
            className="input"
            type="text"
            value={edgeRelation}
            onChange={(event) => setEdgeRelation(event.target.value)}
          />
        </label>
        <label>
          {t("ui.memory.object", "Objek")}
          <input
            className="input"
            type="text"
            value={edgeObject}
            onChange={(event) => setEdgeObject(event.target.value)}
          />
        </label>
        <button
          className="btn dark block"
          type="button"
          onClick={() =>
            void runEdit(
              () =>
                addGraphEdge({
                  subject: edgeSubject.trim(),
                  relation: edgeRelation.trim(),
                  object: edgeObject.trim(),
                }),
              t("ui.memory.edge_added", "Sisi ditambahkan dan tercatat di buku kerja."),
            )
          }
        >
          {t("ui.memory.add_edge", "Tambah sisi")}
        </button>
      </div>
    </div>
  );
}
