/**
 * ChatCard (P-12): the four rich cards that render inside a chat
 * message — draft, options, chart, and map. The card payload is
 * declarative JSON (see `puspa/pwa/chat_cards.py`): the component
 * only renders what the payload describes.
 *
 * - Draft: the draft body, copyable, with a Mail link when the
 *   draft is an email with a recipient.
 * - Options: a question with tappable options; tapping one sends
 *   its label as a chat message (a choice, not a form — the
 *   interactive components are P-14).
 * - Chart: plain SVG bars from the G28 Insights points, no new
 *   dependency.
 * - Map: free OpenStreetMap tiles (`tile.openstreetmap.org`),
 *   markers pinned over the tiles, and the route as an SVG
 *   polyline. The tiles load client-side; no live browser in
 *   this VM proves a tile renders, so the map is verified by
 *   its tile URL maths and its rendered template, not by a
 *   loaded tile.
 */
import { useState } from "react";
import { formatRupiah, type CardOption, type ChatCard } from "../api/client";
import { mapTiles, projectPoint } from "../state/chatCards";

export interface ChatCardProps {
  card: ChatCard;
  onPickOption?: (option: CardOption) => void;
}

const MAP_WIDTH = 320;
const MAP_HEIGHT = 240;
const CHART_WIDTH = 320;
const CHART_HEIGHT = 160;

/** Copy text: the Clipboard API first, a hidden textarea second. Returns false when neither works. */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the textarea fallback below.
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

function DraftCardView({ card }: { card: Extract<ChatCard, { kind: "draft" }> }) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);

  async function onCopy() {
    const ok = await copyText(card.body);
    setCopied(ok);
    setCopyFailed(!ok);
  }

  return (
    <article className="card chat-card chat-card-draft" data-testid="chat-card-draft">
      <strong>{card.title}</strong>
      {card.recipient ? <p className="muted">Ke: {card.recipient}</p> : null}
      {card.subject ? <p className="muted">Perihal: {card.subject}</p> : null}
      <pre>{card.body}</pre>
      <div className="approval-actions">
        <button type="button" onClick={onCopy}>
          {copied ? "Disalin" : "Salin draft"}
        </button>
        {card.mailto ? (
          <a className="button-secondary" href={card.mailto}>
            Buka di Mail
          </a>
        ) : null}
      </div>
      {copyFailed ? (
        <p role="alert" className="error">
          Gagal menyalin — salin manual dari teks di atas.
        </p>
      ) : null}
    </article>
  );
}

function OptionsCardView({
  card,
  onPickOption,
}: {
  card: Extract<ChatCard, { kind: "options" }>;
  onPickOption?: (option: CardOption) => void;
}) {
  const [pickedId, setPickedId] = useState<string | null>(null);

  function onPick(option: CardOption) {
    if (pickedId !== null) return;
    setPickedId(option.id);
    onPickOption?.(option);
  }

  return (
    <article className="card chat-card chat-card-options" data-testid="chat-card-options">
      <strong>{card.question}</strong>
      <div className="approval-actions">
        {card.options.map((option) => (
          <button
            key={option.id}
            type="button"
            className="button-secondary"
            disabled={pickedId !== null}
            onClick={() => onPick(option)}
          >
            {option.label}
            {option.detail ? ` — ${option.detail}` : ""}
          </button>
        ))}
      </div>
      {pickedId !== null ? (
        <p className="muted">Dipilih: {card.options.find((o) => o.id === pickedId)?.label}</p>
      ) : null}
    </article>
  );
}

function formatChartValue(metric: string, value: number): string {
  return metric === "cost_idr" ? formatRupiah(value) : String(Math.trunc(value));
}

function ChartCardView({ card }: { card: Extract<ChatCard, { kind: "chart" }> }) {
  const max = Math.max(1, ...card.points.map((p) => p.value));
  const barWidth = CHART_WIDTH / card.points.length;
  return (
    <article className="card chat-card chat-card-chart" data-testid="chat-card-chart">
      <strong>{card.title}</strong>
      <svg
        width={CHART_WIDTH}
        height={CHART_HEIGHT}
        role="img"
        aria-label={`${card.title}: ${card.points.map((p) => `${p.label} ${formatChartValue(card.metric, p.value)}`).join(", ")}`}
      >
        {card.points.map((point, i) => {
          const height = (point.value / max) * (CHART_HEIGHT - 24);
          const x = i * barWidth + 2;
          return (
            <g key={point.label}>
              <title>
                {point.label}: {formatChartValue(card.metric, point.value)}
              </title>
              <rect
                data-testid="chart-bar"
                x={x}
                y={CHART_HEIGHT - 20 - height}
                width={Math.max(1, barWidth - 4)}
                height={height}
                fill="currentColor"
              />
              <text x={x + (barWidth - 4) / 2} y={CHART_HEIGHT - 6} textAnchor="middle" fontSize="9">
                {point.label.slice(5)}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="muted">
        Tertinggi:{" "}
        {formatChartValue(
          card.metric,
          card.points.every((p) => p.value === 0) ? 0 : max,
        )}
      </p>
    </article>
  );
}

function MapCardView({ card }: { card: Extract<ChatCard, { kind: "map" }> }) {
  const tiles = mapTiles(card, MAP_WIDTH, MAP_HEIGHT);
  const routePoints = card.route
    .map((point) => projectPoint(card, point, MAP_WIDTH, MAP_HEIGHT))
    .map((p) => `${p.x},${p.y}`)
    .join(" ");
  return (
    <article className="card chat-card chat-card-map" data-testid="chat-card-map">
      <strong>{card.title}</strong>
      <div
        style={{
          position: "relative",
          width: MAP_WIDTH,
          height: MAP_HEIGHT,
          overflow: "hidden",
        }}
      >
        {tiles.map((tile) => (
          <img
            key={`${tile.z}/${tile.x}/${tile.y}`}
            data-testid="map-tile"
            src={tile.url}
            alt={`Peta ${tile.z}/${tile.x}/${tile.y}`}
            width={256}
            height={256}
            style={{ position: "absolute", left: tile.leftPx, top: tile.topPx }}
          />
        ))}
        {card.route.length >= 2 ? (
          <svg
            data-testid="map-route"
            width={MAP_WIDTH}
            height={MAP_HEIGHT}
            style={{ position: "absolute", left: 0, top: 0 }}
          >
            <polyline
              points={routePoints}
              fill="none"
              stroke="currentColor"
              strokeWidth={3}
            />
          </svg>
        ) : null}
        {card.markers.map((marker, i) => {
          const p = projectPoint(card, marker, MAP_WIDTH, MAP_HEIGHT);
          return (
            <span
              key={`${marker.lat}-${marker.lon}-${i}`}
              data-testid="map-marker"
              title={marker.label ?? `${marker.lat}, ${marker.lon}`}
              style={{
                position: "absolute",
                left: p.x,
                top: p.y,
                transform: "translate(-50%, -100%)",
              }}
            >
              ●{marker.label ? ` ${marker.label}` : ""}
            </span>
          );
        })}
      </div>
      <p className="muted">
        Peta: OpenStreetMap (tile gratis). {card.markers.length} marker
        {card.route.length >= 2 ? `, rute ${card.route.length} titik` : ""}.
      </p>
    </article>
  );
}

export function ChatCard({ card, onPickOption }: ChatCardProps) {
  switch (card.kind) {
    case "draft":
      return <DraftCardView card={card} />;
    case "options":
      return <OptionsCardView card={card} onPickOption={onPickOption} />;
    case "chart":
      return <ChartCardView card={card} />;
    case "map":
      return <MapCardView card={card} />;
    default:
      return null;
  }
}
