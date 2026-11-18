import { useState } from "react";
import { createAgent, deleteAgent, setConnector, type Bootstrap } from "../api/client";
import {
  channelList,
  CONNECTORS,
  initialConnectorsState,
  markToggling,
  type ConnectorName,
} from "../state/connectors";

export function ConnectorsScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const [state, setState] = useState(() => initialConnectorsState(bootstrap.agents ?? []));
  const [granted, setGranted] = useState<Record<string, boolean>>({});
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [purpose, setPurpose] = useState("");

  function grantKey(agentId: string, connector: ConnectorName): string {
    return `${agentId}:${connector}`;
  }

  async function onToggle(agentId: string, connector: ConnectorName, revoke: boolean) {
    setState((prev) => markToggling(prev, agentId, connector, true));
    try {
      const result = await setConnector(connector, agentId, revoke ? "cabut" : "grant");
      setGranted((prev) => ({
        ...prev,
        [grantKey(agentId, connector)]: result.connected ?? !revoke,
      }));
      setState((prev) => ({ ...markToggling(prev, agentId, connector, false), error: null }));
    } catch (err) {
      setState((prev) => ({
        ...markToggling(prev, agentId, connector, false),
        error: err instanceof Error ? err.message : "Gagal mengubah konektor.",
      }));
    }
  }

  async function onCreateAgent(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    try {
      const agent = await createAgent({ name: name.trim(), role, purpose });
      setState((prev) => ({ ...prev, agents: [...prev.agents, agent], error: null }));
      setName("");
      setRole("");
      setPurpose("");
    } catch (err) {
      setState((prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : "Gagal membuat agen.",
      }));
    }
  }

  async function onDeleteAgent(agentId: string) {
    try {
      await deleteAgent(agentId);
      setState((prev) => ({
        ...prev,
        agents: prev.agents.filter((a) => a.agent_id !== agentId),
        error: null,
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : "Gagal menghapus agen.",
      }));
    }
  }

  return (
    <div>
      <h1>Konektor</h1>
      <p className="muted">
        Hubungkan dan cabut Gmail, Drive, Calendar per agen. Tiap konektor memakai scope minimal
        bawaannya.
      </p>
      {state.error ? <p role="alert" className="error">{state.error}</p> : null}

      <h2>Kanal</h2>
      {channelList().map((c) => (
        <div key={c.name} className="card">
          <strong>{c.name}</strong>{" "}
          <span className="badge">
            {c.status === "available" ? "Tersedia" : "Belum tersedia"}
          </span>
          <p className="muted">{c.note}</p>
        </div>
      ))}

      <h2>Agen</h2>
      {state.agents.length === 0 ? (
        <p className="muted">Belum ada agen. Puspa dibuat otomatis saat kamu masuk.</p>
      ) : (
        state.agents.map((agent) => (
          <div key={agent.agent_id} className="card">
            <strong>{agent.name}</strong>
            {agent.required ? <span className="badge">Wajib</span> : null}
            <p className="muted">
              {agent.role ? `Peran: ${agent.role}` : "Peran: —"}
              {agent.purpose ? ` · Tujuan: ${agent.purpose}` : ""}
            </p>
            {CONNECTORS.map((c) => {
              const isGranted = granted[grantKey(agent.agent_id, c.name)] ?? false;
              const busy = state.toggling[`${agent.agent_id}:${c.name}`] ?? false;
              return (
                <div key={c.name} className="connector-row">
                  <span>
                    {c.label} <span className="muted">({c.scope})</span>{" "}
                    {isGranted ? <span className="badge">Terhubung</span> : null}
                  </span>{" "}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onToggle(agent.agent_id, c.name, isGranted)}
                  >
                    {isGranted ? "Cabut" : "Hubungkan"}
                  </button>
                </div>
              );
            })}
            {!agent.required ? (
              <button type="button" className="button-secondary" onClick={() => onDeleteAgent(agent.agent_id)}>
                Hapus agen
              </button>
            ) : null}
          </div>
        ))
      )}
      <p className="muted">
        Status konektor di atas hanya dari sesi ini: belum ada endpoint baca daftar grant di API,
        jadi status terhubung tidak bertahan setelah muat ulang. Endpoint baca adalah follow-up.
      </p>

      <h2>Buat agen baru</h2>
      <p className="muted">Maks 3 agen: Puspa + 2 agen buatanmu. Puspa tidak bisa dihapus.</p>
      <form onSubmit={onCreateAgent} className="stacked-form">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Nama agen"
          aria-label="Nama agen"
          required
        />
        <input
          value={role}
          onChange={(e) => setRole(e.target.value)}
          placeholder="Peran (opsional)"
          aria-label="Peran agen"
        />
        <input
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          placeholder="Tujuan (opsional)"
          aria-label="Tujuan agen"
        />
        <button type="submit">Buat agen</button>
      </form>
    </div>
  );
}
