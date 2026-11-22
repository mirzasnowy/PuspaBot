/**
 * Auto-curated knowledge wiki (COW-06): typed fetchers over the
 * wiki API (`puspa/pwa/wiki_api.py`). The screen renders the
 * server-rendered HTML the API returns (the server escapes and
 * renders the Markdown itself), so this file carries no Markdown
 * parser and no HTML builder.
 */
import { getJson, postJson } from "./client";

export interface WikiTopic {
  slug: string;
  title: string;
  entries: number;
  sources: string[];
  entities: string[];
  updated_at: string | null;
}

export interface WikiPage {
  slug: string;
  title: string;
  markdown: string;
  html: string;
  sources: string[];
  entities: string[];
  updated_at: string | null;
}

export interface WikiGraphNode {
  name: string;
  kind: string | null;
  description: string;
  sources: string[];
  fact_count: number;
  seen_at: string | null;
  is_code_node: boolean;
}

export interface WikiGraphEdge {
  subject: string;
  relation: string;
  object: string;
  sources: string[];
}

export interface WikiGraph {
  topic: string;
  title: string;
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
  total_nodes: number;
  total_edges: number;
}

export interface WikiBuildReport {
  user_id: string;
  wiki_dir: string;
  pages_written: number;
  files: string[];
  generated_at: string;
  entries: number;
  topics: string[];
}

export function fetchWikiTopics(fetchFn: typeof fetch = fetch): Promise<{ topics: WikiTopic[] }> {
  return getJson<{ topics: WikiTopic[] }>("/api/wiki/topics", fetchFn);
}

export function fetchWikiPage(slug: string, fetchFn: typeof fetch = fetch): Promise<WikiPage> {
  return getJson<WikiPage>(`/api/wiki/pages/${encodeURIComponent(slug)}`, fetchFn);
}

export function fetchWikiGraph(topic: string, fetchFn: typeof fetch = fetch): Promise<WikiGraph> {
  return getJson<WikiGraph>(`/api/wiki/graph?topic=${encodeURIComponent(topic)}`, fetchFn);
}

export function buildWiki(fetchFn: typeof fetch = fetch): Promise<WikiBuildReport> {
  return postJson<WikiBuildReport>("/api/wiki/build", {}, fetchFn);
}
