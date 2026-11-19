import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MockupScreen } from "./MockupScreen";
import type { MockupDoc } from "../state/mockup";

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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MockupScreen (B7)", () => {
  it("generates variants, saves one, and drag-drops a node back into the doc", async () => {
    const doc = bannerDoc();
    const postedOps: unknown[] = [];
    const fetchMock = vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
      const method = init?.method ?? "GET";
      const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
      if (url === "/api/mockups" && method === "GET") return ok({ mockups: [] });
      if (url === "/api/mockups/generate") {
        return ok({ template: "promo_banner", fields: {}, variants: [{ doc, issues: [] }] });
      }
      if (url === "/api/mockups" && method === "POST") {
        return ok({ ...doc, version: 0 });
      }
      if (url === `/api/mockups/${doc.id}/ops` && method === "POST") {
        const op = JSON.parse(init?.body ?? "{}").op;
        postedOps.push(op);
        const moved = JSON.parse(JSON.stringify(doc)) as MockupDoc;
        const node = moved.nodes.find((n) => n.id === op.node_id);
        if (node && op.type === "move_node") {
          node.x = op.payload.x as number;
          node.y = op.payload.y as number;
        }
        moved.version = 1;
        moved.ops = [op];
        return ok(moved);
      }
      return { ok: false, status: 404, json: async () => ({ error: "not found" }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MockupScreen />);
    fireEvent.change(screen.getByLabelText("Prompt mockup"), {
      target: { value: "poster promo diskon toko Kopi Senja" },
    });
    fireEvent.click(screen.getByText("Buat mockup"));
    expect(await screen.findByText("Pakai varian 1")).toBeTruthy();
    fireEvent.click(screen.getByText("Pakai varian 1"));
    const canvas = await screen.findByTestId("mockup-canvas");
    expect(screen.getByTestId("mockup-node-price").textContent).toBe("Rp25.000");

    // jsdom reports every bounding rect as 0, so the grab offset is
    // the full clientX/clientY (90/390) and the drop lands at
    // (200 - 90, 400 - 390) = (110, 10). In a real browser the node
    // rect carries its on-screen position, so the same code lands
    // the node 10px from the grab point, as mockup.test.ts covers.
    const node = screen.getByTestId("mockup-node-price");
    node.dispatchEvent(
      new MouseEvent("dragstart", { clientX: 90, clientY: 390, bubbles: true }),
    );
    canvas.dispatchEvent(new MouseEvent("drop", { clientX: 200, clientY: 400, bubbles: true }));
    expect(await screen.findByText("Versi 1 · 1 operasi · 0 keputusan desain")).toBeTruthy();
    expect(postedOps).toHaveLength(1);
    expect(postedOps[0]).toMatchObject({
      type: "move_node",
      node_id: "price",
      payload: { x: 110, y: 10 },
    });
    expect((screen.getByTestId("mockup-node-price") as HTMLElement).style.left).toBe("110px");
  });
});
