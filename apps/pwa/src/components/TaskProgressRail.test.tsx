import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaskProgressRail } from "./TaskProgressRail";
import type { OfficeProjection } from "../api/client";

describe("TaskProgressRail", () => {
  it("shows one row per working agent from the list-shaped projection", () => {
    const office: OfficeProjection = {
      agents: [
        {
          agent_id: "puspa",
          status: "working",
          last_task: "task-1",
          cost_rp: 250,
          subagents: [{ status: "working" }, { status: "idle" }],
        },
        { agent_id: "agen-1", status: "idle", last_task: "task-0", cost_rp: 100 },
      ],
    };
    render(<TaskProgressRail office={office} connected={true} />);
    expect(screen.getByText("puspa")).toBeTruthy();
    expect(screen.getByText(/Tugas: task-1/)).toBeTruthy();
    expect(screen.getByText(/1 sub-agen bekerja/)).toBeTruthy();
    expect(screen.getByText(/Rp250/)).toBeTruthy();
    expect(screen.queryByText("agen-1")).toBeNull();
  });

  it("also reads the keyed map shape", () => {
    const office: OfficeProjection = {
      agent: {
        puspa: { agent_id: "puspa", status: "working", last_task: "task-9", cost_rp: 0 },
      },
    };
    render(<TaskProgressRail office={office} connected={true} />);
    expect(screen.getByText(/Tugas: task-9/)).toBeTruthy();
  });

  it("shows the empty state when nothing is running", () => {
    render(
      <TaskProgressRail
        office={{ agents: [{ agent_id: "puspa", status: "idle", last_task: "task-1" }] }}
        connected={true}
      />,
    );
    expect(screen.getByText("Tidak ada tugas berjalan saat ini.")).toBeTruthy();
  });

  it("shows the waiting state while the stream is not connected", () => {
    render(<TaskProgressRail office={null} connected={false} />);
    expect(screen.getByText("Live: menunggu SSE…")).toBeTruthy();
  });
});
