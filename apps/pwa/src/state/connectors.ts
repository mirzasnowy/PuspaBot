/**
 * Connectors screen store: agents seeded from the bootstrap plus
 * the per-agent connector grants. The server is the source of truth
 * for grants (POST /api/konektor/{name}); this store only tracks
 * the in-flight toggle so the UI stays responsive.
 */
import type { Agent } from "../api/client";

export type ConnectorName = "gmail" | "drive" | "calendar";

export const CONNECTORS: { name: ConnectorName; label: string; scope: string }[] = [
  { name: "gmail", label: "Gmail", scope: "Read mail" },
  { name: "drive", label: "Drive", scope: "Read files" },
  { name: "calendar", label: "Calendar", scope: "Read events" },
];

export interface ConnectorsState {
  agents: Agent[];
  toggling: Record<string, boolean>;
  error: string | null;
}

export function initialConnectorsState(agents: Agent[] = []): ConnectorsState {
  return { agents, toggling: {}, error: null };
}

export function toggleKey(agentId: string, connector: ConnectorName): string {
  return `${agentId}:${connector}`;
}

export function markToggling(
  state: ConnectorsState,
  agentId: string,
  connector: ConnectorName,
  value: boolean,
): ConnectorsState {
  return {
    ...state,
    toggling: { ...state.toggling, [toggleKey(agentId, connector)]: value },
  };
}

/** The mandatory Puspa agent is always present and cannot be removed. */
export function puspaAgent(agents: Agent[]): Agent | undefined {
  return agents.find((a) => a.required === true || a.agent_id.startsWith("puspa-"));
}

/** Channels checklist (hermes-agent Channels page, as a section). */
export interface ChannelStatus {
  name: string;
  kind: "connector" | "messaging";
  status: "connected" | "available" | "not_available";
  note: string;
}

export function channelList(): ChannelStatus[] {
  return [
    { name: "Gmail", kind: "connector", status: "available", note: "Grant per agent below." },
    { name: "Drive", kind: "connector", status: "available", note: "Grant per agent below." },
    { name: "Calendar", kind: "connector", status: "available", note: "Grant per agent below." },
    {
      name: "Telegram",
      kind: "messaging",
      status: "not_available",
      note: "Planned after MVP (G14 gateway).",
    },
    {
      name: "WhatsApp",
      kind: "messaging",
      status: "not_available",
      note: "Planned after MVP (G14 gateway).",
    },
  ];
}
