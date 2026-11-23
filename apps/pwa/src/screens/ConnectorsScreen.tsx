import { useState } from "react";
import { createAgent, deleteAgent, setConnector, type Bootstrap } from "../api/client";
import {
  channelList,
  CONNECTORS,
  initialConnectorsState,
  markToggling,
  type ConnectorName,
} from "../state/connectors";
import { AgentAvatarChoices } from "../components/AgentAvatarChoices";
import { builtinAvatarSrc, DEFAULT_AVATAR_SRC } from "../state/avatar";
import { useLanguage } from "../state/language";

export function ConnectorsScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const { t } = useLanguage();
  const [state, setState] = useState(() => initialConnectorsState(bootstrap.agents ?? []));
  const [granted, setGranted] = useState<Record<string, boolean>>({});
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [purpose, setPurpose] = useState("");
  const [avatar, setAvatar] = useState("default");

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
      const agent = await createAgent({ name: name.trim(), role, purpose, avatar });
      setState((prev) => ({ ...prev, agents: [...prev.agents, agent], error: null }));
      setName("");
      setRole("");
      setPurpose("");
      setAvatar("default");
    } catch (err) {
      setState((prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : t("ui.agents.create_failed", "Gagal membuat agen."),
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
          <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <img className="app-logo" src={c.logo} alt={`Logo ${c.name}`} />
            <strong>{c.name}</strong>{" "}
            <span className="badge">
              {c.status === "available" ? "Tersedia" : "Belum tersedia"}
            </span>
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
            <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <img
                src={builtinAvatarSrc(agent.avatar) ?? DEFAULT_AVATAR_SRC}
                alt=""
                width={40}
                height={40}
                style={{ borderRadius: "50%" }}
              />
              <strong>{agent.name}</strong>
              {agent.required ? <span className="badge">Wajib</span> : null}
            </span>
            <p className="muted">
              {agent.role ? `Peran: ${agent.role}` : "Peran: —"}
              {agent.purpose ? ` · Tujuan: ${agent.purpose}` : ""}
            </p>
            {CONNECTORS.map((c) => {
              const isGranted = granted[grantKey(agent.agent_id, c.name)] ?? false;
              const busy = state.toggling[`${agent.agent_id}:${c.name}`] ?? false;
              return (
                <div key={c.name} className="connector-row">
                  <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <img className="app-logo" src={c.logo} alt={`Logo ${c.label}`} />
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

      <h2>{t("ui.agents.create_title", "Buat agen baru")}</h2>
      <p className="muted">{t("ui.agents.create_note", "Maks 3 agen: Puspa + 2 agen buatanmu. Puspa tidak bisa dihapus.")}</p>
      <form onSubmit={onCreateAgent} className="stacked-form">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("ui.agents.name_placeholder", "Nama agen")}
          aria-label={t("ui.agents.name_placeholder", "Nama agen")}
          required
        />
        <input
          value={role}
          onChange={(e) => setRole(e.target.value)}
          placeholder={t("ui.agents.role_placeholder", "Peran (opsional)")}
          aria-label={t("ui.agents.role_placeholder", "Peran (opsional)")}
        />
        <input
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          placeholder={t("ui.agents.purpose_placeholder", "Tujuan (opsional)")}
          aria-label={t("ui.agents.purpose_placeholder", "Tujuan (opsional)")}
        />
        <label style={{ fontWeight: 700 }}>
          {t("ui.agents.icon_label", "Ikon agent")}
          <span className="muted" style={{ display: "block", fontWeight: 400, marginTop: 4 }}>
            {t("ui.agents.icon_group", "Pilih ikon agent")}
          </span>
        </label>
        <AgentAvatarChoices value={avatar} onSelect={setAvatar} />
        <button type="submit">{t("ui.agents.create_button", "Buat agen")}</button>
      </form>
    </div>
  );
}
