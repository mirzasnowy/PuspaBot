import { describe, expect, it } from "vitest";
import {
  MockupHistory,
  applyOp,
  dragMoveOp,
  findNode,
  type MockupDoc,
} from "./mockup";

function bannerDoc(): MockupDoc {
  return {
    id: "doc-1",
    title: "Promo",
    template: "promo_banner",
    frame: { width: 800, height: 600 },
    theme: {
      primary: "#1d4ed8",
      background: "#eff6ff",
      text: "#111827",
      accent: "#16a34a",
      font: "Inter",
      radius: 12,
    },
    assets: {},
    nodes: [
      { id: "price", type: "text", x: 80, y: 380, w: 400, h: 80, text: "Rp25.000", style: { font_size: 48 } },
      { id: "headline", type: "text", x: 80, y: 180, w: 400, h: 80, text: "Diskon 20%", style: { font_size: 40 } },
    ],
    version: 0,
    ops: [],
    decisions: [],
  };
}

describe("mockup editor state (B7)", () => {
  it("applies a move op and numbers it", () => {
    const next = applyOp(bannerDoc(), {
      type: "move_node",
      node_id: "price",
      payload: { x: 120, y: 420 },
    });
    expect(findNode(next.nodes, "price")?.x).toBe(120);
    expect(next.ops).toHaveLength(1);
    expect(next.ops[0].seq).toBe(1);
    expect(next.version).toBe(1);
  });

  it("drag-drop round-trips back into the doc", () => {
    const doc = bannerDoc();
    // Grab the price node 10px inside its top-left corner, drop at (200, 400).
    const op = dragMoveOp(doc, "price", 200, 400, 10, 10);
    expect(op.type).toBe("move_node");
    expect(op.payload).toEqual({ x: 190, y: 390 });
    const moved = applyOp(doc, op);
    expect(findNode(moved.nodes, "price")).toMatchObject({ x: 190, y: 390 });
    // The same op re-applied to the original doc reproduces the same doc.
    expect(applyOp(doc, op)).toEqual(moved);
  });

  it("clamps a drag to the frame", () => {
    const op = dragMoveOp(bannerDoc(), "price", 900, 700, 0, 0);
    expect(op.payload).toEqual({ x: 400, y: 520 });
  });

  it("undo/redo restores the doc without rewriting the op log", () => {
    const history = new MockupHistory(bannerDoc());
    history.apply({ type: "set_text", node_id: "price", payload: { text: "Rp10.000" } });
    expect(findNode(history.current().nodes, "price")?.text).toBe("Rp10.000");
    history.undo();
    expect(findNode(history.current().nodes, "price")?.text).toBe("Rp25.000");
    expect(history.current().ops).toHaveLength(0);
    expect(history.canRedo).toBe(true);
    history.redo();
    expect(findNode(history.current().nodes, "price")?.text).toBe("Rp10.000");
    expect(() => new MockupHistory(bannerDoc()).undo()).toThrow("nothing to undo");
  });

  it("records a design decision from an op note", () => {
    const next = applyOp(bannerDoc(), {
      type: "set_theme",
      payload: { theme: { accent: "#16a34a" } },
      note: "Green fits the shop.",
    });
    expect(next.decisions).toEqual([{ seq: 1, note: "Green fits the shop." }]);
  });

  it("mirrors an add_asset op into the doc (P-06)", () => {
    const next = applyOp(bannerDoc(), {
      type: "add_asset",
      payload: {
        asset_id: "logo",
        asset: { kind: "image", data_uri: "data:image/png;base64,AAA", name: "logo.png" },
      },
    });
    expect(next.assets.logo.data_uri).toBe("data:image/png;base64,AAA");
    expect(next.ops).toHaveLength(1);
    expect(next.ops[0].seq).toBe(1);
    expect(next.version).toBe(1);
  });

  it("mirrors a reorder op (P-06)", () => {
    const next = applyOp(bannerDoc(), {
      type: "reorder",
      node_id: "price",
      payload: { index: 1 },
    });
    expect(next.nodes[1].id).toBe("price");
    expect(next.nodes[0].id).toBe("headline");
    expect(next.version).toBe(1);
  });

  it("rejects ops on unknown nodes", () => {
    expect(() =>
      applyOp(bannerDoc(), { type: "move_node", node_id: "nope", payload: { x: 1, y: 1 } }),
    ).toThrow("unknown node");
    expect(() => dragMoveOp(bannerDoc(), "nope", 1, 1, 0, 0)).toThrow("unknown node");
  });
});
