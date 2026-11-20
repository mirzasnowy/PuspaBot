"""Replay timeline of a bot's steps (P-10, plan section 12.10).

What this is, honestly: a task's ledger events, played back
step by step like a video — each step shows what the bot did,
why (derived from the event itself), and what came of it —
with scrubbing (jump to any step, forward or back) and a GIF
export of the whole replay. It replays through the P-09 seam:
the steps are the frame events :mod:`puspa.pwa.live_view`
builds, oldest first, with the hash chain verified before
anything plays.

What it is not — the same honest limit as P-09: there is no
real desktop screenshot. Puspa has no desktop/browser/CDP to
watch, so the ``screenshot`` of every step stays ``None`` and
the GIF is built from *rendered* frames: deterministic PIL
pictures drawn from the step's own data (step number, event
type, tool, reason, result). The screen and the GIF say so in
plain words instead of faking a desktop recording. When a
backend that can produce frames lands, it implements the
:class:`puspa.pwa.live_view.FrameSource` capture seam and the
steps gain real screenshots without changing this API shape.

Rules the replay keeps:

- Ownership is enforced here, not just in the HTTP layer:
  every function takes the requesting ``user_id`` and refuses
  (:class:`ReplayError`, 403) when the agent is not theirs,
  and a task whose events belong to another agent reads as
  404, never as someone else's steps.
- A replay never invents a reason or a result: both are
  derived from the stored event content only. When the event
  recorded no reason, the step says so plainly.
- The hash chain is verified before playback and reported on
  the timeline (``chain_ok`` + ``broken_event_ids``), so a
  replay of a tampered task shows the break instead of
  playing past it.
- The GIF is deterministic: the same steps always render the
  same bytes (fixed canvas, fixed font, fixed frame length),
  so an export can be re-run and compared byte for byte.
"""

from __future__ import annotations

import io
import os
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row

from ..evidence.proof import user_agent_ids
from ..ledger import Ledger

if TYPE_CHECKING:
    from ..ledger import Ledger as LedgerType

__all__ = [
    "GIF_FRAME_DURATION_MS",
    "GIF_FRAME_HEIGHT",
    "GIF_FRAME_WIDTH",
    "MAX_REPLAY_STEPS",
    "ReplayError",
    "ReplayStep",
    "ReplayTimeline",
    "build_replay",
    "export_replay_gif",
    "list_tasks_for_agent",
    "render_step_frame",
]

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: A task with more steps than this cannot be replayed in one
#: timeline: the GIF would be unusably long and the screen
#: unusable. Real tasks stay far below this; the cap is a guard
#: rail, not a target.
MAX_REPLAY_STEPS = 500

#: Rendered GIF frame size (480x270, 16:9) and per-frame length.
GIF_FRAME_WIDTH = 480
GIF_FRAME_HEIGHT = 270
GIF_FRAME_DURATION_MS = 800

#: How much of a recorded reason/result the step shows.
MAX_REASON_CHARS = 200
MAX_RESULT_CHARS = 200

#: Event types that end a task, and the timeline status they map to.
TERMINAL_TASK_STATUS = {
    "task_completed": "completed",
    "task_failed": "failed",
    "task_stopped": "stopped",
}


class ReplayError(Exception):
    """A replay was refused.

    ``status`` is the HTTP status the API layer answers with:
    403 not your agent, 404 unknown task (or step), 400 a task
    too long to replay in one timeline.
    """

    def __init__(self, message: str, *, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


def _require_owner(user_id: str, agent_id: str, *, dsn: str) -> None:
    """403 when ``agent_id`` is not one of ``user_id``'s agents."""
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("user_id must be a non-empty string")
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent_id must be a non-empty string")
    if agent_id not in user_agent_ids(user_id, dsn=dsn):
        raise ReplayError(f"agent {agent_id!r} does not belong to this user", status=403)


def _event_time_iso(event: dict) -> str:
    created_at = event.get("created_at")
    if isinstance(created_at, datetime):
        return created_at.astimezone(UTC).isoformat()
    if isinstance(created_at, (int, float)):
        return datetime.fromtimestamp(created_at, tz=UTC).isoformat()
    return str(created_at)


def _short(value: Any, limit: int) -> str:
    """One line of ``value`` (a recorded string), clipped to ``limit``."""
    text = str(value).strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _first_text(content: dict, *keys: str) -> str:
    """The first non-empty string among ``content[key]`` for ``keys``."""
    for key in keys:
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _tool(content: dict) -> str:
    """The tool a step used, or "" when the step used none."""
    tool = content.get("tool")
    return tool if isinstance(tool, str) else ""


def _describe(event_type: str, content: dict) -> tuple[str, str | None]:
    """(reason, result) for one step, from the event content only.

    The reason says why the step happened; the result says what
    came of it, when the event recorded one (a ``tool_called``
    has no result yet — the result arrives in the later
    ``tool_completed`` step). Nothing is invented: when the
    event recorded no reason, the step says so.
    """
    tool = _tool(content)
    recorded = _first_text(content, "reason", "alasan")
    because = f" — {recorded}" if recorded else ""
    if event_type == "task_created":
        purpose = _first_text(content, "purpose", "description", "text")
        reason = "Bot memulai tugas ini."
        if purpose:
            reason += f" Tujuannya: {_short(purpose, MAX_REASON_CHARS)}"
        return reason, None
    if event_type == "plan_created":
        return "Bot menyusun rencana untuk tugas ini sebelum bekerja.", None
    if event_type == "subagent_created":
        role = _first_text(content, "role", "permission_class")
        return (
            f"Bot membuat subagen baru{(' (' + role + ')') if role else ''} untuk bagian tugas ini.",
            None,
        )
    if event_type == "handoff_proposed":
        return "Bot mengusulkan serah terima tugas ke agen lain.", None
    if event_type == "handoff_accepted":
        return "Serah terima diterima: agen lain mengambil alih tugas ini.", None
    if event_type == "handoff_handed_over":
        return "Tugas diserahkan ke agen lain.", None
    if event_type == "handoff_rejected":
        return f"Serah terima ditolak{because}.", None
    if event_type == "handoff_expired":
        return "Usulan serah terima kedaluwarsa tanpa jawaban.", None
    if event_type == "heartbeat":
        return "Bot masih hidup: detak jantung berkala selama tugas berjalan.", None
    if event_type == "tool_called":
        return (
            f"Bot memanggil tool {tool or '(tidak tercatat)'}{because}.",
            None,
        )
    if event_type == "tool_completed":
        result = _first_text(content, "result", "output", "summary")
        return (
            f"Tool {tool or '(tidak tercatat)'} selesai.",
            _short(result, MAX_RESULT_CHARS) if result else None,
        )
    if event_type == "evidence_recorded":
        kind = _first_text(content, "kind", "type")
        summary = _first_text(content, "summary", "text")
        reason = f"Bot mencatat bukti{(' (' + kind + ')') if kind else ''}."
        return reason, _short(summary, MAX_RESULT_CHARS) if summary else None
    if event_type == "verification_completed":
        verdict = _first_text(content, "verdict", "result")
        return (
            "Bot memeriksa hasil kerjanya sendiri.",
            _short(verdict, MAX_RESULT_CHARS) if verdict else None,
        )
    if event_type == "approval_requested":
        action = _first_text(content, "action", "kind")
        return (
            f"Bot meminta persetujuan sebelum lanjut{(' (' + action + ')') if action else ''}.",
            None,
        )
    if event_type == "approval_granted":
        return "Persetujuan diberikan, bot boleh lanjut.", None
    if event_type == "approval_rejected":
        return f"Persetujuan ditolak{because}; bot tidak lanjut.", None
    if event_type == "user_message":
        return "Pengguna mengirim pesan ke bot.", None
    if event_type == "agent_message":
        text = _first_text(content, "text", "message")
        return (
            "Bot menjawab pengguna.",
            _short(text, MAX_RESULT_CHARS) if text else None,
        )
    if event_type == "budget_exhausted":
        return "Anggaran tugas habis, bot berhenti bekerja.", None
    if event_type == "task_completed":
        summary = _first_text(content, "summary", "result")
        return (
            "Tugas selesai.",
            _short(summary, MAX_RESULT_CHARS) if summary else None,
        )
    if event_type == "task_failed":
        error = _first_text(content, "error", "reason", "message")
        return (
            "Tugas gagal.",
            _short(error, MAX_RESULT_CHARS) if error else None,
        )
    if event_type == "task_stopped":
        return f"Tugas dihentikan{because}.", None
    if event_type == "workflow_proposed":
        return "Bot mengusulkan alur kerja berulang untuk tugas seperti ini.", None
    if event_type == "workflow_activated":
        return "Alur kerja diaktifkan: tugas serupa akan berjalan otomatis.", None
    if event_type == "workflow_paused":
        return "Alur kerja dijeda.", None
    if event_type == "trigger_fired":
        return "Pemicu berbunyi: kondisi yang diawasi terpenuhi.", None
    if event_type == "trigger_missed":
        return "Pemicu terlewat: kondisi terpenuhi tapi tidak sempat dijalankan.", None
    if event_type == "module_proposed":
        return "Bot mengusulkan modul baru untuk tugas seperti ini.", None
    if event_type == "module_activated":
        return "Modul diaktifkan untuk tugas ini.", None
    if event_type == "false_report":
        return "Bot menandai laporan yang ternyata salah.", None
    if event_type == "data_consent":
        return "Pengguna memberi atau mengubah izin pemakaian data.", None
    if event_type == "takeover_started":
        return "Pengguna mengambil alih bot: kerja otonom dijeda.", None
    if event_type == "takeover_released":
        return "Pengguna melepas kendali: bot lanjut bekerja.", None
    return f"Bot mencatat langkah {event_type}.", None


@dataclass
class ReplayStep:
    """One step of a replay: one ledger event, played like a frame.

    ``reason`` says why the step happened and ``result`` what
    came of it (None when the step has no outcome yet).
    ``screenshot`` stays None: no backend can produce a real
    frame yet (see the module docstring); the GIF export draws
    a rendered frame from this step's own data instead.
    """

    index: int
    event_id: str
    type: str
    task_id: str
    agent_id: str
    subagent_id: str | None
    created_at: str
    tool: str | None
    reason: str
    result: str | None
    content: dict = field(default_factory=dict)
    hash: str = ""
    previous_hash: str = ""
    screenshot: str | None = None

    def to_dict(self) -> dict:
        """JSON-safe dict of this step."""
        return {
            "index": self.index,
            "event_id": self.event_id,
            "type": self.type,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "subagent_id": self.subagent_id,
            "created_at": self.created_at,
            "tool": self.tool,
            "reason": self.reason,
            "result": self.result,
            "content": self.content,
            "hash": self.hash,
            "previous_hash": self.previous_hash,
            "screenshot": self.screenshot,
        }


@dataclass
class ReplayTimeline:
    """A whole task, replayable step by step.

    ``status`` is ``completed`` / ``failed`` / ``stopped`` when
    the last event ends the task, else ``running`` (the task may
    still be going, or may have gone quiet — the timeline shows
    the last event time, never claims the bot is still working).
    ``chain_ok`` is False with the broken event ids when the
    hash chain does not verify: the replay still plays, but the
    break is shown, not hidden.
    """

    agent_id: str
    task_id: str
    steps: list[ReplayStep] = field(default_factory=list)
    status: str = "running"
    chain_ok: bool = True
    broken_event_ids: list[str] = field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    generated_at: str = ""

    @property
    def step_count(self) -> int:
        """How many steps the timeline holds."""
        return len(self.steps)

    def step(self, index: int) -> ReplayStep:
        """One step by index (0-based); raises 404 out of range."""
        if not isinstance(index, int) or isinstance(index, bool):
            raise ReplayError("step index must be an integer", status=400)
        if index < 0 or index >= len(self.steps):
            raise ReplayError(
                f"step {index} does not exist in this replay (0..{len(self.steps) - 1})",
                status=404,
            )
        return self.steps[index]

    def to_dict(self) -> dict:
        """JSON-safe dict of this timeline."""
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "step_count": self.step_count,
            "status": self.status,
            "chain_ok": self.chain_ok,
            "broken_event_ids": self.broken_event_ids,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "generated_at": self.generated_at,
            "steps": [step.to_dict() for step in self.steps],
        }


def _step_from_event(index: int, event: dict) -> ReplayStep:
    """One ledger event -> one replay step (oldest first)."""
    content = event.get("content") or {}
    reason, result = _describe(event.get("type", ""), content)
    return ReplayStep(
        index=index,
        event_id=str(event.get("id", "")),
        type=event.get("type", ""),
        task_id=event.get("task_id", ""),
        agent_id=event.get("agent_id", ""),
        subagent_id=event.get("subagent_id"),
        created_at=_event_time_iso(event),
        tool=_tool(content) or None,
        reason=reason,
        result=result,
        content=content,
        hash=event.get("hash", ""),
        previous_hash=event.get("previous_hash", ""),
        screenshot=None,
    )


def build_replay(
    user_id: str,
    agent_id: str,
    task_id: str,
    *,
    dsn: str | None = None,
    ledger: LedgerType | None = None,
    limit: int = MAX_REPLAY_STEPS,
    now: datetime | None = None,
) -> ReplayTimeline:
    """The replay timeline for one task: every step, in order.

    Reads the task's ledger events (oldest first), derives each
    step's reason and result from the event, and verifies the
    hash chain. Raises :class:`ReplayError` (403) when the agent
    is not the user's, (404) when the task has no events or its
    events belong to another agent, (400) when the task has more
    steps than ``limit``.
    """
    resolved_dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    _require_owner(user_id, agent_id, dsn=resolved_dsn)
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id must be a non-empty string")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    ledger = ledger if ledger is not None else Ledger(resolved_dsn)
    events = ledger.events(task_id)
    if not events:
        raise ReplayError(f"task {task_id!r} has no recorded events", status=404)
    if any(event.get("agent_id") != agent_id for event in events):
        # A task of another agent reads as unknown, never as
        # someone else's steps.
        raise ReplayError(f"task {task_id!r} has no recorded events", status=404)
    if len(events) > limit:
        raise ReplayError(
            f"task {task_id!r} has {len(events)} steps, over the {limit}-step replay limit",
            status=400,
        )
    ok, broken = ledger.verify_chain(task_id)
    steps = [_step_from_event(index, event) for index, event in enumerate(events)]
    last_type = events[-1].get("type", "")
    return ReplayTimeline(
        agent_id=agent_id,
        task_id=task_id,
        steps=steps,
        status=TERMINAL_TASK_STATUS.get(last_type, "running"),
        chain_ok=ok,
        broken_event_ids=broken,
        started_at=steps[0].created_at,
        ended_at=steps[-1].created_at,
        generated_at=(now or datetime.now(UTC)).isoformat(),
    )


def list_tasks_for_agent(
    user_id: str,
    agent_id: str,
    *,
    dsn: str | None = None,
) -> list[dict]:
    """The agent's tasks that have ledger events, newest first.

    One row per task: the step count, first/last event time, and
    the status from the newest event. Read-only; raises
    :class:`ReplayError` (403) when the agent is not the user's.
    """
    resolved_dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    _require_owner(user_id, agent_id, dsn=resolved_dsn)
    with psycopg.connect(resolved_dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT task_id, COUNT(*) AS event_count, "
            "MIN(created_at) AS first_event_at, MAX(created_at) AS last_event_at, "
            "(array_agg(type ORDER BY created_at DESC, id DESC))[1] AS last_type "
            "FROM events WHERE agent_id = %s "
            "GROUP BY task_id ORDER BY MAX(created_at) DESC",
            (agent_id,),
        )
        rows = cur.fetchall()
    tasks = []
    for row in rows:
        last_type = row["last_type"] or ""
        tasks.append(
            {
                "task_id": row["task_id"],
                "event_count": row["event_count"],
                "first_event_at": _event_time_iso({"created_at": row["first_event_at"]}),
                "last_event_at": _event_time_iso({"created_at": row["last_event_at"]}),
                "status": TERMINAL_TASK_STATUS.get(last_type, "running"),
            }
        )
    return tasks


# -- rendered frames + GIF export -------------------------------------------


def _wrap_lines(text: str, *, width: int, max_lines: int) -> list[str]:
    """Word-wrapped lines of ``text``, clipped to ``max_lines``."""
    lines = textwrap.wrap(text, width=width) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def render_step_frame(
    step: ReplayStep,
    *,
    total_steps: int,
    width: int = GIF_FRAME_WIDTH,
    height: int = GIF_FRAME_HEIGHT,
) -> Any:
    """One step as a deterministic rendered frame (a PIL image).

    A plain picture drawn from the step's own data — step
    number, event type, time, tool, reason, result — on a fixed
    canvas with the default bitmap font. NOT a desktop
    screenshot: the frame says so in its footer. Deterministic:
    the same step always draws the same pixels.
    """
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default()
    dark = (30, 41, 59)
    header_bg = (30, 58, 95)
    white = (255, 255, 255)
    muted = (100, 116, 139)
    body = (15, 23, 42)
    image = Image.new("RGB", (width, height), white)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 30], fill=header_bg)
    draw.text((12, 10), "PUSPA REPLAY", fill=white, font=font)
    counter = f"Step {step.index + 1}/{total_steps}"
    draw.text(
        (width - 12 - draw.textlength(counter, font=font), 10), counter, fill=white, font=font
    )
    draw.text((12, 40), step.type, fill=dark, font=font)
    draw.text((12, 58), step.created_at, fill=muted, font=font)
    draw.text((12, 76), f"Tool: {step.tool or '-'}", fill=body, font=font)
    y = 100
    draw.text((12, y), "Why:", fill=muted, font=font)
    for line in _wrap_lines(step.reason, width=62, max_lines=4):
        y += 14
        draw.text((12, y), line, fill=body, font=font)
    y += 22
    draw.text((12, y), "Result:", fill=muted, font=font)
    for line in _wrap_lines(step.result or "(no result recorded yet)", width=62, max_lines=4):
        y += 14
        draw.text((12, y), line, fill=body, font=font)
    draw.text(
        (12, height - 18),
        "Rendered frame from ledger data - not a desktop screenshot.",
        fill=muted,
        font=font,
    )
    return image


def export_replay_gif(timeline: ReplayTimeline) -> bytes:
    """The whole timeline as an animated GIF (rendered frames).

    One frame per step, ``GIF_FRAME_DURATION_MS`` each, looping
    forever. Deterministic: the same timeline always exports
    the same bytes. Raises :class:`ReplayError` (404) when the
    timeline has no steps.
    """
    if not timeline.steps:
        raise ReplayError("cannot export a GIF with no steps", status=404)
    frames = [render_step_frame(step, total_steps=timeline.step_count) for step in timeline.steps]
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=GIF_FRAME_DURATION_MS,
        loop=0,
    )
    return buffer.getvalue()
