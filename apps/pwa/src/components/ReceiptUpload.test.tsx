import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReceiptUpload } from "./ReceiptUpload";
import { confirmReceipt, discardReceipt, uploadReceipt } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    uploadReceipt: vi.fn(),
    confirmReceipt: vi.fn(),
    discardReceipt: vi.fn(),
  };
});

const mockedUpload = vi.mocked(uploadReceipt);
const mockedConfirm = vi.mocked(confirmReceipt);
const mockedDiscard = vi.mocked(discardReceipt);

const DRAFT = {
  merchant: "WARUNG KOPI SEDUH",
  expense_date: "2026-11-20",
  currency: "Rp",
  lines: [
    { name: "Kopi susu", amount: 18000, evidence_id: "receipt-p1-line-3", line_index: 3 },
    { name: "Nasi goreng", amount: 25000, evidence_id: "receipt-p1-line-4", line_index: 4 },
  ],
  total: 43000,
  total_evidence_id: "receipt-p1-total",
  total_is_sum: false,
  held_amounts: 0,
};

function mockUploadOk() {
  mockedUpload.mockResolvedValue({
    ok: true,
    pending_id: "pending-1",
    photo_id: "p1",
    photo_path: null,
    draft: DRAFT,
    ocr_lines: [],
    engine: "stub",
    elapsed_seconds: 0.01,
    document_kind: "receipt",
    size_bytes: 123,
    mime_type: "image/png",
  });
}

async function pickPhoto() {
  const user = userEvent.setup();
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["photo"], "struk.png", { type: "image/png" });
  await user.upload(input, file);
  return user;
}

describe("ReceiptUpload", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the parsed draft for confirmation and records it on confirm", async () => {
    mockUploadOk();
    mockedConfirm.mockResolvedValue({
      ok: true,
      expense: {
        expense_id: "e1",
        photo_id: "p1",
        photo_path: null,
        merchant: "WARUNG KOPI SEDUH",
        expense_date: "2026-11-20",
        currency: "Rp",
        lines: DRAFT.lines,
        total: 43000,
        total_evidence_id: "receipt-p1-total",
        edited_by_user: false,
        engine: "stub",
        document_kind: "receipt",
        created_at: 1,
      },
    });
    const onRecorded = vi.fn();
    render(<ReceiptUpload onRecorded={onRecorded} />);
    await pickPhoto();
    await waitFor(() => expect(screen.getByText(/Foto struk dibaca/)).toBeTruthy());
    expect(screen.getByDisplayValue("WARUNG KOPI SEDUH")).toBeTruthy();
    expect(screen.getByDisplayValue("43000")).toBeTruthy();
    expect(screen.getByText(/Kopi susu/)).toBeTruthy();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Simpan pengeluaran" }));
    await waitFor(() => expect(mockedConfirm).toHaveBeenCalled());
    expect(mockedConfirm).toHaveBeenCalledWith("pending-1", {
      merchant: "WARUNG KOPI SEDUH",
      total: 43000,
    });
    expect(onRecorded).toHaveBeenCalledTimes(1);
    expect(onRecorded.mock.calls[0][0].text).toContain("WARUNG KOPI SEDUH");
  });

  it("sends the edited merchant and total on confirm", async () => {
    mockUploadOk();
    mockedConfirm.mockResolvedValue({ ok: true, expense: { merchant: "Warung A", total: 44000 } } as never);
    render(<ReceiptUpload onRecorded={vi.fn()} />);
    await pickPhoto();
    await waitFor(() => expect(screen.getByText(/Foto struk dibaca/)).toBeTruthy());
    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("Nama tempat"));
    await user.type(screen.getByLabelText("Nama tempat"), "Warung A");
    await user.clear(screen.getByLabelText("Total"));
    await user.type(screen.getByLabelText("Total"), "44000");
    await user.click(screen.getByRole("button", { name: "Simpan pengeluaran" }));
    await waitFor(() => expect(mockedConfirm).toHaveBeenCalled());
    expect(mockedConfirm).toHaveBeenCalledWith("pending-1", {
      merchant: "Warung A",
      total: 44000,
    });
  });

  it("discards the draft without recording anything", async () => {
    mockUploadOk();
    mockedDiscard.mockResolvedValue({ ok: true });
    const onRecorded = vi.fn();
    render(<ReceiptUpload onRecorded={onRecorded} />);
    await pickPhoto();
    await waitFor(() => expect(screen.getByText(/Foto struk dibaca/)).toBeTruthy());
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Buang" }));
    await waitFor(() => expect(mockedDiscard).toHaveBeenCalledWith("pending-1"));
    expect(mockedConfirm).not.toHaveBeenCalled();
    expect(onRecorded).not.toHaveBeenCalled();
  });

  it("shows an error when the photo is refused or unreadable", async () => {
    mockedUpload.mockRejectedValue(new Error("identity document"));
    render(<ReceiptUpload onRecorded={vi.fn()} />);
    await pickPhoto();
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("alert").textContent).toContain("identity document");
    expect(mockedConfirm).not.toHaveBeenCalled();
  });
});
