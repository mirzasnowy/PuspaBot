import type { Bootstrap } from "./api/client";
import type { ScreenName } from "./api/bootstrap";
import { ChatScreen } from "./screens/ChatScreen";
import { TasksScreen } from "./screens/TasksScreen";
import { ModulesScreen } from "./screens/ModulesScreen";
import { ConnectorsScreen } from "./screens/ConnectorsScreen";
import { MemoryScreen } from "./screens/MemoryScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { OfficeScreen } from "./screens/OfficeScreen";
import { LoginScreen } from "./screens/LoginScreen";

export function App({ bootstrap, screen }: { bootstrap: Bootstrap; screen: ScreenName }) {
  switch (screen) {
    case "chat":
      return <ChatScreen bootstrap={bootstrap} />;
    case "tasks":
      return <TasksScreen bootstrap={bootstrap} />;
    case "modules":
      return <ModulesScreen />;
    case "connectors":
      return <ConnectorsScreen bootstrap={bootstrap} />;
    case "memory":
      return <MemoryScreen bootstrap={bootstrap} />;
    case "settings":
      return <SettingsScreen />;
    case "office":
      return <OfficeScreen bootstrap={bootstrap} />;
    case "login":
      return <LoginScreen />;
  }
}
