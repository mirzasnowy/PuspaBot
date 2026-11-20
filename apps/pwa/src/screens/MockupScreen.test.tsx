import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MockupScreen } from "./MockupScreen";
import { applyOp, type MockupDoc, type MockupOp } from "../state/mockup";

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

/** Fake server: keeps the current doc and applies every posted op
 * with the real client mirror, the way the server applies them. */
function mockFetch(doc: MockupDoc, postedOps: unknown[]) {
  let current = JSON.parse(JSON.stringify(doc)) as MockupDoc;
  return vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
    const method = init?.method ?? "GET";
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
    if (url === "/api/mockups" && method === "GET") return ok({ mockups: [] });
    if (url === "/api/mockups/generate") {
      return ok({ template: "promo_banner", fields: {}, variants: [{ doc, issues: [] }] });
    }
    if (url === "/api/mockups" && method === "POST") {
      current = JSON.parse(JSON.stringify(doc)) as MockupDoc;
      return ok({ ...current, version: 0 });
    }
    if (url === `/api/mockups/${doc.id}/ops` && method === "POST") {
      const op = JSON.parse(init?.body ?? "{}").op as MockupOp;
      postedOps.push(op);
      current = applyOp(current, op);
      return ok(current);
    }
    if (url === `/api/mockups/${doc.id}/assets` && method === "POST") {
      const body = JSON.parse(init?.body ?? "{}") as {
        asset_id: string;
        data_uri: string;
        name: string;
      };
      current = applyOp(current, {
        type: "add_asset",
        payload: {
          asset_id: body.asset_id,
          asset: { kind: "image", data_uri: body.data_uri, name: body.name },
        },
        note: `Added image asset ${body.asset_id}.`,
      });
      return ok(current);
    }
    return { ok: false, status: 404, json: async () => ({ error: "not found" }) };
  });
}

async function openEditor(doc: MockupDoc, postedOps: unknown[]) {
  vi.stubGlobal("fetch", mockFetch(doc, postedOps));
  render(<MockupScreen />);
  fireEvent.change(screen.getByLabelText("Prompt mockup"), {
    target: { value: "poster promo diskon toko Kopi Senja" },
  });
  fireEvent.click(screen.getByText("Buat mockup"));
  expect(await screen.findByText("Pakai varian 1")).toBeTruthy();
  fireEvent.click(screen.getByText("Pakai varian 1"));
  return screen.findByTestId("mockup-canvas");
}

describe("MockupScreen (B7)", () => {
  it("generates variants, saves one, and drag-drops a node back into the doc", async () => {
    const doc = bannerDoc();
    const postedOps: unknown[] = [];
    const canvas = await openEditor(doc, postedOps);
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

  it("reorders a node with Naik/Turun (P-06)", async () => {
    const doc = bannerDoc();
    const postedOps: unknown[] = [];
    const canvas = await openEditor(doc, postedOps);
    fireEvent.click(screen.getByTestId("mockup-node-price"));
    expect((screen.getByText("Naik") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("Turun"));
    expect(await screen.findByText("Versi 1 · 1 operasi · 1 keputusan desain")).toBeTruthy();
    expect(postedOps).toHaveLength(1);
    expect(postedOps[0]).toMatchObject({
      type: "reorder",
      node_id: "price",
      payload: { index: 1 },
    });
    const nodes = canvas.querySelectorAll("[data-testid^='mockup-node-']");
    expect(nodes[1].getAttribute("data-testid")).toBe("mockup-node-price");
  });

  it("adds an image from a file upload (P-06)", async () => {
    const doc = bannerDoc();
    const postedOps: unknown[] = [];
    await openEditor(doc, postedOps);
    const file = new File([new Uint8Array([137, 80, 78, 71])], "logo.png", {
      type: "image/png",
    });
    const input = screen.getByLabelText("Tambah gambar") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    const img = (await screen.findByAltText("Gambar mockup")) as HTMLImageElement;
    expect(img.src).toMatch(/^data:image\/png;base64,/);
    expect(postedOps).toHaveLength(1);
    expect(postedOps[0]).toMatchObject({
      type: "add_node",
      payload: { node: { type: "image" } },
    });
    // The new image node is selected, so the change-image control shows.
    expect(screen.getByLabelText("Ganti gambar")).toBeTruthy();
    expect(await screen.findByText("Versi 2 · 2 operasi · 2 keputusan desain")).toBeTruthy();
  });
});
