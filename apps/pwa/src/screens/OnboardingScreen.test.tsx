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

describe("OnboardingScreen", () => {
  it("opens step 1 with the three speaking styles", async () => {
    const { mock } = mockOnboardingFetch();
    vi.stubGlobal("fetch", mock);
    try {
      render(<OnboardingScreen />);
      expect(await screen.findByText("Langkah 1: nama dan gaya bicara")).toBeTruthy();
      expect(screen.getByText("Santai")).toBeTruthy();
      expect(screen.getByText("Rapi")).toBeTruthy();
      expect(screen.getByText("Campur kode")).toBeTruthy();
      expect(screen.getByText(/Langkah 1 dari 4/)).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("refuses an empty name without calling the server", async () => {
    const { mock, calls } = mockOnboardingFetch();
    vi.stubGlobal("fetch", mock);
    try {
      render(<OnboardingScreen />);
      await screen.findByText("Langkah 1: nama dan gaya bicara");
      const callsBefore = calls.length;
      const form = screen.getByRole("form", { name: /Langkah 1/ });
      fireEvent.submit(form);
      expect(await screen.findByText("Nama tidak boleh kosong.")).toBeTruthy();
      expect(calls.length).toBe(callsBefore);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("walks all four steps and ends on the done state", async () => {
    const { mock, calls } = mockOnboardingFetch();
    vi.stubGlobal("fetch", mock);
    try {
      render(<OnboardingScreen />);
      const user = userEvent.setup();
      await screen.findByText("Langkah 1: nama dan gaya bicara");

      await user.type(screen.getByLabelText(/Nama kamu/), "Nero");
      await user.click(screen.getByRole("radio", { name: /Rapi/ }));
      await user.click(screen.getByRole("button", { name: "Lanjut ke langkah 2" }));
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

      const step1Call = calls.find((call) => call.url === "/api/onboarding/step1");
      expect(step1Call?.body).toEqual({ name: "Nero", style: "rapi" });
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
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("jaringan putus")));
    try {
      render(<OnboardingScreen />);
      expect(await screen.findByText(/Gagal memuat onboarding/)).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
