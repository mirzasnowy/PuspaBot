import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatWidget } from "./ChatWidget";
import type {
  CalculatorWidget,
  ChatMessage,
  ComparisonWidget,
  DiagramWidget,
  FormWidget,
  SimulationWidget,
} from "../api/client";

const calculatorWidget: CalculatorWidget = {
  kind: "calculator",
  title: "Total belanja",
  inputs: [
    { id: "price", label: "Harga", input_kind: "number", value: 50000, min: 0, max: 1000000, step: 1000 },
    { id: "qty", label: "Jumlah", input_kind: "slider", value: 2, min: 1, max: 10, step: 1 },
  ],
  formula: "price * qty",
  result_label: "Total",
  value_format: "rupiah",
};

const formWidget: FormWidget = {
  kind: "form",
  title: "Data pengiriman",
  fields: [
    { id: "name", label: "Nama", field_kind: "text", required: true },
    { id: "qty", label: "Jumlah", field_kind: "number", required: true },
    { id: "city", label: "Kota", field_kind: "select", required: false, options: ["Jakarta", "Karawang"] },
    { id: "gift", label: "Bungkus kado", field_kind: "checkbox", required: false },
  ],
  submit_label: "Kirim",
};

const comparisonWidget: ComparisonWidget = {
  kind: "comparison",
  title: "Harga HP",
  columns: [
    { id: "price", label: "Harga (Rp)", numeric: true },
    { id: "note", label: "Catatan", numeric: false },
  ],
  rows: [
    { id: "a", label: "Toko A", values: { price: 5200000, note: "Gratis ongkir" } },
    { id: "b", label: "Toko B", values: { price: 5100000, note: "Stok 2" } },
  ],
};

const diagramWidget: DiagramWidget = {
  kind: "diagram",
  title: "Cara beli",
  nodes: [
    { id: "pick", label: "Pilih toko", detail: "Bandingkan harga dulu." },
    { id: "pay", label: "Bayar", detail: "Transfer atau COD." },
  ],
  edges: [{ from: "pick", to: "pay" }],
};

const simulationWidget: SimulationWidget = {
  kind: "simulation",
  title: "Price + bill simulation",
  product_id: "phone-x1",
  stores: ["Tokopedia", "Shopee", "Blibli"],
  start_day: 15,
  day: 15,
  bill_id: "bill-pln-001",
  bill_paid: false,
  prices: { Tokopedia: 5250000, Shopee: 5100000, Blibli: 5300000 },
  bill: {
    status: "not_due",
    amount_due: 150000,
    amount: 150000,
    due_day: 20,
    bill_id: "bill-pln-001",
    day: 15,
  },
  history: [
    { day: 15, prices: { Tokopedia: 5250000, Shopee: 5100000, Blibli: 5300000 } },
  ],
};

/** A fetch stub that answers with one JSON body, like HintBanner.test.tsx does. */
function mockFetchJson(body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

describe("ChatWidget calculator", () => {
  it("shows the live result and updates it when an input changes", async () => {
    const user = userEvent.setup();
    render(<ChatWidget widget={calculatorWidget} />);
    expect(screen.getByText("Rp100.000")).toBeTruthy();
    const qty = screen.getByLabelText("Jumlah");
    fireEvent.change(qty, { target: { value: "3" } });
    expect(screen.getByText("Rp150.000")).toBeTruthy();
    const price = screen.getByLabelText("Harga");
    await user.clear(price);
    await user.type(price, "60000");
    expect(screen.getByText("Rp180.000")).toBeTruthy();
  });
});

describe("ChatWidget form", () => {
  it("submits the answers and reports the new message", async () => {
    const user = userEvent.setup();
    const message: ChatMessage = {
      sender: "puspa",
      text: "Formulir 'Data pengiriman' terkirim: Nama: Sari; Jumlah: 2; Kota: Karawang; Bungkus kado: —",
      created_at: 1,
      widget: formWidget,
    };
    const fetchMock = mockFetchJson(message);
    vi.stubGlobal("fetch", fetchMock);
    try {
      const onNewMessage = vi.fn();
      render(<ChatWidget widget={formWidget} onNewMessage={onNewMessage} />);
      await user.type(screen.getByLabelText("Nama *"), "Sari");
      await user.type(screen.getByLabelText("Jumlah *"), "2");
      await user.selectOptions(screen.getByLabelText("Kota"), "Karawang");
      await user.click(screen.getByRole("button", { name: "Kirim" }));
      expect(await screen.findByRole("button", { name: "Terkirim" })).toBeTruthy();
      expect(onNewMessage).toHaveBeenCalledWith(message);
      const sent = JSON.parse(String(fetchMock.mock.calls[0][1].body));
      expect(sent.answers).toEqual({ name: "Sari", qty: 2, city: "Karawang", gift: null });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows the server error when the submission is refused", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "field 'Nama' is required" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<ChatWidget widget={formWidget} />);
      await userEvent.setup().click(screen.getByRole("button", { name: "Kirim" }));
      expect(await screen.findByRole("alert")).toBeTruthy();
      expect(screen.getByRole("alert").textContent).toContain("field 'Nama' is required");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("ChatWidget comparison", () => {
  it("highlights the cheapest row and sorts by the price column", async () => {
    const user = userEvent.setup();
    render(<ChatWidget widget={comparisonWidget} />);
    expect(screen.getByText(/★ Termurah/)).toBeTruthy();
    const rowsBefore = screen.getAllByRole("row").map((row) => row.textContent ?? "");
    expect(rowsBefore[1]).toContain("Toko A");
    await user.click(screen.getByRole("button", { name: /Harga \(Rp\)/ }));
    const rowsAfter = screen.getAllByRole("row").map((row) => row.textContent ?? "");
    expect(rowsAfter[1]).toContain("Toko B");
  });
});

describe("ChatWidget diagram", () => {
  it("shows a node's detail when the node is tapped", async () => {
    const user = userEvent.setup();
    render(<ChatWidget widget={diagramWidget} />);
    expect(screen.getByText("Ketuk satu langkah untuk melihat detailnya.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Pilih toko" }));
    expect(screen.getByTestId("diagram-detail").textContent).toContain("Bandingkan harga dulu.");
  });
});

describe("ChatWidget simulation", () => {
  it("steps a day forward through the server and reports the new message", async () => {
    const user = userEvent.setup();
    const stepped: SimulationWidget = {
      ...simulationWidget,
      day: 16,
      prices: { Tokopedia: 5248000, Shopee: 5103000, Blibli: 5300000 },
      history: [
        { day: 15, prices: { Tokopedia: 5250000, Shopee: 5100000, Blibli: 5300000 } },
        { day: 16, prices: { Tokopedia: 5248000, Shopee: 5103000, Blibli: 5300000 } },
      ],
    };
    const message: ChatMessage = {
      sender: "puspa",
      text: "Hari simulasi 16: Tokopedia Rp5.248.000, Shopee Rp5.103.000, Blibli Rp5.300.000.",
      created_at: 2,
      widget: stepped,
    };
    const fetchMock = mockFetchJson(message);
    vi.stubGlobal("fetch", fetchMock);
    try {
      const onNewMessage = vi.fn();
      render(<ChatWidget widget={simulationWidget} onNewMessage={onNewMessage} />);
      expect(screen.getByText("Rp5.250.000")).toBeTruthy();
      expect(screen.getAllByTestId("simulation-bar")).toHaveLength(3);
      await user.click(screen.getByRole("button", { name: "Hari berikutnya" }));
      expect(onNewMessage).toHaveBeenCalledWith(message);
      const sent = JSON.parse(String(fetchMock.mock.calls[0][1].body));
      expect(sent.action).toEqual({ type: "advance_day" });
      expect(sent.widget.day).toBe(15);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("disables the pay button once the bill is paid", () => {
    const paid = { ...simulationWidget, bill_paid: true, bill: { ...simulationWidget.bill, status: "paid" as const, amount_due: 0 } };
    render(<ChatWidget widget={paid} />);
    const button = screen.getByRole("button", { name: "Tagihan lunas" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});
