import { useEffect, useState } from "react";
import {
  buildWiki,
  fetchWikiGraph,
  fetchWikiPage,
  fetchWikiTopics,
  type WikiGraph,
  type WikiPage,
  type WikiTopic,
} from "../api/wiki";

type Tab = "page" | "graph";

/**
 * Auto-curated knowledge wiki (COW-06).
 *
 * One screen, two tabs. The Topics list shows every curated
 * topic with its entry count; the Page tab renders the topic's
 * Markdown (as server-rendered HTML) with its sources; the
 * Graph tab shows the topic's nodes and edges from the existing
 * KnowledgeGraph, read-only — there is no edit control here,
 * editing is the second-brain graph view (B18), not this screen.
 *
 * Honest limit: this is a browser over the last build, not a
 * live view — a conversation curated after the last build only
 * appears after the next build (the button below, or the
 * `puspa wiki build` CLI trigger).
 */
export function WikiScreen() {
  const [topics, setTopics] = useState<WikiTopic[]>([]);
  const [selected, setSelected] = useState("");
  const [page, setPage] = useState<WikiPage | null>(null);
  const [graph, setGraph] = useState<WikiGraph | null>(null);
  const [tab, setTab] = useState<Tab>("page");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetchWikiTopics()
      .then((data) => {
        setTopics(data.topics);
        if (data.topics.length > 0) setSelected(data.topics[0].slug);
      })
      .catch(() => setError("Gagal memuat topik wiki."));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setPage(null);
    setGraph(null);
    fetchWikiPage(selected)
      .then(setPage)
      .catch(() => setError("Gagal memuat halaman wiki."));
    fetchWikiGraph(selected)
      .then(setGraph)
      .catch(() => setGraph(null));
  }, [selected]);

  async function rebuild() {
    setStatus("Membangun wiki…");
    try {
      const report = await buildWiki();
      setStatus(`Selesai: ${report.pages_written} halaman, ${report.entries} entri.`);
      const data = await fetchWikiTopics();
      setTopics(data.topics);
      if (data.topics.length > 0 && !data.topics.some((t) => t.slug === selected)) {
        setSelected(data.topics[0].slug);
      }
    } catch {
      setStatus("Build gagal.");
    }
  }

  return (
    <div>
      <h1>Wiki</h1>
      <p>Catatan yang dikurasi otomatis dari percakapan — dikelompokkan per topik, tiap entri menyebut sumbernya.</p>
      {error && <p role="alert">{error}</p>}
      <button type="button" onClick={rebuild}>
        Bangun ulang wiki
      </button>
      {status && <p>{status}</p>}
      {topics.length === 0 ? (
        <p>Belum ada yang dikurasi. Jalankan build setelah agen mengonsolidasi percakapan.</p>
      ) : (
        <ul>
          {topics.map((topic) => (
            <li key={topic.slug}>
              <button
                type="button"
                onClick={() => setSelected(topic.slug)}
                aria-current={selected === topic.slug}
              >
                {topic.title} — {topic.entries} entri
              </button>
            </li>
          ))}
        </ul>
      )}
      {page && (
        <div>
          <div role="tablist">
            <button type="button" role="tab" aria-selected={tab === "page"} onClick={() => setTab("page")}>
              Halaman
            </button>
            <button type="button" role="tab" aria-selected={tab === "graph"} onClick={() => setTab("graph")}>
              Grafik (hanya baca)
            </button>
          </div>
          {tab === "page" ? (
            <article dangerouslySetInnerHTML={{ __html: page.html }} />
          ) : graph ? (
            <div>
              {graph.nodes.length === 0 ? (
                <p>Grafik topik ini masih kosong.</p>
              ) : (
                <>
                  <h2>Simpul</h2>
                  <ul>
                    {graph.nodes.map((node) => (
                      <li key={node.name}>
                        {node.name}
                        {node.kind ? ` (${node.kind})` : ""}
                      </li>
                    ))}
                  </ul>
                  {graph.edges.length > 0 && (
                    <>
                      <h2>Kaitan</h2>
                      <ul>
                        {graph.edges.map((edge) => (
                          <li key={`${edge.subject}-${edge.relation}-${edge.object}`}>
                            {edge.subject} — {edge.relation} → {edge.object}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              )}
            </div>
          ) : (
            <p>Memuat grafik…</p>
          )}
          {page.sources.length > 0 && (
            <p>
              Sumber: {page.sources.join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
