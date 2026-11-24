/**
 * Space screen registry: the port of the Space puspabot-app
 * (live v11) into the repo PWA.
 *
 * FOUR top-level tabs only (Nero, 23 Nov): Chat, Tugas, Memori,
 * Saya. The Space's Beranda screen is NOT ported — its design
 * is saved under `apps/pwa/saved-designs/` with the other
 * unbuilt designs, and nothing in the app links to it.
 *
 * Every other screen lives inside exactly one tab (TAB_OF), so
 * no feature is listed in two places. The ten features the
 * audit hid (Patungan, Preloved, Try-On & Video, QRIS,
 * Anti-Fraud, Notulen, Co-Drafting, Impor Memori, Panel
 * Operator, PPOB tiles Internet-TV / BPJS / E-Wallet / Voucher
 * Game) appear nowhere here: they are not screens, not tabs,
 * not rows — only saved designs.
 *
 * Two Space screens are NOT ported, on purpose: Beranda (Nero
 * removed the tab, 23 Nov) and Autopilot Tagihan (no backend
 * API exists for it, so a toggle would only pretend). Both
 * designs live in `apps/pwa/saved-designs/`.
 */
export const SPACE_SCREEN_IDS = [
  "onboarding",
  "percakapan",
  "chat",
  "ruangchat",
  "tugas",
  "misi",
  "delegasi",
  "memori",
  "editpengetahuan",
  "persona",
  "ekspor",
  "datakontrol",
  "cekharga",
  "ppob",
  "pln",
  "pdam",
  "pulsa",
  "paketdata",
  "persetujuan",
  "struk",
  "keuangan",
  "paket",
  "marketplace",
  "galeri",
  "konektor",
  "provider",
  "studio",
  "modul",
  "ruang",
  "risetgrup",
  "pagi",
  "live",
  "replay",
  "antrean",
  "progres",
  "agen",
  "suara",
  "tema",
  "saya",
  "grupkeuangan",
  "grupstudio",
  "grupruang",
  "grupakun",
  "insights",
  "trial",
  "share",
] as const;

export type SpaceScreenId = (typeof SPACE_SCREEN_IDS)[number];

/** Ported Space screens: 46 of the Space's 48 (Beranda + Autopilot excluded). */
export const SPACE_SCREEN_COUNT = SPACE_SCREEN_IDS.length;

export const SPACE_TITLES: Record<SpaceScreenId, string> = {
  onboarding: "Selamat Datang",
  percakapan: "Percakapan",
  chat: "Puspa",
  ruangchat: "Ruang Bersama",
  tugas: "Tugas",
  misi: "Misi",
  delegasi: "Tugas Delegasi",
  memori: "Memori",
  editpengetahuan: "Edit Pengetahuan",
  persona: "Persona",
  ekspor: "Ekspor Memori",
  datakontrol: "Kontrol Data",
  cekharga: "Cek Harga",
  ppob: "Bayar Tagihan",
  pln: "Pembayaran PLN",
  pdam: "Pembayaran PDAM",
  pulsa: "Beli Pulsa",
  paketdata: "Paket Data",
  persetujuan: "Persetujuan Diperlukan",
  struk: "Foto Struk",
  keuangan: "Keuangan",
  paket: "Paket & Harga",
  marketplace: "Marketplace",
  galeri: "Galeri Karya",
  konektor: "Konektor",
  provider: "Provider & Model",
  studio: "Kanvas Desain",
  modul: "Bangun Modul",
  ruang: "Ruang Skripsi",
  risetgrup: "Riset & Belanja Grup",
  pagi: "Ringkasan Pagi",
  live: "Live View",
  replay: "Replay",
  antrean: "Antrean Persetujuan",
  progres: "Progres Tugas",
  agen: "Tim Agen",
  suara: "Suara",
  tema: "Tema & Otonomi",
  saya: "Saya",
  grupkeuangan: "Keuangan",
  grupstudio: "Agen & Studio",
  grupruang: "Ruang & Tim",
  grupakun: "Akun & Sistem",
  insights: "Insights",
  trial: "Trial Pro",
  share: "Bagikan ke Puspa",
};

export type SpaceTabId = "percakapan" | "tugas" | "memori" | "saya";

/** Exactly four top-level tabs — identical in the sidebar and the bottom bar. */
export const MAIN_TABS: { id: SpaceTabId; label: string; icon: string }[] = [
  { id: "percakapan", label: "Chat", icon: "chat" },
  { id: "tugas", label: "Tugas", icon: "tasks" },
  { id: "memori", label: "Memori", icon: "memory" },
  { id: "saya", label: "Saya", icon: "profile" },
];

/** Which tab each screen belongs to (the Space's TAB_OF, minus Beranda). */
export const TAB_OF: Partial<Record<SpaceScreenId, SpaceTabId>> = {
  chat: "percakapan",
  ruangchat: "percakapan",
  misi: "tugas",
  delegasi: "tugas",
  progres: "tugas",
  live: "tugas",
  replay: "tugas",
  antrean: "tugas",
  editpengetahuan: "memori",
  persona: "memori",
  ekspor: "memori",
  datakontrol: "memori",
  grupkeuangan: "saya",
  grupstudio: "saya",
  grupruang: "saya",
  grupakun: "saya",
  keuangan: "saya",
  ppob: "saya",
  pln: "saya",
  pdam: "saya",
  pulsa: "saya",
  paketdata: "saya",
  persetujuan: "saya",
  struk: "saya",
  cekharga: "saya",
  studio: "saya",
  galeri: "saya",
  marketplace: "saya",
  modul: "saya",
  ruang: "saya",
  risetgrup: "saya",
  agen: "saya",
  pagi: "saya",
  paket: "saya",
  trial: "saya",
  konektor: "saya",
  provider: "saya",
  tema: "saya",
  suara: "saya",
  insights: "saya",
  share: "saya",
};

export function tabOf(screen: SpaceScreenId): SpaceTabId | null {
  if (screen === "percakapan" || screen === "tugas" || screen === "memori" || screen === "saya") {
    return screen;
  }
  return TAB_OF[screen] ?? null;
}

/** The four Saya groups, same rows as the Space. */
export const SAYA_GROUPS: {
  id: string;
  icon: string;
  cls: string;
  label: string;
  rows: [string, string, string, string][];
}[] = [
  {
    id: "grupkeuangan",
    icon: "wallet",
    cls: "",
    label: "Keuangan",
    rows: [
      ["wallet", "", "Keuangan", "keuangan"],
      ["bolt", "peach", "Bayar Tagihan", "ppob"],
      ["search", "", "Cek Harga", "cekharga"],
    ],
  },
  {
    id: "grupstudio",
    icon: "sparkle",
    cls: "peach",
    label: "Agen & Studio",
    rows: [
      ["sparkle", "peach", "Tim Agen", "agen"],
      ["edit", "", "Kanvas Desain", "studio"],
      ["image", "", "Galeri Karya", "galeri"],
      ["box", "green", "Marketplace Skill", "marketplace"],
    ],
  },
  {
    id: "grupruang",
    icon: "users",
    cls: "green",
    label: "Ruang & Tim",
    rows: [
      ["users", "", "Ruang Bersama", "ruang"],
      ["sun", "peach", "Ringkasan Pagi", "pagi"],
    ],
  },
  {
    id: "grupakun",
    icon: "settings",
    cls: "",
    label: "Akun & Sistem",
    rows: [
      ["link", "", "Konektor", "konektor"],
      ["sliders", "", "Provider & Model", "provider"],
      ["sun", "peach", "Tema & Otonomi", "tema"],
      ["mic", "blue", "Suara", "suara"],
      ["chart", "", "Insights", "insights"],
      ["share", "green", "Bagikan ke Puspa", "share"],
    ],
  },
];

/**
 * Features that must NOT appear in the UI: the audit hid them
 * because they do not exist in the product yet. Their designs
 * live only in `apps/pwa/saved-designs/`, never in navigation.
 * Kept as a list so tests can assert none of these words render
 * in the tab bar, the Saya groups, or the PPOB hub.
 */
export const HIDDEN_FEATURES = [
  "Patungan",
  "Preloved",
  "Try-On",
  "QRIS",
  "Anti-Fraud",
  "Notulen",
  "Co-Drafting",
  "Impor Memori",
  "Panel Operator",
  "Internet-TV",
  "BPJS",
  "E-Wallet",
  "Voucher Game",
] as const;
