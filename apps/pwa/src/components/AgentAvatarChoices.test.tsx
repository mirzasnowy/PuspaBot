import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentAvatarChoices } from "./AgentAvatarChoices";
import {
  deleteAgentPhoto,
  fetchAgentAvatar,
  setBuiltinAvatar,
} from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchAgentAvatar: vi.fn(),
    setBuiltinAvatar: vi.fn(),
    deleteAgentPhoto: vi.fn(),
  };
});

const mockedFetch = vi.mocked(fetchAgentAvatar);
const mockedSetBuiltin = vi.mocked(setBuiltinAvatar);
const mockedDelete = vi.mocked(deleteAgentPhoto);

const NO_PHOTO = {
  ok: true,
  has_photo: false,
  photo_url: null,
  updated_at: null,
  mime_type: null,
  source: "default",
};

const SAKURA = {
  ok: true,
  has_photo: true,
  photo_url: "/api/agent/avatar/photo",
  updated_at: 1780000000,
  mime_type: "image/webp",
  source: "builtin:sakura",
};

describe("AgentAvatarChoices (Nero, 24 Nov — flowers only)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedFetch.mockResolvedValue(NO_PHOTO);
  });

  it("offers the default logo plus four flowers — never animals", async () => {
    render(<AgentAvatarChoices />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    for (const label of [
      "Puspa (default)",
      "Tulip",
      "Bunga matahari",
      "Mawar",
      "Sakura",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
    for (const animal of ["Robot", "Kucing", "Rubah", "Burung"]) {
      expect(screen.queryByRole("button", { name: animal })).toBeNull();
    }
    // Default is active while no photo is set.
    expect(
      screen.getByRole("button", { name: "Puspa (default)" }).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("picking a flower calls the builtin endpoint and moves the highlight", async () => {
    mockedSetBuiltin.mockResolvedValue(SAKURA);
    const user = userEvent.setup();
    render(<AgentAvatarChoices />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Sakura" }));
    await waitFor(() => expect(mockedSetBuiltin).toHaveBeenCalledWith("sakura"));
    expect(await screen.findByText("Avatar diganti.")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Sakura" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByRole("button", { name: "Puspa (default)" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("picking the default removes the photo", async () => {
    mockedFetch.mockResolvedValue(SAKURA);
    mockedDelete.mockResolvedValue({
      ok: true,
      has_photo: false,
    });
    const user = userEvent.setup();
    render(<AgentAvatarChoices />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Sakura" }).getAttribute("aria-pressed"),
      ).toBe("true"),
    );
    await user.click(screen.getByRole("button", { name: "Puspa (default)" }));
    await waitFor(() => expect(mockedDelete).toHaveBeenCalled());
    expect(await screen.findByText("Avatar kembali ke logo default Puspa.")).toBeTruthy();
  });

  it("controlled mode (Tambah Agent form, Nero 24 Nov) only reports the pick — it never touches the Puspa avatar", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<AgentAvatarChoices value="default" onSelect={onSelect} />);
    await user.click(screen.getByRole("button", { name: "Mawar" }));
    expect(onSelect).toHaveBeenCalledWith("rose");
    expect(mockedFetch).not.toHaveBeenCalled();
    expect(mockedSetBuiltin).not.toHaveBeenCalled();
    expect(mockedDelete).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Puspa (default)" }));
    expect(onSelect).toHaveBeenCalledWith("default");
  });
});
