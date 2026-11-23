import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { GraphView } from "./GraphView";
import type { GraphEdge, GraphNode } from "../api/client";

function node(name: string, extra: Partial<GraphNode> = {}): GraphNode {
  return {
    name,
    kind: "phone",
    sources: ["episode-1#p0"],
    fact_count: 1,
    is_code_node: false,
    ...extra,
  };
}

const nodes = [node("samsung galaxy s24"), node("tokopedia")];
const edges: GraphEdge[] = [
  { subject: "samsung galaxy s24", relation: "sells", object: "tokopedia", sources: [] },
];

describe("GraphView", () => {
  it("draws nodes and their edge relation", () => {
    render(<GraphView nodes={nodes} edges={edges} selectedName={null} onSelect={() => {}} />);
    expect(screen.getByRole("img", { name: "Graf pengetahuan" })).toBeTruthy();
    expect(screen.getByText("sells")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Node samsung galaxy s24" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Node tokopedia" })).toBeTruthy();
  });

  it("calls onSelect with the node name when a node is clicked", () => {
    const onSelect = vi.fn();
    render(<GraphView nodes={nodes} edges={edges} selectedName={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Node tokopedia" }));
    expect(onSelect).toHaveBeenCalledWith("tokopedia");
  });

  it("marks code-graph nodes as read-only", () => {
    const withCode = [...nodes, node("puspa/billing.py", { kind: "file", is_code_node: true })];
    render(<GraphView nodes={withCode} edges={edges} selectedName={null} onSelect={() => {}} />);
    expect(screen.getByText(/kode, hanya bisa dibaca/)).toBeTruthy();
  });

  it("shows an empty state when the graph has no nodes", () => {
    render(<GraphView nodes={[]} edges={[]} selectedName={null} onSelect={() => {}} />);
    expect(screen.getByText(/Graf masih kosong/)).toBeTruthy();
  });
});

describe("GraphView interactivity (B18)", () => {
  it("offers zoom in, zoom out, and reset with a live zoom level", () => {
    render(<GraphView nodes={nodes} edges={edges} selectedName={null} onSelect={() => {}} />);
    expect(screen.getByText("100%")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Perbesar graf" }));
    expect(screen.getByText("125%")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Perkecil graf" }));
    expect(screen.getByText("100%")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Perbesar graf" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset tampilan graf" }));
    expect(screen.getByText("100%")).toBeTruthy();
  });

  it("a drag moves a node and does not select it; a plain click still selects", () => {
    const onSelect = vi.fn();
    render(<GraphView nodes={nodes} edges={edges} selectedName={null} onSelect={onSelect} />);
    const nodeButton = screen.getByRole("button", { name: "Node tokopedia" });
    const svg = screen.getByRole("img", { name: "Graf pengetahuan" });
    // jsdom has no layout: give the svg a box so pointer math works.
    svg.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 640, height: 420 }) as DOMRect;
    // jsdom has no PointerEvent, so fireEvent.pointerDown carries
    // no coordinates; dispatch MouseEvents of the pointer type
    // instead — React only reads the event type and clientX/Y.
    const pointer = (type: string, target: Element, x: number, y: number) =>
      target.dispatchEvent(
        new MouseEvent(type, { bubbles: true, clientX: x, clientY: y }),
      );
    pointer("pointerdown", nodeButton, 100, 100);
    pointer("pointermove", svg, 160, 100);
    pointer("pointerup", svg, 160, 100);
    fireEvent.click(nodeButton);
    expect(onSelect).not.toHaveBeenCalled();
    // A fresh click with no drag selects.
    fireEvent.click(nodeButton);
    expect(onSelect).toHaveBeenCalledWith("tokopedia");
  });
});

describe("GraphView top-bar actions (Nero, 24 Nov)", () => {
  it("shows + / eye / Ekspor .md only when their callbacks are given, and calls them", () => {
    const onAddNode = vi.fn();
    const onFullscreen = vi.fn();
    const onExport = vi.fn();
    const { rerender } = render(
      <GraphView
        nodes={nodes}
        edges={edges}
        selectedName={null}
        onSelect={vi.fn()}
        onAddNode={onAddNode}
        onFullscreen={onFullscreen}
        onExport={onExport}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Tambah simpul" }));
    expect(onAddNode).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Lihat graf fullscreen" }));
    expect(onFullscreen).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Ekspor .md" }));
    expect(onExport).toHaveBeenCalledTimes(1);
    // Without the callbacks the buttons are gone, not dead.
    rerender(
      <GraphView nodes={nodes} edges={edges} selectedName={null} onSelect={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Tambah simpul" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Lihat graf fullscreen" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ekspor .md" })).toBeNull();
  });
});
