import type { GraphEdge, GraphNode } from "../api/client";

export interface GraphViewProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedName: string | null;
  onSelect: (name: string) => void;
}

/**
 * The visible second brain (B18): the user's knowledge graph as
 * a real SVG drawing, not a mock. Nodes sit on a circle in name
 * order (a fixed, deterministic layout — no layout research,
 * out of scope for this ticket); edges are straight lines with
 * the relation written along them. Clicking a node selects it
 * for the inspect panel. A plain list under the drawing keeps
 * the graph usable when the drawing is too crowded, and marks
 * code-graph nodes (read-only) so the user can tell them apart.
 */
export function GraphView({ nodes, edges, selectedName, onSelect }: GraphViewProps) {
  const width = 640;
  const height = 420;
  const centerX = width / 2;
  const centerY = height / 2;
  const radius = Math.min(width, height) / 2 - 50;
  const positions = new Map<string, { x: number; y: number }>();
  nodes.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / Math.max(nodes.length, 1) - Math.PI / 2;
    positions.set(node.name, {
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    });
  });
  const shortLabel = (name: string) =>
    name.length > 18 ? `${name.slice(0, 17)}…` : name;

  if (nodes.length === 0) {
    return <p className="muted">Graf masih kosong. Puspa membangunnya dari percakapanmu.</p>;
  }

  return (
    <div>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Graf pengetahuan"
        style={{ maxWidth: "100%", height: "auto", display: "block" }}
      >
        {edges.map((edge) => {
          const from = positions.get(edge.subject);
          const to = positions.get(edge.object);
          if (!from || !to) return null;
          return (
            <g key={`${edge.subject}|${edge.relation}|${edge.object}`}>
              <line
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke="var(--hatch-widget-border)"
                strokeWidth={1.5}
              />
              <text
                x={(from.x + to.x) / 2}
                y={(from.y + to.y) / 2 - 4}
                textAnchor="middle"
                fontSize={11}
                fill="var(--hatch-widget-muted)"
              >
                {edge.relation}
              </text>
            </g>
          );
        })}
        {nodes.map((node) => {
          const pos = positions.get(node.name);
          if (!pos) return null;
          const selected = node.name === selectedName;
          return (
            <g
              key={node.name}
              onClick={() => onSelect(node.name)}
              style={{ cursor: "pointer" }}
              role="button"
              aria-label={`Node ${node.name}`}
            >
              <circle
                cx={pos.x}
                cy={pos.y}
                r={selected ? 26 : 20}
                fill={
                  node.is_code_node
                    ? "var(--hatch-widget-surface-muted)"
                    : "var(--hatch-widget-surface)"
                }
                stroke={selected ? "var(--hatch-widget-accent)" : "var(--hatch-widget-border)"}
                strokeWidth={selected ? 3 : 1.5}
              />
              <text
                x={pos.x}
                y={pos.y + 4}
                textAnchor="middle"
                fontSize={11}
                fill="var(--hatch-widget-text)"
              >
                {shortLabel(node.name)}
              </text>
            </g>
          );
        })}
      </svg>
      <ul>
        {nodes.map((node) => (
          <li key={node.name}>
            <button type="button" onClick={() => onSelect(node.name)}>
              {node.name}
            </button>
            {node.kind ? ` (${node.kind})` : ""}
            {node.is_code_node ? " — kode, hanya bisa dibaca" : ""}
            {node.name === selectedName ? " — dipilih" : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}
