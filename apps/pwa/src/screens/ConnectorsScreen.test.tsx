import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConnectorsScreen } from "./ConnectorsScreen";
import { createAgent } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    createAgent: vi.fn(),
    deleteAgent: vi.fn(),
    setConnector: vi.fn(),
  };
});

const mockedCreate = vi.mocked(createAgent);

const BOOTSTRAP = {
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
      agent_id: "agen-rose",
      name: "Riset",
      role: "riset",
      purpose: "Riset harga",
      required: false,
      avatar: "rose",
    },
  ],
} as never;

describe("ConnectorsScreen agent icon choice (Nero, 24 Nov)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("the Tambah Agent form offers the default logo plus four flowers", () => {
    render(<ConnectorsScreen bootstrap={BOOTSTRAP} />);
    for (const label of [
      "Puspa (default)",
      "Tulip",
      "Bunga matahari",
      "Mawar",
      "Sakura",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
    expect(screen.getByText("Ikon agent")).toBeTruthy();
  });

  it("picking a flower sends it as the new agent's avatar, and the list shows it", async () => {
    mockedCreate.mockResolvedValue({
      agent_id: "agen-baru",
      name: "Nova",
      role: "",
      purpose: "",
      required: false,
      avatar: "rose",
    } as never);
    const user = userEvent.setup();
    render(<ConnectorsScreen bootstrap={BOOTSTRAP} />);
    await user.click(screen.getByRole("button", { name: "Mawar" }));
    await user.type(screen.getByLabelText("Nama agen"), "Nova");
    await user.click(screen.getByRole("button", { name: "Buat agen" }));
    await waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith({
        name: "Nova",
        role: "",
        purpose: "",
        avatar: "rose",
      }),
    );
    // The new agent appears with the rose icon; the existing
    // rose agent already shows it too.
    const roseImgs = document.querySelectorAll('img[src*="agent-flower-rose-nobg"]');
    expect(roseImgs.length).toBeGreaterThanOrEqual(2);
  });

  it("without a pick the new agent keeps the default icon", async () => {
    mockedCreate.mockResolvedValue({
      agent_id: "agen-baru",
      name: "Nova",
      role: "",
      purpose: "",
      required: false,
      avatar: "default",
    } as never);
    const user = userEvent.setup();
    render(<ConnectorsScreen bootstrap={BOOTSTRAP} />);
    await user.type(screen.getByLabelText("Nama agen"), "Nova");
    await user.click(screen.getByRole("button", { name: "Buat agen" }));
    await waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith({
        name: "Nova",
        role: "",
        purpose: "",
        avatar: "default",
      }),
    );
  });
});
