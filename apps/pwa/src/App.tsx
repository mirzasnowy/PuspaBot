import type { ReactNode } from "react";
import type { Bootstrap } from "./api/client";
import type { ScreenName } from "./api/bootstrap";
import { ChatScreen } from "./screens/ChatScreen";
import { TasksScreen } from "./screens/TasksScreen";
import { ModulesScreen } from "./screens/ModulesScreen";
import { ConnectorsScreen } from "./screens/ConnectorsScreen";
import { MemoryScreen } from "./screens/MemoryScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { OfficeScreen } from "./screens/OfficeScreen";
import { InsightsScreen } from "./screens/InsightsScreen";
import { OnboardingScreen } from "./screens/OnboardingScreen";
import { MockupScreen } from "./screens/MockupScreen";
import { RoomsScreen } from "./screens/RoomsScreen";
import { LoginScreen } from "./screens/LoginScreen";
import { HintBanner } from "./components/HintBanner";

export function App({ bootstrap, screen }: { bootstrap: Bootstrap; screen: ScreenName }) {
  if (screen === "login") {
    return <LoginScreen />;
  }
  let content: ReactNode;
  switch (screen) {
    case "chat":
      content = <ChatScreen bootstrap={bootstrap} />;
      break;
    case "tasks":
      content = <TasksScreen bootstrap={bootstrap} />;
      break;
    case "modules":
      content = <ModulesScreen />;
      break;
    case "connectors":
      content = <ConnectorsScreen bootstrap={bootstrap} />;
      break;
    case "memory":
      content = <MemoryScreen bootstrap={bootstrap} />;
      break;
    case "settings":
      content = <SettingsScreen />;
      break;
    case "office":
      content = <OfficeScreen bootstrap={bootstrap} />;
      break;
    case "insights":
      content = <InsightsScreen />;
      break;
    case "onboarding":
      content = <OnboardingScreen />;
      break;
    case "mockup":
      content = <MockupScreen />;
      break;
    case "rooms":
      content = <RoomsScreen />;
      break;
  }
  return (
    <>
      <HintBanner key={screen} screen={screen} bootstrap={bootstrap} />
      {content}
    </>
  );
}
