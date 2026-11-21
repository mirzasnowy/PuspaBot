import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PersonasScreen } from "./PersonasScreen";

afterEach(() => {
  vi.unstubAllGlobals();
});

type FetchCall = { url: string; method: string; body: unknown };

function stubFetch(handler: (call: FetchCall) => unknown) {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string, options?: { method?: string; body?: string }) => {
      const call: FetchCall = {
        url,
        method: options?.method ?? "GET",
        body: options?.body ? JSON.parse(options.body as string) : undefined,
      };
      calls.push(call);
      const body = handler(call);
      if (body instanceof Error) {
        return { ok: false, status: 500, json: async () => ({ error: body.message }) };
      }
      return { ok: true, status: 200, json: async () => body };
    }),
  );
  return calls;
}

function listPayload(active = "default") {
  return {
    personas: [
      { slug: "default", name: "Puspa", style: null, is_active: active === "default", is_default: true },
      { slug: "kerja", name: "Kerja", style: "rapi", is_active: active === "kerja", is_default: false },
    ],
    active,
  };
}

describe("PersonasScreen", () => {
  it("lists personas with the active one checked and shows the create form", async () => {
    stubFetch((call) => {
      if (call.url === "/api/personas" && call.method === "GET") return listPayload();
      throw new Error(`unexpected fetch: ${call.method} ${call.url}`);
    });
    render(<PersonasScreen />);
    expect(await screen.findByText("Persona")).toBeTruthy();
    expect(await screen.findByRole("radio", { name: /Kerja/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Puspa/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Aktifkan persona" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Buat persona" })).toBeTruthy();
  });

  it("switches the active persona only on the explicit submit", async () => {
    const user = userEvent.setup();
    const calls = stubFetch((call) => {
      if (call.url === "/api/personas" && call.method === "GET") return listPayload();
      if (call.url === "/api/personas/kerja/activate" && call.method === "POST")
        return listPayload("kerja");
      throw new Error(`unexpected fetch: ${call.method} ${call.url}`);
    });
    render(<PersonasScreen />);
    await user.click(await screen.findByRole("radio", { name: /Kerja/ }));
    // Clicking the radio alone must not call the API.
    expect(calls.filter((c) => c.url.includes("activate"))).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Aktifkan persona" }));
    expect(await screen.findByText(/Persona aktif sekarang: kerja/)).toBeTruthy();
    expect(calls.filter((c) => c.url.includes("activate"))).toHaveLength(1);
  });

  it("creates a persona from the form", async () => {
    const user = userEvent.setup();
    const calls = stubFetch((call) => {
      if (call.url === "/api/personas" && call.method === "GET") return listPayload();
      if (call.url === "/api/personas" && call.method === "POST") {
        return {
          slug: "personal",
          name: "Personal",
          soul: "Santai aja.",
          identity: "Name: Personal",
          user: "",
          style: "santai",
          is_active: false,
          is_default: false,
        };
      }
      throw new Error(`unexpected fetch: ${call.method} ${call.url}`);
    });
    render(<PersonasScreen />);
    await screen.findByRole("radio", { name: /Kerja/ });
    await user.type(screen.getByLabelText(/Nama persona/), "Personal");
    await user.type(screen.getByLabelText(/Prompt/), "Santai aja.");
    await user.click(screen.getByRole("button", { name: "Buat persona" }));
    expect(await screen.findByText(/Persona Personal dibuat/)).toBeTruthy();
    const created = calls.find((c) => c.method === "POST" && c.url === "/api/personas");
    expect(created?.body).toMatchObject({ name: "Personal", soul: "Santai aja." });
  });

  it("deletes a persona after confirming", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("confirm", () => true);
    const calls = stubFetch((call) => {
      if (call.url === "/api/personas" && call.method === "GET") return listPayload();
      if (call.url === "/api/personas/kerja" && call.method === "DELETE")
        return { deleted: "kerja", active: "default" };
      throw new Error(`unexpected fetch: ${call.method} ${call.url}`);
    });
    render(<PersonasScreen />);
    await user.click(await screen.findByRole("button", { name: "Hapus persona Kerja" }));
    expect(await screen.findByText(/Persona Kerja dihapus/)).toBeTruthy();
    expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/personas/kerja")).toBe(true);
  });
});
