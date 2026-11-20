import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MorningScreen } from "./MorningScreen";
import type {
  MorningNoteList,
  MorningStatus,
  MorningSummary,
} from "../api/morning";

function summaryBody(): MorningSummary {
  return {
    date: "2026-11-20",
    weekday: "Friday",
    schedule: {
      events: [
        { summary: "Rapat pagi", start: "09:00", end: "10:00", location: "Rumah" },
      ],
    },
    bills: {
      candidates: [
        {
          message_id: "m1",
          subject: "Tagihan Listrik",
          snippet: "Tagihan listrik Anda jatuh tempo 12 Desember 2026.",
          date: "",
          due_hint: "12 Desember 2026",
          due_hint_source: "subject_or_snippet",
          verified: false,
          note: "Candidate from a keyword match.",
        },
      ],
    },
    tasks: {
      open: [
        {
          task_id: "tugas-1",
          purpose: "Cek harga beras",
          status: "accepted",
          created_at: null,
        },
      ],
    },
    holidays: {
      today: [],
      upcoming: [
        { date: "2026-12-15", local_name: "Hari Raya Natal", name: "Christmas Day" },
      ],
    },
    prayer: {
      city: "Jakarta",
      date: "2026-11-20",
      timings: {
        fajr: "04:03",
        sunrise: "05:26",
        dhuhr: "11:38",
        asr: "15:02",
        maghrib: "17:51",
        isha: "19:05",
        imsak: null,
      },
      hijri: { date: "10-06-1448", month: "Jumādá al-ākhirah", month_number: 6, year: "1448" },
      is_ramadan: false,
      method_name: "Kementerian Agama RI",
      imsak_note: "Imsak is shown only during Ramadan.",
      source: "Aladhan API",
    },
    source_status: {
      calendar: { status: "not_connected", detail: "Calendar is not connected." },
      gmail: { status: "ok", detail: "1 bill candidate(s) from email." },
      tasks: { status: "ok", detail: "1 open task(s)." },
      holidays: { status: "ok", detail: "Holidays loaded." },
      prayer: { status: "ok", detail: "Prayer times loaded." },
    },
  };
}

function statusBody(): MorningStatus {
  return {
    calendar: { status: "not_connected", detail: "Calendar is not connected." },
    gmail: { status: "ok", detail: "Gmail connected." },
    transcriber: { engine: "stub", engine_selected: false, note: "Stub engine." },
    prayer: { status: "public", detail: "Aladhan API." },
    holidays: { status: "public", detail: "Nager.Date API." },
  };
}

function notesBody(): MorningNoteList {
  return {
    notes: [
      {
        note_id: "note-1",
        source: "meeting",
        title: "Pengajian",
        summary: "Rapat pengajian malam ini.",
        actions: [
          {
            text: "@Budi tolong hubungi ustadz besok",
            owner: "Budi",
            due_hint: "besok",
            source_line: 1,
            task_id: "tugas-1",
          },
          {
            text: "Bayar iuran kas",
            owner: null,
            due_hint: null,
            source_line: 2,
            task_id: "tugas-2",
          },
        ],
        created_at: "2026-11-20T00:00:00Z",
      },
    ],
  };
}

function mockFetch() {
  const calls: string[] = [];
  const fetchMock = vi.fn((url: string) => {
    calls.push(typeof url === "string" ? url : String(url));
    let body: unknown = {};
    if (url.includes("/api/morning/summary")) body = summaryBody();
    else if (url.includes("/api/morning/status")) body = statusBody();
    else if (url.includes("/api/morning/notes/text")) {
      body = {
        ok: true,
        note_id: "note-2",
        summary: "Rapat pengajian.",
        actions: [
          {
            text: "Bayar iuran kas",
            owner: null,
            due_hint: null,
            source_line: 1,
            task_id: "tugas-9",
          },
        ],
      };
    } else if (url.includes("/api/morning/notes")) body = notesBody();
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => body,
    } as Response);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

describe("MorningScreen", () => {
  it("shows schedule, bills, tasks, holidays, and prayer times", async () => {
    mockFetch();
    try {
      render(<MorningScreen />);
      await waitFor(() => expect(screen.getByText(/Rapat pagi/)).toBeTruthy());
      expect(screen.getByText(/Tagihan Listrik/)).toBeTruthy();
      expect(screen.getByText(/Belum terverifikasi/)).toBeTruthy();
      expect(screen.getByText("Cek harga beras")).toBeTruthy();
      expect(screen.getByText(/Hari Raya Natal/)).toBeTruthy();
      expect(screen.getByText(/Zuhur: 11:38/)).toBeTruthy();
      // Imsak is hidden outside Ramadan, with the note instead.
      expect(screen.queryByText(/Imsak: /)).toBeNull();
      expect(screen.getByText(/Imsak is shown only during Ramadan/)).toBeTruthy();
      // Honest per-source status is shown.
      expect(screen.getByText(/Kalender: not_connected/)).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("lists saved notes with owners and unassigned actions", async () => {
    mockFetch();
    try {
      render(<MorningScreen />);
      await waitFor(() => expect(screen.getByText("Pengajian")).toBeTruthy());
      expect(screen.getByText(/Budi/)).toBeTruthy();
      expect(screen.getByText(/Belum ada penanggung jawab/)).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("saves a pasted note and refreshes the list", async () => {
    const { fetchMock } = mockFetch();
    try {
      const user = userEvent.setup();
      render(<MorningScreen />);
      await waitFor(() => expect(screen.getByText("Pengajian")).toBeTruthy());
      await user.type(
        screen.getByPlaceholderText(/@Budi tolong hubungi ustadz besok/),
        "Bayar iuran kas.",
      );
      await user.click(screen.getByText("Simpan jadi tindakan"));
      await waitFor(() =>
        expect(screen.getByText(/Catatan tersimpan: 1 tindakan/)).toBeTruthy(),
      );
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).includes("/api/morning/notes/text"),
        ),
      ).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows an error when the summary cannot load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ error: "boom" }),
      } as Response),
    );
    try {
      render(<MorningScreen />);
      await waitFor(() =>
        expect(screen.getByText(/Gagal memuat ringkasan pagi/)).toBeTruthy(),
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
