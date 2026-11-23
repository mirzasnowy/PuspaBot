import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentsScreen } from "./System";
import { fetchAgents } from "../../api/client";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    fetchAgents: vi.fn(),
  };
});

const mockedFetchAgents = vi.mocked(fetchAgents);

class FakeEventSource {
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(_url: string) {}
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AgentsScreen (Tim Agen) custom agents (Nero, 24 Nov)", () => {
  it("lists custom agents below the six built-ins, with their picked flower icon", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    mockedFetchAgents.mockResolvedValue({
      agents: [
        {
          agent_id: "puspa-user-1",
          name: "Puspa",
          role: "agen utama",
          purpose: "Agen wajib Puspa",
          required: true,
          avatar: "default",
        },
        {
          agent_id: "agen-nova",
          name: "Nova",
          role: "riset",
          purpose: "Riset harga",
          required: false,
          avatar: "rose",
        },
      ],
    } as never);
    render(<AgentsScreen />);
    expect(await screen.findByText("Tim Agen")).toBeTruthy();
    expect(await screen.findByText("Nova")).toBeTruthy();
    expect(screen.getByText("Agen buatanmu")).toBeTruthy();
    expect(
      document.querySelector('img[src*="agent-flower-rose-nobg"]'),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Kelola" }).getAttribute("href")).toBe(
      "/konektor",
    );
  });

  it("shows no custom section when the user has only Puspa", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    mockedFetchAgents.mockResolvedValue({
      agents: [
        {
          agent_id: "puspa-user-1",
          name: "Puspa",
          role: "agen utama",
          purpose: "Agen wajib Puspa",
          required: true,
          avatar: "default",
        },
      ],
    } as never);
    render(<AgentsScreen />);
    expect(await screen.findByText("Tim Agen")).toBeTruthy();
    expect(screen.queryByText("Agen buatanmu")).toBeNull();
  });
});
