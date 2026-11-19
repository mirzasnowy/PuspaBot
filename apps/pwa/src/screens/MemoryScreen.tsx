import { useCallback, useEffect, useState } from "react";
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

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Gagal memuat graf.";
}

export function MemoryScreen({ bootstrap }: { bootstrap: Bootstrap }) {
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
      setGraphError(errorText(error));
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
      setDetailError(errorText(error));
    }
  }, []);

  const afterEdit = useCallback(
    async (note: string) => {
      setEditNote(note);
      setEditError("");
      await refreshPage();
      if (selectedName) await selectNode(selectedName);
    },
    [refreshPage, selectNode, selectedName],
  );

  const runEdit = useCallback(
    async (work: () => Promise<unknown>, note: string) => {
      try {
        await work();
        await afterEdit(note);
      } catch (error) {
        setEditError(errorText(error));
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
      "Fakta dikoreksi dan tercatat di buku kerja.",
    );

  return (
    <div>
      <h1>Memori</h1>
      <h2>Profil</h2>
      {active.length === 0 ? (
        <p className="muted">Belum ada fakta profil. Puspa belajar dari percakapanmu.</p>
      ) : (
        active.map((f, i) => (
          <div key={`${f.key}-${i}`} className="card">
            <strong>{f.key}</strong>
            {f.value ? <span>: {f.value}</span> : null}
            <p className="muted">
              {ORIGIN_LABELS[f.origin]}
              {f.status !== "unknown" ? ` · ${f.status}` : ""}
            </p>
          </div>
        ))
      )}

      <h2>Usulan</h2>
      <p className="muted">
        Fakta yang disimpulkan agen menunggu konfirmasimu di sini. Konfirmasi dan koreksi Usulan
        berjalan lewat chat sampai endpoint kelola memori mendarat di API.
      </p>
      {waiting.length === 0 ? (
        <p className="muted">Tidak ada Usulan menunggu.</p>
      ) : (
        waiting.map((f, i) => (
          <div key={`${f.key}-${i}`} className="card">
            <strong>{f.key}</strong>
            {f.value ? <span>: {f.value}</span> : null}
            <p className="muted">Disimpulkan — menunggu konfirmasi.</p>
          </div>
        ))
      )}

      <h2>Graf otak kedua</h2>
      <p className="muted">
        Yang Puspa ketahui, sebagai simpul dan sisi. Klik simpul untuk melihat fakta dan
        sumbernya; koreksimu tercatat di buku kerja sebagai koreksi pengguna, jadi jawaban
        Puspa berikutnya berubah. Simpul kode hanya bisa dibaca.
      </p>
      {graphError ? <p role="alert">{graphError}</p> : null}
      {editError ? <p role="alert">{editError}</p> : null}
      {editNote ? <p>{editNote}</p> : null}
      {page ? (
        <>
          <p className="muted">
            {page.total_nodes} simpul, {page.total_edges} sisi
            {page.total_nodes > page.nodes.length
              ? ` — menampilkan ${page.nodes.length} pertama`
              : ""}
          </p>
          <GraphView
            nodes={page.nodes}
            edges={page.edges}
            selectedName={selectedName}
            onSelect={(name) => void selectNode(name)}
          />
        </>
      ) : (
        <p className="muted">Memuat graf…</p>
      )}

      {detailError ? <p role="alert">{detailError}</p> : null}
      {detail ? (
        <div className="card">
          <h3>{detail.name}</h3>
          <p className="muted">
            {detail.kind ? `Jenis: ${detail.kind} · ` : ""}
            {detail.fact_count} fakta
            {detail.is_code_node ? " · simpul kode, hanya bisa dibaca" : ""}
          </p>
          <h4>Fakta</h4>
          {detail.facts.length === 0 ? (
            <p className="muted">Belum ada fakta untuk simpul ini.</p>
          ) : (
            <ul>
              {detail.facts.map((fact) => (
                <li key={`${fact.subject}|${fact.relation}|${fact.object}`}>
                  {fact.subject} — {fact.relation} — {fact.object}
                  {!detail.is_code_node ? (
                    <>
                      {" "}
                      <label>
                        Koreksi jadi{" "}
                        <input
                          type="text"
                          defaultValue={fact.object}
                          aria-label={`Koreksi objek untuk ${fact.subject} ${fact.relation} ${fact.object}`}
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
                            "Sisi dihapus dan tercatat di buku kerja.",
                          )
                        }
                      >
                        Hapus sisi
                      </button>
                    </>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          <h4>Sumber</h4>
          {detail.provenance.length === 0 ? (
            <p className="muted">Tidak ada sumber tercatat.</p>
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
              <h4>Ubah nama simpul</h4>
              <label>
                Nama baru{" "}
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
                    "Simpul diubah namanya dan tercatat di buku kerja.",
                  )
                }
              >
                Ubah nama
              </button>{" "}
              <button
                type="button"
                onClick={() =>
                  void runEdit(
                    () => deleteGraphNode(detail.name),
                    "Simpul dihapus dan tercatat di buku kerja.",
                  )
                }
              >
                Hapus simpul
              </button>
            </>
          ) : null}
        </div>
      ) : null}

      <h3>Tambah simpul</h3>
      <label>
        Nama{" "}
        <input
          type="text"
          value={newNodeName}
          onChange={(event) => setNewNodeName(event.target.value)}
        />
      </label>{" "}
      <label>
        Jenis{" "}
        <input
          type="text"
          value={newNodeKind}
          onChange={(event) => setNewNodeKind(event.target.value)}
        />
      </label>{" "}
      <button
        type="button"
        onClick={() =>
          void runEdit(
            () =>
              addGraphNode({
                name: newNodeName.trim(),
                kind: newNodeKind.trim() || null,
              }),
            "Simpul ditambahkan dan tercatat di buku kerja.",
          )
        }
      >
        Tambah simpul
      </button>

      <h3>Tambah sisi</h3>
      <label>
        Subjek{" "}
        <input
          type="text"
          value={edgeSubject}
          onChange={(event) => setEdgeSubject(event.target.value)}
        />
      </label>{" "}
      <label>
        Relasi{" "}
        <input
          type="text"
          value={edgeRelation}
          onChange={(event) => setEdgeRelation(event.target.value)}
        />
      </label>{" "}
      <label>
        Objek{" "}
        <input
          type="text"
          value={edgeObject}
          onChange={(event) => setEdgeObject(event.target.value)}
        />
      </label>{" "}
      <button
        type="button"
        onClick={() =>
          void runEdit(
            () =>
              addGraphEdge({
                subject: edgeSubject.trim(),
                relation: edgeRelation.trim(),
                object: edgeObject.trim(),
              }),
            "Sisi ditambahkan dan tercatat di buku kerja.",
          )
        }
      >
        Tambah sisi
      </button>

      <h2>Ekspor dan hapus</h2>
      <p className="muted">
        Ekspor penuh dan hapus akun tersedia lewat jalur Fase 1 (puspa.consent.account). Hapus akun
        menghapus memori, alur, modul, kaset, dan blob; rantai hash Buku Kerja tetap utuh lewat
        tombstone. Tombol ekspor/hapus di PWA adalah follow-up setelah endpoint-nya mendarat.
      </p>
    </div>
  );
}
