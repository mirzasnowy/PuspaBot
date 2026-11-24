import { useEffect, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "../api/client";
import { Icon } from "./icons";
import { useLanguage } from "../state/language";

export interface GraphViewProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedName: string | null;
  onSelect: (name: string) => void;
  /** Add a node (Nero, 24 Nov): a "+" icon in the top bar. */
  onAddNode?: () => void;
  /** Open the graph fullscreen (Nero, 24 Nov): an eye icon. */
  onFullscreen?: () => void;
  /** Export the graph as Obsidian markdown (Nero, 24 Nov). */
  onExport?: () => void;
}

type Point = { x: number; y: number };

const WIDTH = 640;
const HEIGHT = 420;

/**
 * The visible second brain (B18): the user's knowledge graph as
 * a real interactive SVG drawing, not a mock. Nodes start on a
 * circle in name order (a fixed, deterministic layout); the user
 * can drag a node to a better spot, drag the background to pan,
 * scroll (or use the buttons) to zoom, and click a node to open
 * its note fullscreen. A plain list under the drawing keeps the
 * graph usable when the drawing is too crowded, and marks
 * code-graph nodes (read-only) so the user can tell them apart.
 *
 * Click vs drag: a press that moves less than 5px is a click
 * (selects the node); anything further is a drag and the
 * click that follows it is ignored.
 */
export function GraphView({
  nodes,
  edges,
  selectedName,
  onSelect,
  onAddNode,
  onFullscreen,
  onExport,
}: GraphViewProps) {
  const { t } = useLanguage();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [positions, setPositions] = useState<Map<string, Point>>(() =>
    circlePositions(nodes),
  );
  const dragRef = useRef<
    | {
        kind: "pan" | "node";
        name?: string;
        startClient: Point;
        startView: { x: number; y: number; k: number };
        startNode?: Point;
        moved: boolean;
      }
    | null
  >(null);
  const suppressClickRef = useRef(false);

  // New nodes join the circle; deleted nodes leave; dragged
  // nodes keep the spot the user put them.
  useEffect(() => {
    setPositions((prev) => {
      const next = new Map(prev);
      const fresh = circlePositions(nodes);
      for (const node of nodes) {
        if (!next.has(node.name)) next.set(node.name, fresh.get(node.name) as Point);
      }
      for (const name of Array.from(next.keys())) {
        if (!nodes.some((node) => node.name === name)) next.delete(name);
      }
      return next;
    });
  }, [nodes]);

  // Wheel zoom needs a non-passive listener so the page does
  // not scroll while the user zooms the graph (React's wheel
  // handler is passive and cannot preventDefault).
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = svg.getBoundingClientRect();
      const cursor = {
        x: (event.clientX - rect.left) * (WIDTH / rect.width),
        y: (event.clientY - rect.top) * (HEIGHT / rect.height),
      };
      setView((prev) => zoomAt(prev, cursor, event.deltaY < 0 ? 1.15 : 1 / 1.15));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  const shortLabel = (name: string) =>
    name.length > 18 ? `${name.slice(0, 17)}…` : name;

  function onPointerDown(event: React.PointerEvent<SVGSVGElement>) {
    const target = (event.target as Element).closest?.("[data-node]");
    const name = target?.getAttribute("data-node") ?? undefined;
    const startNode = name ? positions.get(name) : undefined;
    dragRef.current = {
      kind: name ? "node" : "pan",
      name,
      startClient: { x: event.clientX, y: event.clientY },
      startView: view,
      startNode,
      moved: false,
    };
    // Pointer capture keeps the drag alive when the pointer
    // leaves the drawing mid-drag; if the browser refuses it
    // (or the test env has no pointer), the drag still works
    // while the pointer stays over the drawing.
    try {
      (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
    } catch {
      // Ignore: capture is best-effort here.
    }
  }

  function onPointerMove(event: React.PointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    const dx = (event.clientX - drag.startClient.x) * (WIDTH / rect.width);
    const dy = (event.clientY - drag.startClient.y) * (HEIGHT / rect.height);
    if (Math.hypot(dx, dy) > 5) drag.moved = true;
    if (!drag.moved) return;
    if (drag.kind === "pan") {
      setView({ ...drag.startView, x: drag.startView.x + dx, y: drag.startView.y + dy });
    } else if (drag.name && drag.startNode) {
      const point = { x: drag.startNode.x + dx / drag.startView.k, y: drag.startNode.y + dy / drag.startView.k };
      setPositions((prev) => new Map(prev).set(drag.name as string, point));
    }
  }

  function onPointerUp() {
    const drag = dragRef.current;
    dragRef.current = null;
    // A real drag must not also fire the node's click. The flag
    // is consumed by that one click (see the node's onClick);
    // the timer only clears it if no click ever arrives.
    if (drag?.moved) {
      suppressClickRef.current = true;
      window.setTimeout(() => {
        suppressClickRef.current = false;
      }, 0);
    }
  }

  function zoomBy(factor: number) {
    setView((prev) => zoomAt(prev, { x: WIDTH / 2, y: HEIGHT / 2 }, factor));
  }

  function resetView() {
    setView({ x: 0, y: 0, k: 1 });
  }

  if (nodes.length === 0) {
    return <p className="muted">{t("ui.graph.empty", "Graf masih kosong. Puspa membangunnya dari percakapanmu.")}</p>;
  }

  return (
    <div>
      <div className="graph-tools" role="group" aria-label={t("ui.graph.controls", "Kontrol graf")}>
        <button type="button" onClick={() => zoomBy(1.25)} aria-label={t("ui.graph.zoom_in", "Perbesar graf")}>
          +
        </button>
        <button type="button" onClick={() => zoomBy(1 / 1.25)} aria-label={t("ui.graph.zoom_out", "Perkecil graf")}>
          −
        </button>
        <button type="button" onClick={resetView} aria-label={t("ui.graph.zoom_reset", "Reset tampilan graf")}>
          Reset
        </button>
        <span className="muted">{Math.round(view.k * 100)}%</span>
        {onAddNode ? (
          <button type="button" onClick={onAddNode} aria-label={t("ui.memory.add_node", "Tambah simpul")}>
            +
          </button>
        ) : null}
        {onFullscreen ? (
          <button
            type="button"
            onClick={onFullscreen}
            aria-label={t("ui.graph.fullscreen", "Lihat graf fullscreen")}
          >
            <Icon name="eye" size={18} />
          </button>
        ) : null}
        {onExport ? (
          <button type="button" onClick={onExport}>
            <Icon name="download" size={16} /> {t("ui.graph.export", "Ekspor .md")}
          </button>
        ) : null}
      </div>
      <svg
        ref={svgRef}
        width={WIDTH}
        height={HEIGHT}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={t("ui.graph.label", "Graf pengetahuan")}
        style={{ maxWidth: "100%", height: "auto", display: "block", touchAction: "none" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
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
                data-node={node.name}
                onClick={() => {
                  if (suppressClickRef.current) {
                    suppressClickRef.current = false;
                    return;
                  }
                  onSelect(node.name);
                }}
                style={{ cursor: "grab" }}
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
        </g>
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

function circlePositions(nodes: GraphNode[]): Map<string, Point> {
  const centerX = WIDTH / 2;
  const centerY = HEIGHT / 2;
  const radius = Math.min(WIDTH, HEIGHT) / 2 - 50;
  const positions = new Map<string, Point>();
  nodes.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / Math.max(nodes.length, 1) - Math.PI / 2;
    positions.set(node.name, {
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
    });
  });
  return positions;
}

/** Zoom around a point in SVG coordinates, clamped to 25–300%. */
function zoomAt(
  view: { x: number; y: number; k: number },
  point: Point,
  factor: number,
): { x: number; y: number; k: number } {
  const k = Math.min(3, Math.max(0.25, view.k * factor));
  const scale = k / view.k;
  return {
    k,
    x: point.x - (point.x - view.x) * scale,
    y: point.y - (point.y - view.y) * scale,
  };
}
