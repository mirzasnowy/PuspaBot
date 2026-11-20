import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoiceRecorder } from "./VoiceRecorder";
import { confirmVoice, discardVoice, uploadVoice } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    uploadVoice: vi.fn(),
    confirmVoice: vi.fn(),
    discardVoice: vi.fn(),
  };
});

const mockedUpload = vi.mocked(uploadVoice);
const mockedConfirm = vi.mocked(confirmVoice);
const mockedDiscard = vi.mocked(discardVoice);

class MockMediaRecorder {
  static isTypeSupported = () => true;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  state: string = "inactive";
  mimeType = "audio/webm";

  constructor(
    public stream: MediaStream,
    public options?: MediaRecorderOptions,
  ) {}

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["audio"], { type: "audio/webm" }) });
    this.onstop?.();
  }
}

function installRecordingSupport() {
  Object.defineProperty(window, "MediaRecorder", {
    value: MockMediaRecorder,
    configurable: true,
  });
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [] }) },
    configurable: true,
  });
}

function removeRecordingSupport() {
  Object.defineProperty(window, "MediaRecorder", { value: undefined, configurable: true });
  Object.defineProperty(navigator, "mediaDevices", { value: undefined, configurable: true });
}

describe("VoiceRecorder", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    removeRecordingSupport();
  });

  it("says voice is unsupported when the browser has no MediaRecorder", () => {
    render(<VoiceRecorder onSent={() => {}} />);
    expect(screen.getByText(/tidak didukung browser ini/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Rekam/ })).toBeNull();
  });

  it("records, shows the transcript for confirmation, and sends on confirm", async () => {
    installRecordingSupport();
    const user = userEvent.setup();
    const onSent = vi.fn();
    mockedUpload.mockResolvedValue({
      ok: true,
      pending_id: "pend-1",
      transcript: "tolong cek harga beras",
      language: "id",
      engine: "stub",
      audio_deleted: true,
      size_bytes: 5,
      mime_type: "audio/webm",
    });
    mockedConfirm.mockResolvedValue({
      ok: true,
      message: { sender: "users", text: "tolong cek harga beras", created_at: 1 },
    });
    render(<VoiceRecorder onSent={onSent} />);

    await user.click(screen.getByRole("button", { name: "Rekam pesan suara" }));
    expect(await screen.findByRole("button", { name: "Berhenti" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Berhenti" }));
    const transcriptBox = await screen.findByLabelText("Transkrip pesan suara");
    expect((transcriptBox as HTMLTextAreaElement).value).toBe("tolong cek harga beras");
    expect(mockedUpload).toHaveBeenCalledTimes(1);
    // Nothing sent yet: confirmation is a separate tap.
    expect(mockedConfirm).not.toHaveBeenCalled();
    expect(onSent).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Kirim transkrip" }));
    await waitFor(() => expect(mockedConfirm).toHaveBeenCalledWith("pend-1", "tolong cek harga beras"));
    expect(onSent).toHaveBeenCalledTimes(1);
    expect(onSent).toHaveBeenCalledWith({
      sender: "users",
      text: "tolong cek harga beras",
      created_at: 1,
    });
  });

  it("sends the edited transcript when the user corrects it", async () => {
    installRecordingSupport();
    const user = userEvent.setup();
    mockedUpload.mockResolvedValue({
      ok: true,
      pending_id: "pend-2",
      transcript: "tolong cek harga beras",
      language: "id",
      engine: "stub",
      audio_deleted: true,
      size_bytes: 5,
      mime_type: "audio/webm",
    });
    mockedConfirm.mockResolvedValue({
      ok: true,
      message: { sender: "users", text: "cek harga gula", created_at: 1 },
    });
    render(<VoiceRecorder onSent={() => {}} />);

    await user.click(screen.getByRole("button", { name: "Rekam pesan suara" }));
    await user.click(await screen.findByRole("button", { name: "Berhenti" }));
    const transcriptBox = await screen.findByLabelText("Transkrip pesan suara");
    await user.clear(transcriptBox);
    await user.type(transcriptBox, "cek harga gula");
    await user.click(screen.getByRole("button", { name: "Kirim transkrip" }));
    await waitFor(() => expect(mockedConfirm).toHaveBeenCalledWith("pend-2", "cek harga gula"));
  });

  it("discards the pending transcript without sending it", async () => {
    installRecordingSupport();
    const user = userEvent.setup();
    mockedUpload.mockResolvedValue({
      ok: true,
      pending_id: "pend-3",
      transcript: "tolong cek harga beras",
      language: "id",
      engine: "stub",
      audio_deleted: true,
      size_bytes: 5,
      mime_type: "audio/webm",
    });
    mockedDiscard.mockResolvedValue({ ok: true });
    render(<VoiceRecorder onSent={() => {}} />);

    await user.click(screen.getByRole("button", { name: "Rekam pesan suara" }));
    await user.click(await screen.findByRole("button", { name: "Berhenti" }));
    await screen.findByLabelText("Transkrip pesan suara");
    await user.click(screen.getByRole("button", { name: "Buang" }));
    await waitFor(() => expect(mockedDiscard).toHaveBeenCalledWith("pend-3"));
    expect(mockedConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Rekam pesan suara" })).toBeTruthy();
  });
});
