import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryScreen } from "./MemoryScreen";
import { fetchGraphNode, fetchGraphPage, type Bootstrap } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchGraphPage: vi.fn(),
    fetchGraphNode: vi.fn(),
    addGraphNode: vi.fn(),
    addGraphEdge: vi.fn(),
    renameGraphNode: vi.fn(),
    deleteGraphNode: vi.fn(),
    changeGraphEdge: vi.fn(),
    deleteGraphEdge: vi.fn(),
  };
});

const mockedPage = vi.mocked(fetchGraphPage);
const mockedNode = vi.mocked(fetchGraphNode);

const PAGE = {
  total_nodes: 2,
  total_edges: 1,
  nodes: [
    { name: "samsung galaxy s24", kind: "phone", sources: [], fact_count: 1, is_code_node: false },
    { name: "tokopedia", kind: "shop", sources: [], fact_count: 1, is_code_node: false },
  ],
  edges: [
    { subject: "samsung galaxy s24", relation: "sells", object: "tokopedia", sources: [] },
  ],
};

const DETAIL = {
  name: "samsung galaxy s24",
  kind: "phone",
  fact_count: 1,
  is_code_node: false,
  facts: [{ subject: "samsung galaxy s24", relation: "sells", object: "tokopedia" }],
  provenance: [{ passage_id: "episode-1#p0", text: "Puspa menemukan harga di Tokopedia." }],
};

describe("MemoryScreen graph note (B18, Nero 24 Nov)", () => {
  it("clicking a node opens its note fullscreen; closing returns to the graph", async () => {
    mockedPage.mockResolvedValue(PAGE as never);
    mockedNode.mockResolvedValue(DETAIL as never);
    render(<MemoryScreen bootstrap={{ profile: [] } as unknown as Bootstrap} />);
    const nodeButton = await screen.findByRole("button", { name: "Node samsung galaxy s24" });
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(nodeButton);
    const dialog = await screen.findByRole("dialog", { name: "Catatan samsung galaxy s24" });
    expect(dialog).toBeTruthy();
    expect(screen.getByText(/Puspa menemukan harga di Tokopedia/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "← Tutup catatan" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Node samsung galaxy s24" })).toBeTruthy();
  });
});

describe("MemoryScreen graph fullscreen and Obsidian export (Nero, 24 Nov)", () => {
  it("the eye opens the graph fullscreen with a back button; back closes it", async () => {
    mockedPage.mockResolvedValue(PAGE as never);
    render(<MemoryScreen bootstrap={{ profile: [] } as unknown as Bootstrap} />);
    await screen.findByRole("button", { name: "Node samsung galaxy s24" });
    fireEvent.click(screen.getByRole("button", { name: "Lihat graf fullscreen" }));
    const dialog = screen.getByRole("dialog", { name: "Graf pengetahuan fullscreen" });
    expect(dialog).toBeTruthy();
    expect(screen.getByRole("button", { name: "← Kembali" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "← Kembali" }));
    expect(screen.queryByRole("dialog", { name: "Graf pengetahuan fullscreen" })).toBeNull();
  });

  it("the + in the graph bar focuses the add-node name field", async () => {
    mockedPage.mockResolvedValue(PAGE as never);
    render(<MemoryScreen bootstrap={{ profile: [] } as unknown as Bootstrap} />);
    await screen.findByRole("button", { name: "Node samsung galaxy s24" });
    // Two "Tambah simpul" buttons exist: the graph bar + and the
    // form submit. The first is the graph bar's.
    const plus = screen.getAllByRole("button", { name: "Tambah simpul" })[0];
    fireEvent.click(plus);
    expect(document.activeElement?.id).toBe("new-node-name");
  });

  it("Ekspor .md fetches the Obsidian export and downloads puspa-graph.md", async () => {
    mockedPage.mockResolvedValue(PAGE as never);
    const markdown = "# Puspa knowledge graph\n\n## [[tokopedia]]\n";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve(markdown),
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fetchMock as never;
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:fake");
    URL.revokeObjectURL = vi.fn();
    try {
      render(<MemoryScreen bootstrap={{ profile: [] } as unknown as Bootstrap} />);
      await screen.findByRole("button", { name: "Node samsung galaxy s24" });
      fireEvent.click(screen.getByRole("button", { name: "Ekspor .md" }));
      await screen.findByText(/Graf diekspor ke puspa-graph\.md/);
      expect(fetchMock).toHaveBeenCalledWith("/api/graph/export/obsidian");
      expect(URL.createObjectURL).toHaveBeenCalled();
    } finally {
      globalThis.fetch = originalFetch;
      URL.createObjectURL = originalCreateObjectURL;
      URL.revokeObjectURL = originalRevokeObjectURL;
    }
  });
});
