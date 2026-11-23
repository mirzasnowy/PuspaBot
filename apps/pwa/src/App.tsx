import type { ReactNode } from "react";
import type { Bootstrap } from "./api/client";
import type { ScreenName } from "./api/bootstrap";
import { SAYA_GROUPS } from "./api/space";
import { ChatScreen } from "./screens/ChatScreen";
import { TasksScreen } from "./screens/TasksScreen";
import { MemoryScreen } from "./screens/MemoryScreen";
import { ArtifactsScreen } from "./screens/ArtifactsScreen";
import { HubScreen } from "./screens/HubScreen";
import { WikiScreen } from "./screens/WikiScreen";
import { OnboardingScreen } from "./screens/OnboardingScreen";
import { MockupScreen } from "./screens/MockupScreen";
import { DiagnosticsScreen } from "./screens/DiagnosticsScreen";
import { LoginScreen } from "./screens/LoginScreen";
import { HintBanner } from "./components/HintBanner";
import { ConversationsScreen, RoomChatScreen } from "./screens/space/Conversations";
import {
  ApprovalQueueScreen,
  DelegationScreen,
  MissionsScreen,
  ProgressScreen,
} from "./screens/space/TasksExtra";
import {
  DataControlScreen,
  EditKnowledgeScreen,
  ExportScreen,
  PersonaScreen,
} from "./screens/space/MemoryExtra";
import {
  ApprovalScreen,
  BillDetailScreen,
  FinanceScreen,
  PpobScreen,
  PriceCheckScreen,
  ReceiptScreen,
} from "./screens/space/Billing";
import {
  GroupScreen,
  PlansScreen,
  ProfileScreen,
  ShareScreen,
  TrialScreen,
} from "./screens/space/Profile";
import {
  GalleryScreen,
  MarketplaceScreen,
  ModuleBuilderScreen,
  StudioScreen,
} from "./screens/space/Studio";
import {
  AgentsScreen,
  ConnectorsSpaceScreen,
  GroupResearchScreen,
  InsightsSpaceScreen,
  LiveSpaceScreen,
  MorningSpaceScreen,
  ProviderScreen,
  ReplaySpaceScreen,
  RoomsSpaceScreen,
  ThemeScreen,
  VoiceScreen,
} from "./screens/space/System";

/**
 * App router (Space port): every one of the 46 ported Space
 * screens plus the legacy screens at their old paths (direct
 * URL only, not in the four-tab nav, not counted).
 *
 * Bill details (PLN / PDAM / Pulsa / Paket Data) show a real
 * pending approval when one matches; the fallback rows carry no
 * invented customer numbers — the Space's demo IDs are not
 * ported as data.
 */
const BILL_CONFIGS = {
  pln: {
    service: "PLN",
    icon: "bolt",
    cls: "peach",
    fields: [
      ["ID Pelanggan", "Belum tersimpan — minta Puspa cek tagihan"],
      ["Status", "Belum ada tagihan menunggu"],
    ] as [string, string][],
    warning: "Pastikan kamu memeriksa informasi sebelum membayar.",
    payLabel: "Ya, Bayar",
  },
  pdam: {
    service: "PDAM",
    icon: "drop",
    cls: "",
    fields: [
      ["ID Pelanggan", "Belum tersimpan — minta Puspa cek tagihan"],
      ["Status", "Belum ada tagihan menunggu"],
    ] as [string, string][],
    warning: "Pastikan kamu memeriksa informasi sebelum membayar.",
    payLabel: "Ya, Bayar",
  },
  pulsa: {
    service: "Pulsa",
    icon: "phone",
    cls: "green",
    fields: [
      ["Nomor", "Belum tersimpan — minta Puspa cek pulsa"],
      ["Status", "Belum ada pembelian menunggu"],
    ] as [string, string][],
    warning: "Pastikan nomornya benar — pulsa yang sudah terkirim tidak bisa ditarik lagi.",
    payLabel: "Ya, Beli",
  },
  paketdata: {
    service: "Paket Data",
    icon: "globe",
    cls: "peach",
    fields: [
      ["Nomor", "Belum tersimpan — minta Puspa cek paket"],
      ["Paket", "Pilih paket saat Puspa mengecek"],
    ] as [string, string][],
    warning: "Pastikan nomornya benar sebelum membeli paket.",
    payLabel: "Ya, Beli",
  },
} as const;

export function App({ bootstrap, screen }: { bootstrap: Bootstrap; screen: ScreenName }) {
  if (screen === "login") {
    return <LoginScreen />;
  }
  let content: ReactNode;
  switch (screen) {
    // -- Chat tab --
    case "percakapan":
      content = <ConversationsScreen />;
      break;
    case "chat":
      content = <ChatScreen bootstrap={bootstrap} />;
      break;
    case "ruangchat":
      content = <RoomChatScreen />;
      break;
    // -- Tugas tab --
    case "tugas":
      content = <TasksScreen bootstrap={bootstrap} />;
      break;
    case "misi":
      content = <MissionsScreen />;
      break;
    case "delegasi":
      content = <DelegationScreen />;
      break;
    case "progres":
      content = <ProgressScreen />;
      break;
    case "antrean":
      content = <ApprovalQueueScreen bootstrap={bootstrap} />;
      break;
    // -- Memori tab --
    case "memori":
      content = <MemoryScreen bootstrap={bootstrap} />;
      break;
    case "editpengetahuan":
      content = <EditKnowledgeScreen />;
      break;
    case "persona":
      content = <PersonaScreen />;
      break;
    case "ekspor":
      content = <ExportScreen />;
      break;
    case "datakontrol":
      content = <DataControlScreen />;
      break;
    // -- Saya tab: money --
    case "cekharga":
      content = <PriceCheckScreen />;
      break;
    case "ppob":
      content = <PpobScreen />;
      break;
    case "pln":
    case "pdam":
    case "pulsa":
    case "paketdata":
      content = <BillDetailScreen bootstrap={bootstrap} {...BILL_CONFIGS[screen]} />;
      break;
    case "persetujuan":
      content = <ApprovalScreen bootstrap={bootstrap} />;
      break;
    case "struk":
      content = <ReceiptScreen />;
      break;
    case "keuangan":
      content = <FinanceScreen />;
      break;
    case "paket":
      content = <PlansScreen />;
      break;
    case "trial":
      content = <TrialScreen />;
      break;
    // -- Saya tab: studio --
    case "marketplace":
      content = <MarketplaceScreen />;
      break;
    case "galeri":
      content = <GalleryScreen />;
      break;
    case "studio":
      content = <StudioScreen />;
      break;
    case "modul":
      content = <ModuleBuilderScreen />;
      break;
    case "agen":
      content = <AgentsScreen />;
      break;
    // -- Saya tab: rooms --
    case "ruang":
      content = <RoomsSpaceScreen />;
      break;
    case "risetgrup":
      content = <GroupResearchScreen />;
      break;
    case "pagi":
      content = <MorningSpaceScreen />;
      break;
    // -- Saya tab: account --
    case "konektor":
      content = <ConnectorsSpaceScreen bootstrap={bootstrap} />;
      break;
    case "provider":
      content = <ProviderScreen />;
      break;
    case "tema":
      content = <ThemeScreen />;
      break;
    case "suara":
      content = <VoiceScreen />;
      break;
    case "insights":
      content = <InsightsSpaceScreen />;
      break;
    case "share":
      content = <ShareScreen />;
      break;
    case "live":
      content = <LiveSpaceScreen bootstrap={bootstrap} />;
      break;
    case "replay":
      content = <ReplaySpaceScreen bootstrap={bootstrap} />;
      break;
    case "saya":
      content = <ProfileScreen />;
      break;
    case "grupkeuangan":
    case "grupstudio":
    case "grupruang":
    case "grupakun": {
      const group = SAYA_GROUPS.find((g) => g.id === screen);
      content = group ? <GroupScreen group={group} /> : <ProfileScreen />;
      break;
    }
    case "onboarding":
      content = <OnboardingScreen />;
      break;
    // -- Legacy screens (old paths, direct URL only) --
    case "artifacts":
      content = <ArtifactsScreen bootstrap={bootstrap} />;
      break;
    case "hub":
      content = <HubScreen />;
      break;
    case "wiki":
      content = <WikiScreen />;
      break;
    case "mockup":
      content = <MockupScreen />;
      break;
    case "diagnostics":
      content = <DiagnosticsScreen />;
      break;
    default:
      content = <ConversationsScreen />;
  }
  return (
    <>
      <HintBanner key={screen} screen={screen} bootstrap={bootstrap} />
      {content}
    </>
  );
}
