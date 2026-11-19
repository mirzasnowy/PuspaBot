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
