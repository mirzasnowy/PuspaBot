import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HubScreen } from "./HubScreen";
import type { HubPreview, HubSkill } from "../api/client";

function hubSkills(): HubSkill[] {
  return [
    {
      id: "find-skills",
      name: "Find Skills",
      description: "Discovers and installs agent skills.",
      version: "1.0.0",
      origin: "builtin",
      status: "builtin",
      source: "puspa-builtin",
      license: "MIT",
      price_credits: 0,
      installed: true,
    },
    {
      id: "weather-watcher",
      name: "Weather Watcher",
      description: "Watches the weather for you.",
      version: "1.0.0",
      origin: "library",
      status: "enabled",
      source: "github:acme/weather-watcher",
      license: "MIT",
      price_credits: 0,
      installed: true,
    },
  ];
}

function preview(): HubPreview {
  return {
    slug: "weather-watcher",
    name: "Weather Watcher",
    description: "Watches the weather for you.",
    license: "MIT",
    sha256: "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
    source: "github:acme/weather-watcher",
  };
}

function stubFetch() {
  const calls: string[] = [];
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const path = String(url);
    calls.push(`${init?.method ?? "GET"} ${path}`);
    let body: unknown;
    let ok = true;
    if (path.startsWith("/api/hub/skills")) {
      const q = new URL(path, "http://localhost").searchParams.get("q") ?? "";
      body = {
        skills: hubSkills().filter((s) =>
          s.name.toLowerCase().includes(q.toLowerCase()),
        ),
        query: q,
      };
    } else if (path.startsWith("/api/hub/preview")) {
      body = preview();
    } else if (path === "/api/hub/install") {
      body = {
        ok: true,
        installed: {
          skill_id: "weather-watcher",
          version: "1.0.0",
          status: "enabled",
          sha256: preview().sha256,
          source: "github:acme/weather-watcher",
        },
      };
    } else {
      ok = false;
      body = { error: "not found" };
    }
    return Promise.resolve({
      ok,
      status: ok ? 200 : 404,
      json: async () => body,
    });
  });
  return { fetchMock, calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HubScreen", () => {
  it("lists the local skills: name, version, origin, licence, and source", async () => {
    const { fetchMock } = stubFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<HubScreen />);
    expect(await screen.findByText("Find Skills")).toBeTruthy();
    expect(screen.getByText("Weather Watcher")).toBeTruthy();
    expect(screen.getAllByText(/sudah terpasang/)).toHaveLength(2);
  });

  it("says plainly when no skill matches the search", async () => {
    const { fetchMock } = stubFetch();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<HubScreen />);
    await screen.findByText("Find Skills");
    await user.type(screen.getByLabelText("Cari skill"), "tidak-ada");
    await user.click(screen.getByRole("button", { name: "Cari" }));
    expect(await screen.findByText("Belum ada skill yang cocok.")).toBeTruthy();
  });

  it("previews a source, then installs it only after confirm", async () => {
    const { fetchMock, calls } = stubFetch();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<HubScreen />);
    await screen.findByText("Find Skills");
    await user.type(screen.getByLabelText("Sumber skill"), "acme/weather-watcher");
    await user.click(screen.getByRole("button", { name: "Lihat dulu" }));
    const matches = await screen.findAllByText("Weather Watcher");
    expect(matches).toHaveLength(2);
    expect(screen.getByText(/lisensi MIT/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Pasang skill ini" }));
    expect(
      await screen.findByText("Terpasang: weather-watcher v1.0.0."),
    ).toBeTruthy();
    expect(calls).toContain(
      "GET /api/hub/preview?source=acme%2Fweather-watcher",
    );
    expect(calls).toContain("POST /api/hub/install");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/hub/install",
      expect.objectContaining({
        body: JSON.stringify({
          source: "acme/weather-watcher",
          expected_sha256: preview().sha256,
        }),
      }),
    );
  });

  it("reports a failed preview honestly instead of installing", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const path = String(url);
      if (path.startsWith("/api/hub/skills")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ skills: hubSkills(), query: "" }),
        });
      }
      return Promise.resolve({
        ok: false,
        status: 502,
        json: async () => ({ error: "SKILL.md not found in acme/nope" }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<HubScreen />);
    await screen.findByText("Find Skills");
    await user.type(screen.getByLabelText("Sumber skill"), "acme/nope");
    await user.click(screen.getByRole("button", { name: "Lihat dulu" }));
    expect(
      await screen.findByText(/Gagal mengambil pratinjau: SKILL.md not found in acme\/nope/),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pasang skill ini" })).toBeNull();
  });
});
