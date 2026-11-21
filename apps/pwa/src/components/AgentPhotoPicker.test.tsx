import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentPhotoPicker } from "./AgentPhotoPicker";
import { deleteAgentPhoto, fetchAgentAvatar, uploadAgentPhoto } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    fetchAgentAvatar: vi.fn(),
    uploadAgentPhoto: vi.fn(),
    deleteAgentPhoto: vi.fn(),
  };
});

const mockedFetch = vi.mocked(fetchAgentAvatar);
const mockedUpload = vi.mocked(uploadAgentPhoto);
const mockedDelete = vi.mocked(deleteAgentPhoto);

const NO_PHOTO = {
  ok: true,
  has_photo: false,
  photo_url: null,
  updated_at: null,
  mime_type: null,
};

const WITH_PHOTO = {
  ok: true,
  has_photo: true,
  photo_url: "/api/agent/avatar/photo",
  updated_at: 1780000000,
  mime_type: "image/png",
};

describe("AgentPhotoPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedFetch.mockResolvedValue(NO_PHOTO);
  });

  it("shows the default logo and no remove button when no photo is set", async () => {
    render(<AgentPhotoPicker />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    const img = screen.getByAltText("Foto agen Puspa") as HTMLImageElement;
    expect(img.src).toContain("/static/puspa-logo.png");
    expect(screen.getByText("Belum ada foto kustom; logo default Puspa dipakai (/static/puspa-logo.png).")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Hapus foto/ })).toBeNull();
  });

  it("uploads a picked photo and shows the new preview", async () => {
    mockedUpload.mockResolvedValue(WITH_PHOTO);
    const user = userEvent.setup();
    render(<AgentPhotoPicker />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["photo"], "puspa.png", { type: "image/png" });
    await user.upload(input, file);
    await waitFor(() => expect(mockedUpload).toHaveBeenCalled());
    expect(mockedUpload.mock.calls[0][0]).toBe(file);
    await waitFor(() => expect(screen.getByText("Foto agen tersimpan.")).toBeTruthy());
    const img = screen.getByAltText("Foto agen Puspa") as HTMLImageElement;
    expect(img.src).toContain("/api/agent/avatar/photo?v=1780000000");
  });

  it("refuses a too-large file in the browser without uploading", async () => {
    const user = userEvent.setup();
    render(<AgentPhotoPicker />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const big = new File([new Uint8Array(2 * 1024 * 1024 + 1)], "big.png", {
      type: "image/png",
    });
    await user.upload(input, big);
    await waitFor(() => expect(screen.getByText("Foto terlalu besar; maksimal 2 MB.")).toBeTruthy());
    expect(mockedUpload).not.toHaveBeenCalled();
  });

  it("shows the server error when the upload fails", async () => {
    mockedUpload.mockRejectedValue(new Error("unsupported photo type"));
    const user = userEvent.setup();
    render(<AgentPhotoPicker />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["x"], "puspa.png", { type: "image/png" }));
    await waitFor(() =>
      expect(screen.getByText("Gagal menyimpan foto: unsupported photo type")).toBeTruthy(),
    );
  });

  it("removes the photo and falls back to the default logo", async () => {
    mockedFetch.mockResolvedValue(WITH_PHOTO);
    mockedDelete.mockResolvedValue({ ok: true, has_photo: false });
    const user = userEvent.setup();
    render(<AgentPhotoPicker />);
    await waitFor(() => expect(screen.getByText("Foto agen kustom terpasang.")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: /Hapus foto/ }));
    await waitFor(() => expect(mockedDelete).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText("Foto agen dihapus; logo default dipakai lagi.")).toBeTruthy(),
    );
    const img = screen.getByAltText("Foto agen Puspa") as HTMLImageElement;
    expect(img.src).toContain("/static/puspa-logo.png");
  });
});
