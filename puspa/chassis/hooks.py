"""Hook run nanobot untuk Buku Kerja Puspa (definisi 2.3 dan 2.6).

``PuspaHook`` adalah subclass ``AgentHook`` nanobot — titik kait resmi
untuk kustomisasi runner ("Minimal lifecycle surface for shared runner
customization"). Hook di sini HANYA observasi: ia tidak bisa mem-veto
tool (``before_execute_tool`` tidak punya nilai kembali), jadi penegakan
izin tetap di ``PuspaTool``/``PuspaRegistry``. Yang dicatat hook:

- ``before_run`` -> ``tugas_dibuat`` (satu turn = satu tugas di fase 0;
  kalau satu tugas memicu beberapa run, kejadian ganda tidak berbahaya
  di log append-only — proyeksi memakai yang pertama).
- ``after_run`` -> ``tugas_selesai`` + ``pesan_agen`` (konten akhir,
  dipangkas 2000 karakter).
- ``on_error`` -> ``tugas_gagal`` (alasan dari konteks).

Audit tool call (``tool_dipanggil``/``tool_selesai`` + temuan injeksi)
TIDAK dicatat di sini — itu tugas ``PuspaTool`` (satu tempat, supaya
tidak ada pencatatan ganda). Aturan angka yatim (definisi 2.5,
``finalize_content``) belum ditegakkan di fase 0; itu fase 2 bersama
Pemeriksa + renderer.

Penulisan ledger bersifat best-effort (lihat ``make_ledger_sink``):
kegagalan tulis tidak boleh menjatuhkan turn. ``CompositeHook`` nanobot
juga mengisolasi eksepsi hook per-hook, jadi hook yang rusak tidak
merusak loop.

Dipasang lewat konstruktor ``AgentLoop`` (``hooks=[...]``) atau lewat
factory per-turn (``hook_factories=[...]``) — keduanya parameter resmi.
``make_puspa_hook_factory`` mengembalikan ``AgentTurnHookFactory`` yang
dipanggil ``build_agent_turn_hook`` tiap turn.
"""

from __future__ import annotations

from nanobot.agent.hook import AgentHook, AgentTurnHookContext

__all__ = ["PuspaHook", "make_puspa_hook_factory"]

_MAX_MESSAGE_CHARS = 2000


class PuspaHook(AgentHook):
    """Hook observasi: siklus tugas + pesan akhir ke Buku Kerja."""

    def __init__(self, *, task_id: str, agent_id: str, log=None) -> None:
        super().__init__()
        self.task_id = task_id
        self.agent_id = agent_id
        self._log = log

    def _log_event(self, event_type: str, content: dict) -> None:
        if self._log is None:
            return
        try:
            self._log(event_type, content)
        except Exception:
            pass

    async def before_run(self, context) -> None:
        self._log_event(
            "task_created",
            {"task_id": self.task_id, "agent_id": self.agent_id},
        )

    async def after_run(self, context) -> None:
        content = {"task_id": self.task_id, "agent_id": self.agent_id}
        final = (context.final_content or "")[:_MAX_MESSAGE_CHARS]
        if final:
            content["summaries"] = final
        self._log_event("task_completed", content)
        if final:
            self._log_event("agent_message", {"content": final})

    async def on_error(self, context) -> None:
        self._log_event(
            "task_failed",
            {
                "task_id": self.task_id,
                "agent_id": self.agent_id,
                "reason": str(context.error or context.exception or ""),
            },
        )


def make_puspa_hook_factory(task_id: str, agent_id: str, log=None):
    """Factory per-turn (``AgentTurnHookFactory``) untuk ``PuspaHook``."""

    def factory(turn_ctx: AgentTurnHookContext) -> AgentHook | None:
        return PuspaHook(task_id=task_id, agent_id=agent_id, log=log)

    return factory
