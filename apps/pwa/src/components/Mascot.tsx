/**
 * Flower mascot + animated agent avatar, ported from the Space
 * puspabot-app export (`mascot()`): one flower character per
 * agent color, leaves, eight petals, face.
 *
 * Animation (Nero, 23 Nov): the avatar is alive, like Muse's —
 * idle and working look clearly different. Idle breathes slowly
 * (gentle scale, 4s) and blinks; working bobs faster, wiggles,
 * and carries a pulsing red ring, so a glance tells whether the
 * agent is thinking. Both are transform/opacity only and stop
 * under `prefers-reduced-motion` (see styles.css).
 */
import type { AgentAvatarState } from "../state/avatar";

export function Mascot({
  color,
  size = 56,
  mood,
  state = "idle",
  label,
}: {
  color: string;
  size?: number;
  mood?: "sleep" | "wow";
  state?: AgentAvatarState;
  label?: string;
}) {
  const petals: React.ReactNode[] = [];
  for (let i = 0; i < 8; i++) {
    const a = (i * 45 * Math.PI) / 180;
    const cx = 50 + 27 * Math.cos(a);
    const cy = 54 + 27 * Math.sin(a);
    petals.push(<circle key={i} cx={cx.toFixed(1)} cy={cy.toFixed(1)} r={17} fill={color} />);
  }
  const eyes =
    mood === "sleep" ? (
      <path
        d="M36 56q4 3 8 0M56 56q4 3 8 0"
        stroke="#1C1C1C"
        strokeWidth={3}
        fill="none"
        strokeLinecap="round"
      />
    ) : (
      <>
        <circle cx={40} cy={54} r={4.4} fill="#1C1C1C" />
        <circle cx={60} cy={54} r={4.4} fill="#1C1C1C" />
      </>
    );
  const mouth =
    mood === "wow" ? (
      <circle cx={50} cy={66} r={4} fill="#1C1C1C" />
    ) : (
      <path
        d="M45 65q5 4.5 10 0"
        stroke="#1C1C1C"
        strokeWidth={3}
        fill="none"
        strokeLinecap="round"
      />
    );
  return (
    <span
      className={`mascot-avatar mascot-avatar--${state}`}
      data-state={state}
      role={label ? "img" : undefined}
      aria-label={label}
    >
      <svg
        className="mav"
        width={size}
        height={size}
        viewBox="0 0 100 100"
        aria-hidden={label ? undefined : "true"}
      >
        <path
          d="M50 16C47 8 40 4 32 5c1 8 7 13 14 12M50 16c3-8 10-12 18-11-1 8-7 13-14 12"
          fill="#4CAF50"
        />
        {petals}
        <ellipse cx={50} cy={55} rx={26} ry={23} fill="#FFF7F3" />
        {eyes}
        {mouth}
        <circle cx={33} cy={63} r={4} fill="#F8C8CE" opacity={0.8} />
        <circle cx={67} cy={63} r={4} fill="#F8C8CE" opacity={0.8} />
      </svg>
    </span>
  );
}

/** The six agents of the Space, same names, roles and colors. */
export const SPACE_AGENTS = {
  puspa: { name: "Puspa", role: "Asisten Utama", color: "#F0566B" },
  pulse: { name: "Pulse", role: "Analisis Data", color: "#F59E4C" },
  forge: { name: "Forge", role: "Eksekusi Tugas", color: "#4C8BF5" },
  scout: { name: "Scout", role: "Riset", color: "#57B25E" },
  atlas: { name: "Atlas", role: "Perencana", color: "#9B7ED9" },
  roxanne: { name: "Roxanne", role: "Asisten Finansial", color: "#3FA7C4" },
} as const;

export type SpaceAgentKey = keyof typeof SPACE_AGENTS;

export function AgentMascot({
  agent,
  size = 56,
  state = "idle",
}: {
  agent: SpaceAgentKey;
  size?: number;
  state?: AgentAvatarState;
}) {
  const info = SPACE_AGENTS[agent];
  return <Mascot color={info.color} size={size} state={state} label={info.name} />;
}
