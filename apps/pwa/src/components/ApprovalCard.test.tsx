import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalCard } from "./ApprovalCard";
import type { ApprovalRequest } from "../api/client";

function item(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    request_id: "req-1",
    kind: "spend",
    status: "pending",
    summary: "Pay Rp50.000 to PLN",
    amount_rp: 50000,
    target: "PLN",
    content_hash: "abc123hashvalue",
    ...overrides,
  };
}

describe("ApprovalCard", () => {
  it("shows the full approval content on the card", () => {
    render(<ApprovalCard item={item()} deciding={false} onDecide={() => {}} />);
    expect(screen.getByText("Pay Rp50.000 to PLN")).toBeTruthy();
    expect(screen.getByText("Rp50.000")).toBeTruthy();
    expect(screen.getByText("PLN")).toBeTruthy();
    expect(screen.getByTestId("approval-hash").textContent).toBe("abc123hashva…");
    expect(screen.getByTestId("approval-status").textContent).toBe("Menunggu");
  });

  it("approves with one tap and calls onDecide once", async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn();
    render(<ApprovalCard item={item()} deciding={false} onDecide={onDecide} />);
    await user.click(screen.getByRole("button", { name: "Setuju" }));
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith("req-1", true);
  });

  it("rejects with one tap", async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn();
    render(<ApprovalCard item={item()} deciding={false} onDecide={onDecide} />);
    await user.click(screen.getByRole("button", { name: "Tolak" }));
    expect(onDecide).toHaveBeenCalledWith("req-1", false);
  });

  it("locks the buttons once the card is decided", () => {
    render(
      <ApprovalCard item={item({ status: "approved" })} deciding={false} onDecide={() => {}} />,
    );
    expect(screen.getByRole("button", { name: "Setuju" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Tolak" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("approval-status").textContent).toBe("Disetujui");
  });
});
