/**
 * Tugas sub-screens, Space port: Misi, Delegasi, Progres, Antrean.
 *
 * Wired to the real APIs: missions from GET /api/missions,
 * delegation from the live office projection (the same SSE the
 * task rail reads), progress from the same projection, and the
 * approval queue from the bootstrap approvals + POST
 * /api/persetujuan/{id}. No demo approvals: an empty queue says
 * so.
 */
import { useEffect, useState } from "react";
import { decideApproval, type Bootstrap } from "../../api/client";
import { fetchMissions, type Mission } from "../../api/missions";
import {
  applyDecision,
  initialApprovalsState,
  markDeciding,
  pendingApprovals,
} from "../../state/approvals";
import { officeAgents } from "../../state/office";
import { ApprovalCard } from "../../components/ApprovalCard";
import { TaskProgressRail } from "../../components/TaskProgressRail";
import { AgentMascot } from "../../components/Mascot";
import { Icon } from "../../components/icons";
import { PageHead, useLiveOffice, useToast } from "./shared";

export function MissionsScreen() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    fetchMissions()
      .then((result) => {
        if (!cancelled) setMissions(result.missions ?? []);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Gagal memuat misi.");
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="page">
      <PageHead title="Misi" back />
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      {missions.length === 0 && !error ? (
        <p className="foot-note">
          Belum ada misi minggu ini. Misi baru muncul dari chat — minta Puspa
          buatkan misi untukmu.
        </p>
      ) : (
        missions.map((mission) => {
          const done = mission.steps.filter((s) => s.status === "done").length;
          const total = mission.steps.length || 1;
          const pct = Math.round((done / total) * 100);
          return (
            <div className="card flat" style={{ marginTop: 12 }} key={mission.id}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span
                  className="check"
                  style={{ width: 30, height: 30, flexBasis: 30 }}
                >
                  {mission.status === "done" ? <Icon name="check" size={16} /> : null}
                </span>
                <div style={{ flex: 1 }}>
                  <b>{mission.title}</b>
                </div>
                <span className="tag gray">{mission.status}</span>
                <span className="tag peach">
                  {done}/{total}
                </span>
              </div>
              <div className="progress" style={{ marginTop: 11 }}>
                <i style={{ width: `${pct}%` }} />
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

export function DelegationScreen() {
  const { office, connected } = useLiveOffice();
  return (
    <div className="page">
      <PageHead title="Tugas Delegasi" back />
      <p className="muted">
        Tugas yang sedang dikerjakan agen — sama seperti rail di layar Chat,
        dibaca dari proyeksi live.
      </p>
      <TaskProgressRail office={office} connected={connected} />
      <p className="foot-note">
        Delegasikan tugas baru dari chat: tulis tugasnya, Puspa membaginya ke
        tim.
      </p>
    </div>
  );
}

export function ProgressScreen() {
  const { office, connected } = useLiveOffice();
  const team = officeAgents(office);
  return (
    <div className="page">
      <PageHead title="Progres Tugas" back />
      <div className="grid2" style={{ marginTop: 16 }}>
        <div className="card">
          <b style={{ fontSize: 16 }}>Progres tugas hari ini</b>
          <div style={{ marginTop: 12 }}>
            <TaskProgressRail office={office} connected={connected} />
          </div>
        </div>
        <div>
          <div className="card flat">
            <b>Tim yang bekerja</b>
            {team.length === 0 ? (
              <p className="muted">Tidak ada agen yang bekerja saat ini.</p>
            ) : (
              team.map((agent) => (
                <div className="row-item" key={agent.agent_id}>
                  <AgentMascot agent="puspa" size={30} state="idle" />
                  <span className="grow">
                    <span className="t" style={{ fontSize: 14.5 }}>
                      {agent.agent_id}
                    </span>
                  </span>
                  <span className="tag gray">{agent.status}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function ApprovalQueueScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const [state, setState] = useState(() =>
    initialApprovalsState(bootstrap.approvals ?? []),
  );
  const [error, setError] = useState("");
  const { show, toast } = useToast();
  const pending = pendingApprovals(state);

  async function decide(requestId: string, approve: boolean) {
    if (state.deciding[requestId]) return;
    setState((prev) => markDeciding(prev, requestId));
    setError("");
    try {
      await decideApproval(requestId, approve);
      setState((prev) =>
        applyDecision(prev, requestId, approve ? "approved" : "rejected"),
      );
      show(approve ? "Disetujui & dibayar." : "Ditolak. Puspa tidak akan membayar tanpa izinmu.");
    } catch (err) {
      setState((prev) => ({ ...prev, deciding: { ...prev.deciding, [requestId]: false } }));
      setError(err instanceof Error ? err.message : "Gagal memutuskan persetujuan.");
    }
  }

  return (
    <div className="page">
      <PageHead title="Antrean Persetujuan" back />
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      <div>
        {pending.length ? (
          pending.map((a) => (
            <ApprovalCard
              key={a.request_id}
              item={a}
              deciding={!!state.deciding[a.request_id]}
              onDecide={(requestId, approve) => void decide(requestId, approve)}
            />
          ))
        ) : (
          <div className="card empty-note" style={{ marginTop: 14 }}>
            <p style={{ margin: 0, fontSize: 15 }}>
              Antrean kosong. Semua aksi sudah kamu putuskan.
            </p>
          </div>
        )}
      </div>
      <h2 className="sec">Log Audit</h2>
      <div className="card flat" style={{ padding: "6px 14px" }}>
        <div className="row-item">
          <span className="sq-ic">
            <Icon name="check" size={22} />
          </span>
          <span className="grow">
            <span className="t" style={{ fontSize: 14.5 }}>
              Setiap pembayaran tercatat di sini
            </span>
          </span>
        </div>
      </div>
      {toast}
    </div>
  );
}
