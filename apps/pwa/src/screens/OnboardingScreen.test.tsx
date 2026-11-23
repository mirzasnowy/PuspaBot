import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OnboardingScreen } from "./OnboardingScreen";

/** Fetch mock that answers each onboarding route from a table. */
function mockOnboardingFetch() {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url, method, body });
    const json = (payload: unknown) => ({
      ok: true,
      status: 200,
      json: async () => payload,
    });
    if (url === "/api/onboarding" && method === "GET") {
      return json({ started: false, current_step: 0 });
    }
    if (url === "/api/onboarding/start") {
      return json({ started: true, current_step: 0 });
    }
    if (url === "/api/onboarding/agent-file") {
      return json({
        slug: "puspa",
        name: body.agent_name,
        soul: `# ${body.agent_name}\nGaya bicara: ${body.style}\nTujuan: ${body.purposes.join(", ")}`,
        identity: `Name: ${body.agent_name}\n`,
        user: `Name: ${body.user_name}\nGaya: ${body.style}`,
      });
    }
    if (url === "/api/onboarding/step1") {
      return json({
        started: true,
        current_step: 1,
        name: body.name,
        style: body.style,
        style_fact_id: "fact-1",
      });
    }
    if (url === "/api/onboarding/step2") {
      return json({
        started: true,
        current_step: 2,
        test_notification: "terkirim=False; Web Push belum ada (Fase 2)",
        notification_sent: false,
      });
    }
    if (url === "/api/onboarding/step3") {
      return json({ started: true, current_step: 3, connector: body.connector });
    }
    if (url === "/api/onboarding/first-task") {
      return json({
        status: {
          started: true,
          current_step: 4,
          first_task: `${body.event_type}:${body.target}`,
        },
        task_id: "task-1",
        type: body.event_type,
        target: body.target,
        verified: false,
        reason: "Wedge bandingkan harga / autopilot tagihan dibangun Fase 3.",
        task_duration_seconds: 0.1,
        event_ids: [],
        offered_workflow_templates: [
          "Tagihan bulanan",
          "Pantau harga barang",
          "Rekap tagihan minggu ini",
        ],
      });
    }
    throw new Error(`unexpected route ${method} ${url}`);
  });
  return { mock, calls };
}

/** Walk welcome → purpose → personalize → data, landing on the agent phase. */
async function walkToAgentPhase(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("Selamat Datang");
  await user.click(screen.getByRole("button", { name: "Mulai Sekarang" }));
  await screen.findByText("Untuk apa Puspa dipakai?");
  await user.click(screen.getByRole("button", { name: /Bandingkan harga/ }));
  await user.click(screen.getByRole("button", { name: "Lanjut" }));
  // The stepper also shows the label, so match the heading only.
  await screen.findByRole("heading", { name: "Personalisasi" });
  await user.type(screen.getByLabelText(/Nama kamu/), "Nero");
  await user.click(screen.getByRole("radio", { name: /Rapi/ }));
  await user.click(screen.getByRole("button", { name: "Lanjut" }));
  await screen.findByText("Data apa saja yang dipakai?");
  await user.click(screen.getByLabelText(/Aku mengerti/));
  await user.click(screen.getByRole("button", { name: "Lanjut" }));
  await screen.findByText("Beri nama agentmu");
}

describe("OnboardingScreen", () => {
  it("opens the welcome phase first, not step 1", async () => {
    window.localStorage.clear();
    const { mock } = mockOnboardingFetch();
    vi.stubGlobal("fetch", mock);
    try {
      render(<OnboardingScreen />);
      expect(await screen.findByText("Selamat Datang")).toBeTruthy();
      expect(
        screen.getByRole("button", { name: "Mulai Sekarang" }),
      ).toBeTruthy();
      expect(screen.getByText("Tujuan")).toBeTruthy();
      expect(screen.getByText("Personalisasi")).toBeTruthy();
      expect(screen.getByText("Nama agent")).toBeTruthy();
      expect(screen.queryByText("Langkah 1: nama dan gaya bicara")).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("refuses an empty agent name without calling the server", async () => {
    window.localStorage.clear();
    const { mock, calls } = mockOnboardingFetch();
    vi.stubGlobal("fetch", mock);
    try {
      render(<OnboardingScreen />);
      const user = userEvent.setup();
      await walkToAgentPhase(user);
      const callsBefore = calls.length;
      const nameInput = screen.getByLabelText(/Nama agent/);
      await user.clear(nameInput);
      const submit = screen.getByRole("button", { name: "Buat File Agent" });
      expect(submit).toHaveProperty("disabled", true);
      fireEvent.click(submit);
      expect(
        calls.filter((call) => call.url === "/api/onboarding/agent-file"),
      ).toHaveLength(0);
      expect(calls.length).toBe(callsBefore);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("walks the whole flow and ends on the done state", async () => {
    window.localStorage.clear();
    const { mock, calls } = mockOnboardingFetch();
    vi.stubGlobal("fetch", mock);
    try {
      render(<OnboardingScreen />);
      const user = userEvent.setup();
      await walkToAgentPhase(user);

      // Agent name is pre-filled with "Puspa"; write the agent file.
      expect(
        (screen.getByLabelText(/Nama agent/) as HTMLInputElement).value,
      ).toBe("Puspa");
      await user.click(
        screen.getByRole("button", { name: "Buat File Agent" }),
      );
      expect(await screen.findByText("File agentmu sudah ditulis")).toBeTruthy();
      // The generated file shows the answers verbatim.
      expect(screen.getByText(/Gaya bicara: rapi/)).toBeTruthy();
      expect(screen.getByText(/Tujuan: price_compare/)).toBeTruthy();
      await user.click(screen.getByRole("tab", { name: "IDENTITY.md" }));
      expect(screen.getByText(/Name: Puspa/)).toBeTruthy();
      await user.click(screen.getByRole("tab", { name: "USER.md" }));
      expect(screen.getByText(/Name: Nero/)).toBeTruthy();

      const fileCall = calls.find(
        (call) => call.url === "/api/onboarding/agent-file",
      );
      expect(fileCall?.body).toEqual(
        expect.objectContaining({
          agent_name: "Puspa",
          user_name: "Nero",
          style: "rapi",
          purposes: ["price_compare"],
        }),
      );
      // Phase A records G28 step 1 with the same answers.
      const step1Call = calls.find((call) => call.url === "/api/onboarding/step1");
      expect(step1Call?.body).toEqual({ name: "Nero", style: "rapi" });

      // Phase B starts at step 2 — step 1 was already recorded.
      await user.click(
        screen.getByRole("button", { name: "Lanjut ke Notifikasi" }),
      );
      expect(await screen.findByText("Langkah 2: uji notifikasi")).toBeTruthy();

      await user.click(
        screen.getByRole("button", { name: "Izinkan notifikasi + uji kirim" }),
      );
      expect(await screen.findByText("Langkah 3: sambungkan konektor")).toBeTruthy();
      // Step 2 honestly reports the test as NOT sent, and the note
      // stays visible after moving on.
      expect(
        screen.getByText(/Uji notifikasi tercatat sebagai tidak terkirim/),
      ).toBeTruthy();

      await user.click(screen.getByRole("button", { name: "Lanjut ke langkah 4" }));
      expect(await screen.findByText("Langkah 4: tugas pertama terbimbing")).toBeTruthy();

      await user.type(screen.getByLabelText(/Barang yang dicari/), "Asus Vivobook 14");
      await user.click(screen.getByRole("button", { name: "Jalankan tugas pertama" }));
      expect(await screen.findByText("Onboarding selesai")).toBeTruthy();
      // The unverified first-task result stays visible on the done state.
      expect(
        screen.getByText("Tugas pertama selesai (belum terverifikasi)"),
      ).toBeTruthy();
      expect(screen.getByText(/Tagihan bulanan/)).toBeTruthy();

      const taskCall = calls.find((call) => call.url === "/api/onboarding/first-task");
      expect(taskCall?.body).toEqual({
        event_type: "compare_prices",
        target: "Asus Vivobook 14",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows a load error when the status fetch fails", async () => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("jaringan putus")));
    try {
      render(<OnboardingScreen />);
      expect(await screen.findByText(/Gagal memuat onboarding/)).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
