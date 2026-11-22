import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { currentScreen, readBootstrap } from "./api/bootstrap";
import { fetchOnboardingStatus } from "./state/onboarding";
import { requiredAgentName } from "./state/names";
import { loadCatalog } from "./state/language";
import { Nav } from "./components/Nav";
import { App } from "./App";

const bootstrap = readBootstrap();
const screen = currentScreen(bootstrap);
// COW-18: load the UI-string catalog for the user's stored
// language in the background; screens render their hardcoded
// Indonesian copy until it arrives, then re-render translated.
void loadCatalog();
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Nav current={screen} botName={requiredAgentName(bootstrap.agents ?? [])} />
      <main>
        <App bootstrap={bootstrap} screen={screen} />
      </main>
    </StrictMode>,
  );
}

// First run: a logged-in user who never started onboarding is
// sent to /onboarding once, from any screen except onboarding
// itself (and login, where no session exists yet). The check
// runs after the first paint and never blocks it: a failed
// fetch, or a 401 for a user who is not logged in, simply
// leaves the user on the screen they opened.
if (screen !== "onboarding" && screen !== "login") {
  fetchOnboardingStatus()
    .then((status) => {
      if (!status.started) {
        window.location.assign("/onboarding");
      }
    })
    .catch(() => {
      // Fall through to the normal screen.
    });
}
