/**
 * WorkStrip: the slim "who is working" strip shown above the chat
 * while a task runs (revision 7 §2.1). Source is the live office
 * projection from SSE, never animation.
 */
import type { OfficeProjection } from "../api/client";
import { workingAgents } from "../state/chat";

export function WorkStrip({
  office,
  connected,
}: {
  office: OfficeProjection | null;
  connected: boolean;
}) {
  const working = workingAgents(office ?? null);
  if (working.length === 0) {
    return (
      <p className="muted" data-testid="work-strip">
        {connected ? "Live: tidak ada agen bekerja saat ini." : "Live: menunggu SSE…"}
      </p>
    );
  }
  return (
    <div className="card work-strip" data-testid="work-strip">
      <strong>Sedang bekerja:</strong> {working.join(", ")}
    </div>
  );
}
