/**
 * Mockup editor state (B7): the client-side mirror of the server
 * design doc (`puspa/mockup/doc.py`).
 *
 * The design doc is the single source of truth, so the editor
 * never keeps its own layout model: every drag, text edit, colour
 * change, reorder, and image upload is a numbered op on the same
 * doc the server stores, and the same op is POSTed to
 * `/api/mockups/{id}/ops` (uploads go to `/api/mockups/{id}/assets`
 * as an `add_asset` op).
 * Undo/redo here are whole-doc snapshots, exactly like the server
 * `DesignSession`; the op log itself is append-only and is never
 * rewritten by an undo.
 *
 * Drag-and-drop: the canvas reports a drop as (nodeId, dropX,
 * dropY) in frame coordinates, and `dragMoveOp` turns it into a
 * `move_node` op with the grab offset subtracted, so the node
 * lands where the user dropped it instead of jumping. The
 * round-trip (drag -> op -> doc -> render) is covered by
 * `mockup.test.ts`.
 */

export type MockupNodeType =
  | "text"
  | "image"
  | "button"
  | "card"
  | "list"
  | "form"
  | "table"
  | "chart";

export interface MockupAsset {
  kind: string;
  data_uri: string;
  name: string;
}

export interface MockupNode {
  id: string;
  type: MockupNodeType;
  x: number;
  y: number;
  w: number;
  h: number;
  text?: string;
  style?: Record<string, string | number | boolean>;
  src?: string;
  children?: MockupNode[];
  props?: Record<string, unknown>;
}

export interface MockupTheme {
  primary: string;
  background: string;
  text: string;
  accent: string;
  font: string;
  radius: number;
}

export interface MockupOp {
  seq?: number;
  type:
    | "add_node"
    | "remove_node"
    | "move_node"
    | "set_text"
    | "set_style"
    | "set_image"
    | "reorder"
    | "set_theme"
    | "set_title"
    | "add_asset";
  node_id?: string;
  parent_id?: string;
  payload?: Record<string, unknown>;
  note?: string;
}

export interface MockupDoc {
  id: string;
  title: string;
  template: string;
  frame: { width: number; height: number };
  theme: MockupTheme;
  assets: Record<string, MockupAsset>;
  nodes: MockupNode[];
  version: number;
  ops: MockupOp[];
  decisions: { seq: number; note: string }[];
}

export interface MockupVariant {
  doc: MockupDoc;
  issues: { kind: string; node_id: string | null; detail: string }[];
}

export function findNode(nodes: MockupNode[], nodeId: string): MockupNode | null {
  for (const node of nodes) {
    if (node.id === nodeId) return node;
    const found = findNode(node.children ?? [], nodeId);
    if (found) return found;
  }
  return null;
}

function cloneDoc(doc: MockupDoc): MockupDoc {
  return JSON.parse(JSON.stringify(doc)) as MockupDoc;
}

/** Apply one op to a copy of the doc and return the copy. */
export function applyOp(doc: MockupDoc, op: MockupOp): MockupDoc {
  const next = cloneDoc(doc);
  const stored: MockupOp = { ...op, seq: next.ops.length + 1 };
  const payload = (op.payload ?? {}) as Record<string, unknown>;
  if (op.type === "move_node" && op.node_id) {
    const node = findNode(next.nodes, op.node_id);
    if (!node) throw new Error(`unknown node ${op.node_id}`);
    node.x = Math.max(0, Number(payload.x));
    node.y = Math.max(0, Number(payload.y));
  } else if (op.type === "set_text" && op.node_id) {
    const node = findNode(next.nodes, op.node_id);
    if (!node) throw new Error(`unknown node ${op.node_id}`);
    node.text = String(payload.text ?? "");
  } else if (op.type === "set_style" && op.node_id) {
    const node = findNode(next.nodes, op.node_id);
    if (!node) throw new Error(`unknown node ${op.node_id}`);
    node.style = {
      ...(node.style ?? {}),
      ...((payload.style as Record<string, string | number | boolean>) ?? {}),
    };
  } else if (op.type === "set_theme") {
    next.theme = { ...next.theme, ...((payload.theme as Partial<MockupTheme>) ?? {}) };
  } else if (op.type === "set_title") {
    next.title = String(payload.title ?? next.title);
  } else if (op.type === "reorder" && op.node_id) {
    const siblings = parentList(next.nodes, op.node_id);
    if (!siblings) throw new Error(`unknown node ${op.node_id}`);
    const current = siblings.findIndex((n) => n.id === op.node_id);
    const [node] = siblings.splice(current, 1);
    siblings.splice(Math.min(Number(payload.index ?? 0), siblings.length), 0, node);
  } else if (op.type === "remove_node" && op.node_id) {
    const siblings = parentList(next.nodes, op.node_id);
    if (!siblings) throw new Error(`unknown node ${op.node_id}`);
    const index = siblings.findIndex((n) => n.id === op.node_id);
    siblings.splice(index, 1);
  } else if (op.type === "add_node") {
    next.nodes.push(payload.node as MockupNode);
  } else if (op.type === "set_image" && op.node_id) {
    const node = findNode(next.nodes, op.node_id);
    if (!node) throw new Error(`unknown node ${op.node_id}`);
    node.src = String(payload.asset_id ?? "");
  } else if (op.type === "add_asset") {
    const assetId = String(payload.asset_id ?? "");
    if (!assetId) throw new Error("add_asset needs an asset_id");
    next.assets[assetId] = payload.asset as MockupAsset;
  } else {
    throw new Error(`unsupported op ${op.type}`);
  }
  next.ops.push(stored);
  next.version += 1;
  if (op.note) next.decisions.push({ seq: stored.seq ?? next.ops.length, note: op.note });
  return next;
}

function parentList(nodes: MockupNode[], nodeId: string): MockupNode[] | null {
  for (const node of nodes) {
    if (node.id === nodeId) return nodes;
    const found = parentList(node.children ?? [], nodeId);
    if (found) return found;
  }
  return null;
}

/**
 * Build the `move_node` op for a drop: the drop point is where the
 * pointer landed in frame coordinates, minus the grab offset
 * recorded at drag start, so the node does not jump to the cursor.
 */
export function dragMoveOp(
  doc: MockupDoc,
  nodeId: string,
  dropX: number,
  dropY: number,
  grabOffsetX: number,
  grabOffsetY: number,
): MockupOp {
  const node = findNode(doc.nodes, nodeId);
  if (!node) throw new Error(`unknown node ${nodeId}`);
  const frame = doc.frame;
  const x = Math.max(0, Math.min(dropX - grabOffsetX, frame.width - node.w));
  const y = Math.max(0, Math.min(dropY - grabOffsetY, frame.height - node.h));
  return { type: "move_node", node_id: nodeId, payload: { x, y } };
}

/** Whole-doc undo/redo history, mirroring the server DesignSession. */
export class MockupHistory {
  private past: MockupDoc[] = [];
  private future: MockupDoc[] = [];

  constructor(private doc: MockupDoc) {}

  current(): MockupDoc {
    return this.doc;
  }

  apply(op: MockupOp): MockupDoc {
    this.past.push(cloneDoc(this.doc));
    this.future = [];
    this.doc = applyOp(this.doc, op);
    return this.doc;
  }

  undo(): MockupDoc {
    const previous = this.past.pop();
    if (!previous) throw new Error("nothing to undo");
    this.future.push(cloneDoc(this.doc));
    this.doc = previous;
    return this.doc;
  }

  redo(): MockupDoc {
    const next = this.future.pop();
    if (!next) throw new Error("nothing to redo");
    this.past.push(cloneDoc(this.doc));
    this.doc = next;
    return this.doc;
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }
}
