import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WikiScreen } from "./WikiScreen";

function stubFetch() {
  return vi.fn().mockImplementation((url: string, options?: { method?: string }) => {
    const path = String(url);
    let body: unknown;
    if (path === "/api/wiki/topics") {
      body = {
        topics: [
          {
            slug: "phone",
            title: "Phone",
            entries: 2,
            sources: ["fact:preference:coffee"],
            entities: ["phone", "tokopedia"],
            updated_at: "2026-11-22T00:00:00+00:00",
          },
        ],
      };
    } else if (path.startsWith("/api/wiki/pages/")) {
      body = {
        slug: "phone",
        title: "Phone",
        markdown: "# Phone\n\n- User prefers Tokopedia\n",
        html: "<h1>Phone</h1>\n<ul><li>User prefers Tokopedia</li></ul>",
        sources: ["fact:preference:coffee"],
        entities: ["phone", "tokopedia"],
        updated_at: "2026-11-22T00:00:00+00:00",
      };
    } else if (path.startsWith("/api/wiki/graph")) {
      body = {
        topic: "phone",
        title: "Phone",
        nodes: [
          {
            name: "tokopedia",
            kind: null,
            description: "",
            sources: ["ep1#p0"],
            fact_count: 1,
            seen_at: null,
            is_code_node: false,
          },
        ],
        edges: [],
        total_nodes: 1,
        total_edges: 0,
      };
    } else if (path === "/api/wiki/build" && options?.method === "POST") {
      body = {
        user_id: "u1",
        wiki_dir: "/home/wiki/u1",
        pages_written: 1,
        files: ["phone.md"],
        generated_at: "2026-11-22T00:00:00+00:00",
        entries: 2,
        topics: ["phone"],
      };
    } else {
      body = {};
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => body,
    });
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WikiScreen", () => {
  it("lists topics and renders the selected page with its sources", async () => {
    vi.stubGlobal("fetch", stubFetch());
    render(<WikiScreen />);
    expect(await screen.findByText(/Phone — 2 entri/)).toBeTruthy();
    expect(await screen.findByText("User prefers Tokopedia")).toBeTruthy();
    expect(screen.getByText(/Sumber: fact:preference:coffee/)).toBeTruthy();
  });

  it("switches to the read-only graph tab and shows the topic nodes", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", stubFetch());
    render(<WikiScreen />);
    await screen.findByText("User prefers Tokopedia");
    await user.click(screen.getByRole("tab", { name: /Grafik/ }));
    expect(await screen.findByText("Simpul")).toBeTruthy();
    expect(screen.getByText("tokopedia")).toBeTruthy();
  });

  it("rebuilds the wiki from the button and reports the count", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", stubFetch());
    render(<WikiScreen />);
    await screen.findByText(/Phone — 2 entri/);
    await user.click(screen.getByRole("button", { name: /Bangun ulang wiki/ }));
    expect(await screen.findByText(/Selesai: 1 halaman, 2 entri/)).toBeTruthy();
  });

  it("says honestly when nothing has been curated yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve({ ok: true, status: 200, json: async () => ({ topics: [] }) }),
      ),
    );
    render(<WikiScreen />);
    expect(await screen.findByText(/Belum ada yang dikurasi/)).toBeTruthy();
  });
});
