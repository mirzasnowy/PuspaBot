import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  addMockupAsset,
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

/** Server asset cap (puspa/mockup/doc.py MAX_ASSET_BYTES): checked
 * here so a too-big file fails fast with a plain message, not a 400. */
const MAX_ASSET_BYTES = 262144;

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Gagal membaca file."));
    reader.readAsDataURL(file);
  });
}

function randomSuffix(): string {
  return Math.random().toString(36).slice(2, 10);
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Gagal memuat mockup.";
}

function nodeLabel(node: MockupNode): string {
  return node.text ?? (node.type === "image" ? "[gambar]" : node.type);
}

/** One node on the canvas. Children render inside their parent at
 * their relative position, so a catalogue card shows its product
 * name and price instead of just "card". Only top-level nodes are
 * draggable: a child drag would need parent-relative coordinates,
 * which dragMoveOp does not compute. */
function MockupNodeView({
  node,
  doc,
  selectedId,
  draggable,
  onSelect,
  onDragStart,
}: {
  node: MockupNode;
  doc: MockupDoc;
  selectedId: string | null;
  draggable: boolean;
  onSelect: (node: MockupNode) => void;
  onDragStart: (node: MockupNode, event: React.DragEvent) => void;
}) {
  const asset = node.src ? doc.assets[node.src] : undefined;
  const children = node.children ?? [];
  return (
    <div
      data-testid={`mockup-node-${node.id}`}
      draggable={draggable}
      onDragStart={draggable ? (event) => onDragStart(node, event) : undefined}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(node);
      }}
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
        cursor: draggable ? "grab" : "default",
        overflow: "hidden",
      }}
    >
      {node.type === "image" && asset ? (
        <img
          src={asset.data_uri}
          alt={node.text ?? "Gambar mockup"}
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
      ) : children.length === 0 ? (
        nodeLabel(node)
      ) : null}
      {children.map((child) => (
        <MockupNodeView
          key={child.id}
          node={child}
          doc={doc}
          selectedId={selectedId}
          draggable={false}
          onSelect={onSelect}
          onDragStart={onDragStart}
        />
      ))}
    </div>
  );
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
  const topLevelIndex =
    doc && selectedId ? doc.nodes.findIndex((node) => node.id === selectedId) : -1;

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

  const handleDragStart = useCallback((node: MockupNode, event: React.DragEvent) => {
    const rect = (event.target as HTMLElement).getBoundingClientRect();
    dragRef.current = {
      nodeId: node.id,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
    event.dataTransfer?.setData("text/plain", node.id);
  }, []);

  const moveSelected = useCallback(
    async (delta: number) => {
      if (!doc || !selected || topLevelIndex < 0) return;
      const nextIndex = topLevelIndex + delta;
      if (nextIndex < 0 || nextIndex >= doc.nodes.length) return;
      await applyAndSave({
        type: "reorder",
        node_id: selected.id,
        payload: { index: nextIndex },
        note: "Reordered element.",
      });
    },
    [doc, selected, topLevelIndex, applyAndSave],
  );

  /** Upload a picture, then add an image node showing it. The two
   * calls go straight to the server (no optimistic local doc):
   * the asset upload bumps the version, so the add_node op must
   * apply to the stored doc, not the pre-upload local copy. */
  const addImage = useCallback(
    async (file: File) => {
      if (!doc) return;
      if (file.size > MAX_ASSET_BYTES) {
        setError("Gambar maksimal 256 KB.");
        return;
      }
      try {
        const dataUri = await readFileAsDataUrl(file);
        const assetId = `img-${randomSuffix()}`;
        const stored = await addMockupAsset(doc.id, {
          asset_id: assetId,
          data_uri: dataUri,
          name: file.name,
        });
        const bottom = stored.nodes.reduce((max, node) => Math.max(max, node.y + node.h), 0);
        const y = Math.min(
          Math.max(40, bottom + 20),
          Math.max(40, stored.frame.height - 220),
        );
        const updated = await applyMockupOp(stored.id, {
          type: "add_node",
          payload: {
            node: {
              id: `node-${randomSuffix()}`,
              type: "image",
              x: 40,
              y,
              w: 320,
              h: 200,
              src: assetId,
              style: {},
              children: [],
            },
          },
          note: "Added image.",
        });
        setDoc(updated);
        setSelectedId(updated.nodes[updated.nodes.length - 1].id);
        setError("");
      } catch (caught) {
        setError(errorText(caught));
      }
    },
    [doc],
  );

  /** Replace the picture of the selected image node. */
  const changeImage = useCallback(
    async (file: File) => {
      if (!doc || !selected || selected.type !== "image") return;
      if (file.size > MAX_ASSET_BYTES) {
        setError("Gambar maksimal 256 KB.");
        return;
      }
      try {
        const dataUri = await readFileAsDataUrl(file);
        const assetId = `img-${randomSuffix()}`;
        const stored = await addMockupAsset(doc.id, {
          asset_id: assetId,
          data_uri: dataUri,
          name: file.name,
        });
        const updated = await applyMockupOp(stored.id, {
          type: "set_image",
          node_id: selected.id,
          payload: { asset_id: assetId },
          note: "Changed image.",
        });
        setDoc(updated);
        setError("");
      } catch (caught) {
        setError(errorText(caught));
      }
    },
    [doc, selected],
  );

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
              <MockupNodeView
                key={node.id}
                node={node}
                doc={doc}
                selectedId={selectedId}
                draggable
                onSelect={selectNode}
                onDragStart={handleDragStart}
              />
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
              </button>{" "}
              {topLevelIndex >= 0 && (
                <>
                  <button
                    type="button"
                    onClick={() => void moveSelected(-1)}
                    disabled={topLevelIndex === 0}
                  >
                    Naik
                  </button>{" "}
                  <button
                    type="button"
                    onClick={() => void moveSelected(1)}
                    disabled={topLevelIndex === doc.nodes.length - 1}
                  >
                    Turun
                  </button>
                </>
              )}
              {selected.type === "image" && (
                <label>
                  Ganti gambar
                  <input
                    aria-label="Ganti gambar"
                    type="file"
                    accept="image/*"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (file) void changeImage(file);
                    }}
                  />
                </label>
              )}
            </div>
          )}

          <div>
            <label>
              Tambah gambar
              <input
                aria-label="Tambah gambar"
                type="file"
                accept="image/*"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void addImage(file);
                }}
              />
            </label>
          </div>

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
