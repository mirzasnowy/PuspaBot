import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  applyMockupOp,
  createMockup,
  fetchMockups,
  fetchMockup,
  generateMockups,
  patchMockup,
  redoMockup,
  undoMockup,
} from "../api/client";
import {
  applyOp,
  dragMoveOp,
  findNode,
  type MockupDoc,
  type MockupNode,
  type MockupVariant,
} from "../state/mockup";

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Gagal memuat mockup.";
}

function nodeLabel(node: MockupNode): string {
  return node.text ?? (node.type === "image" ? "[gambar]" : node.type);
}

export function MockupScreen() {
  const [prompt, setPrompt] = useState("");
  const [variants, setVariants] = useState<MockupVariant[]>([]);
  const [saved, setSaved] = useState<{ id: string; title: string; template: string }[]>([]);
  const [doc, setDoc] = useState<MockupDoc | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editColor, setEditColor] = useState("#111827");
  const [instruction, setInstruction] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const dragRef = useRef<{ nodeId: string; offsetX: number; offsetY: number } | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);

  const refreshSaved = useCallback(async () => {
    try {
      const body = await fetchMockups();
      setSaved(body.mockups);
    } catch {
      // The list is a convenience; the editor still works without it.
    }
  }, []);

  useEffect(() => {
    void refreshSaved();
  }, [refreshSaved]);

  const selected = doc && selectedId ? findNode(doc.nodes, selectedId) : null;

  const openDoc = useCallback(async (id: string) => {
    try {
      const loaded = await fetchMockup(id);
      setDoc(loaded);
      setSelectedId(null);
      setError("");
    } catch (caught) {
      setError(errorText(caught));
    }
  }, []);

  const runGenerate = useCallback(async () => {
    if (!prompt.trim()) return;
    try {
      setError("");
      const result = await generateMockups(prompt);
      setVariants(result.variants);
      setStatus(`Template ${result.template}: ${result.variants.length} varian siap.`);
    } catch (caught) {
      setError(errorText(caught));
    }
  }, [prompt]);

  const saveVariant = useCallback(
    async (variant: MockupVariant) => {
      try {
        const stored = await createMockup({ doc: variant.doc });
        setDoc(stored);
        setVariants([]);
        setStatus("Mockup disimpan. Seret elemen untuk menatanya.");
        await refreshSaved();
      } catch (caught) {
        setError(errorText(caught));
      }
    },
    [refreshSaved],
  );

  const applyAndSave = useCallback(
    async (op: Parameters<typeof applyOp>[1]) => {
      if (!doc) return;
      try {
        setDoc(applyOp(doc, op));
        const stored = await applyMockupOp(doc.id, op);
        setDoc(stored);
        setError("");
      } catch (caught) {
        setError(errorText(caught));
        await openDoc(doc.id);
      }
    },
    [doc, openDoc],
  );

  const onDrop = useCallback(
    async (event: React.DragEvent) => {
      event.preventDefault();
      if (!doc || !dragRef.current || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      const dropX = event.clientX - rect.left;
      const dropY = event.clientY - rect.top;
      const { nodeId, offsetX, offsetY } = dragRef.current;
      dragRef.current = null;
      await applyAndSave(dragMoveOp(doc, nodeId, dropX, dropY, offsetX, offsetY));
    },
    [doc, applyAndSave],
  );

  const selectNode = useCallback(
    (node: MockupNode) => {
      setSelectedId(node.id);
      setEditText(node.text ?? "");
      setEditColor((node.style?.color as string) ?? "#111827");
    },
    [],
  );

  const applyEdit = useCallback(async () => {
    if (!selected) return;
    if (editText !== (selected.text ?? "")) {
      await applyAndSave({
        type: "set_text",
        node_id: selected.id,
        payload: { text: editText },
        note: "Text edited in the editor.",
      });
    }
    await applyAndSave({
      type: "set_style",
      node_id: selected.id,
      payload: { style: { color: editColor } },
      note: "Colour edited in the editor.",
    });
  }, [selected, editText, editColor, applyAndSave]);

  const runPatch = useCallback(async () => {
    if (!doc || !instruction.trim()) return;
    try {
      const stored = await patchMockup(doc.id, instruction);
      setDoc(stored);
      setInstruction("");
      setError("");
    } catch (caught) {
      setError(errorText(caught));
    }
  }, [doc, instruction]);

  const runUndo = useCallback(async () => {
    if (!doc) return;
    try {
      setDoc(await undoMockup(doc.id));
    } catch (caught) {
      setError(errorText(caught));
    }
  }, [doc]);

  const runRedo = useCallback(async () => {
    if (!doc) return;
    try {
      setDoc(await redoMockup(doc.id));
    } catch (caught) {
      setError(errorText(caught));
    }
  }, [doc]);

  return (
    <div>
      <h1>Mockup</h1>
      <p>Deskripsikan yang kamu mau, Puspa buatkan mockupnya. Seret elemen untuk menata ulang.</p>
      {error && <p role="alert">{error}</p>}
      {status && <p>{status}</p>}

      <label>
        Prompt
        <input
          aria-label="Prompt mockup"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="contoh: poster promo diskon toko Kopi Senja Rp25.000"
        />
      </label>
      <button type="button" onClick={() => void runGenerate()}>
        Buat mockup
      </button>

      {variants.length > 0 && (
        <section aria-label="Varian mockup">
          <h2>Pilih varian</h2>
          {variants.map((variant, index) => (
            <article key={variant.doc.id + index}>
              <h3>{variant.doc.title}</h3>
              <p>{variant.doc.nodes.length} elemen</p>
              {variant.issues.length > 0 && (
                <p>
                  {variant.issues.length} catatan: {variant.issues.map((i) => i.kind).join(", ")}
                </p>
              )}
              <button type="button" onClick={() => void saveVariant(variant)}>
                Pakai varian {index + 1}
              </button>
            </article>
          ))}
        </section>
      )}

      {saved.length > 0 && (
        <section aria-label="Mockup tersimpan">
          <h2>Mockup tersimpan</h2>
          <ul>
            {saved.map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => void openDoc(item.id)}>
                  {item.title} ({item.template})
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {doc && (
        <section aria-label="Editor mockup">
          <h2>{doc.title}</h2>
          <p>
            Versi {doc.version} · {doc.ops.length} operasi · {doc.decisions.length} keputusan desain
          </p>
          <button type="button" onClick={() => void runUndo()}>
            Urungkan
          </button>
          <button type="button" onClick={() => void runRedo()}>
            Ulangi
          </button>
          <a href={`/api/mockups/${doc.id}/export.png`}>Unduh PNG</a>{" "}
          <a href={`/api/mockups/${doc.id}/export.pdf`}>Unduh PDF</a>{" "}
          <a href={`/api/mockups/${doc.id}/render.svg`} target="_blank" rel="noreferrer">
            Lihat SVG
          </a>

          <div
            ref={canvasRef}
            data-testid="mockup-canvas"
            role="application"
            aria-label="Kanvas mockup, seret elemen untuk memindah"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => void onDrop(event)}
            style={{
              position: "relative",
              width: doc.frame.width,
              height: doc.frame.height,
              maxWidth: "100%",
              overflow: "auto",
              background: doc.theme.background,
              border: "1px solid #d1d5db",
            }}
          >
            {doc.nodes.map((node) => (
              <div
                key={node.id}
                data-testid={`mockup-node-${node.id}`}
                draggable
                onDragStart={(event) => {
                  const rect = (event.target as HTMLElement).getBoundingClientRect();
                  dragRef.current = {
                    nodeId: node.id,
                    offsetX: event.clientX - rect.left,
                    offsetY: event.clientY - rect.top,
                  };
                  event.dataTransfer?.setData("text/plain", node.id);
                }}
                onClick={() => selectNode(node)}
                style={{
                  position: "absolute",
                  left: node.x,
                  top: node.y,
                  width: node.w,
                  height: node.h,
                  fontSize: (node.style?.font_size as number) ?? 20,
                  fontWeight: node.style?.bold ? "bold" : "normal",
                  color: (node.style?.color as string) ?? doc.theme.text,
                  background: (node.style?.background as string) ?? undefined,
                  border: selectedId === node.id ? "2px solid #1d4ed8" : "1px dashed #9ca3af",
                  boxSizing: "border-box",
                  cursor: "grab",
                  overflow: "hidden",
                }}
              >
                {nodeLabel(node)}
              </div>
            ))}
          </div>

          {selected && (
            <div>
              <h3>Edit elemen</h3>
              <label>
                Teks
                <input
                  aria-label="Teks elemen"
                  value={editText}
                  onChange={(event) => setEditText(event.target.value)}
                />
              </label>
              <label>
                Warna teks
                <input
                  aria-label="Warna teks"
                  type="color"
                  value={editColor}
                  onChange={(event) => setEditColor(event.target.value)}
                />
              </label>
              <button type="button" onClick={() => void applyEdit()}>
                Terapkan edit
              </button>
            </div>
          )}

          <div>
            <label>
              Ubah dengan kata-kata
              <input
                aria-label="Ubah dengan kata-kata"
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder="contoh: besarkan harga, ganti warna jadi hijau"
              />
            </label>
            <button type="button" onClick={() => void runPatch()}>
              Terapkan
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
