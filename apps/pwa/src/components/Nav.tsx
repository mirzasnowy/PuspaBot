import { SCREENS, type ScreenName } from "../api/bootstrap";
import { StopAllButton } from "./StopAllButton";

export function Nav({ current }: { current: ScreenName }) {
  return (
    <header>
      <strong>Puspa</strong>
      {SCREENS.map((s) => (
        <a key={s.name} href={s.path} className={current === s.name ? "active" : undefined}>
          {s.label}
        </a>
      ))}
      <StopAllButton />
    </header>
  );
}
