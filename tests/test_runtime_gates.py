"""Tes pengukuran gerbang 11 + 12 (parsial, yang bisa diukur di Fase 2).

Gerbang 11 (agen + sub-agen): buat agen ke-2 dari PWA, pola 3 Pembaca
+ 1 Pemeriksa, Pemeriksa tangkap harga salah 3/3, handoff macet
dilaporkan < 2 menit, Puspa tidak bisa dihapus, token di luar
ditolak 3/3, handoff melingkar ditolak, Hentikan < 5 detik.
Gerbang 12 (tulang punggung + bukti): rantai hash yang diubah
terdeteksi 3/3, angka yatim ditahan 3/3, kaset 9/10 identik.
"""

import psycopg
import pytest

from puspa.evidence.renderer import sajikan
from puspa.evidence.verifier import periksa_suntikan
from puspa.evidence.evidence import record_evidence
from puspa.runtime.handoff import tolak_lingkaran, HandoffError
from puspa.runtime.subagents import SubagentPool, SubagentError


def test_gerbang11_pemeriksa_tangkap_suntikan_3_dari_3():
    def reread(url):
        return {"value": 12500000, "name": "Laptop X"}
    for value_wrong in (9999999, 15000000, 100000):
        b = record_evidence(task_id="t1", klaim="Laptop X", value=value_wrong,
                        unit="Rp", sumber_url="https://tokopedia.com/produk/laptop-x",
                        text_excerpt="x", reader_id="sub-1")
        assert periksa_suntikan(b, value_wrong=value_wrong, reread=reread) != "Verified"


def test_gerbang11_token_di_luar_rejected_3_dari_3(token_payloads):
    kolam = SubagentPool()
    for tools in (["delete_all"], ["read", "send_without_permission"], ["pay"]):
        bukan_subset = dict(token_payloads["sub"], tools=tools)
        with pytest.raises(SubagentError):
            kolam.spawn(agent_id="agen-1", task_id="t1", permission_class="Pelaksana",
                        token_agent=token_payloads["agents"], token_subagen=bukan_subset)


def test_gerbang11_handoff_melingkar_rejected():
    with pytest.raises(HandoffError):
        tolak_lingkaran(["puspa", "agen-riset"], "agen-riset", "puspa")


def test_gerbang12_rantai_hash_diubah_terdeteksi_3_dari_3(ledger, make_user):
    u = make_user()
    for i in range(3):
        task_id = f"tugas-rantai-{i}"
        ledger.append_event(task_id=task_id, agent_id=u["agent_id"],
                            jenis="task_created", content={"purpose": "test"})
        ledger.append_event(task_id=task_id, agent_id=u["agent_id"],
                            jenis="task_completed", content={})
        ok_sebelum, _ = ledger.verify_chain(task_id)
        assert ok_sebelum is True
        # Ubah diam-diam satu hash (sebagai pemilik DB; nilai unik per tugas).
        with psycopg.connect(ledger.dsn) as conn:
            conn.execute(
                "UPDATE events SET hash = %s WHERE task_id = %s AND type = 'task_created'",
                (f"00diubah{i}", task_id,))
            conn.commit()
        ok_sesudah, _ = ledger.verify_chain(task_id)
        assert ok_sesudah is False


def test_gerbang12_number_yatim_ditahan_3_dari_3():
    for text in ("Harganya Rp9.999.000.", "Tagihan Rp132.500 jatuh tempo 20 Nov.",
                 "Selisihnya 15% lebih murah."):
        result = sajikan(text, {})
        assert result["ditahan"] is True and result["dibuang"]
