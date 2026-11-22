import { SCREENS, type ScreenName } from "../api/bootstrap";
import { StopAllButton } from "./StopAllButton";
import { useLanguage } from "../state/language";

export function Nav({
  current,
  botName,
}: {
  current: ScreenName;
  /** The bot's own name (V2-27): the required agent from the bootstrap, "Puspa" when unknown. */
  botName?: string;
}) {
  const { t } = useLanguage();
  return (
    <header>
      <strong>{botName?.trim() || "Puspa"}</strong>
      {SCREENS.map((s) => (
        <a key={s.name} href={s.path} className={current === s.name ? "active" : undefined}>
          {t(`ui.nav.${s.name}`, s.label)}
        </a>
      ))}
      <StopAllButton />
    </header>
  );
}
