/**
 * Shared Space screen pieces: the big page head (the screen's ONE
 * title — no header row above it, Nero 23 Nov), title-only rows
 * with a distinctive outline icon and a right chevron, group
 * cards, and a small toast for action feedback.
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { parseSseEvent, type OfficeProjection } from "../../api/client";
import { applyOfficeEvent } from "../../state/office";
import { Icon } from "../../components/icons";

export function PageHead({
  title,
  back,
  action,
}: {
  title: string;
  back?: boolean;
  action?: ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
      {back ? (
        <button
          type="button"
          className="circle-btn"
          onClick={() => window.history.back()}
          aria-label="Kembali"
        >
          <Icon name="backchev" size={20} strokeWidth={3.4} />
        </button>
      ) : null}
      <div style={{ flex: 1, minWidth: 0 }}>
        <h1 className="page">{title}</h1>
      </div>
      {action}
    </div>
  );
}

export function RowItem({
  href,
  icon,
  cls,
  title,
  meta,
  onClick,
}: {
  href?: string;
  icon: string;
  cls?: string;
  title: string;
  meta?: ReactNode;
  onClick?: () => void;
}) {
  const inner = (
    <>
      <span className={`sq-ic ${cls ?? ""}`}>
        <Icon name={icon} size={22} />
      </span>
      <span className="grow">
        <span className="t">{title}</span>
      </span>
      {meta}
      <Icon name="chev" size={17} />
    </>
  );
  if (href) {
    return (
      <a className="row-item" href={href} style={{ textDecoration: "none", color: "inherit" }}>
        {inner}
      </a>
    );
  }
  return (
    <button type="button" className="row-item" onClick={onClick}>
      {inner}
    </button>
  );
}

export function GroupCard({
  label,
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  return (
    <div className="card flat group-card" style={{ marginTop: 14 }}>
      {label ? <div className="group-label">{label}</div> : null}
      {children}
    </div>
  );
}

export function useToast() {
  const [note, setNote] = useState("");
  const show = useCallback((message: string) => {
    setNote(message);
    window.setTimeout(() => setNote(""), 2600);
  }, []);
  const toast = note ? (
    <div className="toast show" role="status">
      {note}
    </div>
  ) : null;
  return { show, toast };
}

/**
 * Live office projection over the same /sse stream the Chat
 * screen reads: the delegation, progress, and live screens all
 * render the same live data, never a second fetch.
 */
export function useLiveOffice() {
  const [office, setOffice] = useState<OfficeProjection | null>(null);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const source = new EventSource("/sse");
    source.onmessage = (event) => {
      const parsed = parseSseEvent(event.data);
      if (parsed) {
        setOffice((prev) => applyOfficeEvent(prev, parsed));
        setConnected(true);
      }
    };
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, []);
  return { office, connected };
}
