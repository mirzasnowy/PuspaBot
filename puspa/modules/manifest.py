"""Manifes modul kustom (§2.13 revisi 7).

Modul = alur + tampilan + data + notif, ditulis sebagai SATU manifes
deklaratif. Manifes adalah DATA, bukan program: tidak ada HTML,
JavaScript, atau SQL dari bot.

Gerbang manifes (SEMUA oleh kode):
1. validasi skema,
2. pemindai komponen dan tipe yang diizinkan,
3. manifes izin turunan dari blok yang dipakai,
4. uji kering dengan data contoh,
5. batas ukuran.
Manifes yang gagal di salah satunya tidak bisa diaktifkan, dan
alasannya ditampilkan.
"""

from __future__ import annotations

import json
import os

import psycopg
from psycopg.rows import dict_row

from puspa.workflow.blocks import validate_workflow
from puspa.workflow.manifest import compute_manifest, subset_permission
from puspa.workflow.triggers import validate_trigger

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

ALLOWED_COMPONENTS = ("card", "table", "items", "form", "number", "line_chart", "action_button")
COLUMN_TYPES = ("text", "number", "Rupiah", "date", "choice")
MAX_VIEWS = 3
MAX_TABLES = 5
MAX_COLUMNS_PER_TABLE = 10
MAX_MANIFEST_BYTES = 65536
MAX_ACTIVE_MODULES = 3

# Pola yang menandakan kode (bukan data) di dalam manifes. Manifes yang
# memuatnya DITOLAK di gerbang 2 — bukan dibersihkan diam-diam.
FORBIDDEN_CODE_PATTERNS = (
    "<script",
    "</script",
    "<iframe",
    "javascript:",
    "onerror=",
    "onclick=",
    "SELECT ",
    "INSERT ",
    "UPDATE ",
    "DELETE ",
    "DROP ",
    "--",
    "/*",
)


class ModuleError(Exception):
    """Manifes modul tidak lolos gerbang, atau modul tidak ditemukan."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row
    )


def _row(row: dict) -> dict:
    row = dict(row)
    for k in ("id", "user_id"):
        if k in row and row[k] is not None:
            row[k] = str(row[k])
    return row


# ---------------------------------------------------------------- gerbang


def schema_gate(manifest: dict) -> list[str]:
    """Gerbang 1: validasi skema."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifes harus objek"]
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        problems.append("manifes: 'nama' wajib teks tak kosong")
    views = manifest.get("views")
    if not isinstance(views, list) or not views:
        problems.append("manifes: 'tampilan' wajib daftar tak kosong")
    table = manifest.get("table", [])
    if not isinstance(table, list):
        problems.append("manifes: 'tabel' harus daftar")
    else:
        for t in table:
            if not isinstance(t, dict) or not t.get("name"):
                problems.append("manifes: tiap tabel wajib punya 'nama'")
                continue
            columns = t.get("columns")
            if not isinstance(columns, list) or not columns:
                problems.append(f"tabel {t['name']!r}: 'kolom' wajib daftar tak kosong")
                continue
            for k in columns:
                if not isinstance(k, dict) or not k.get("name") or not k.get("type"):
                    problems.append(f"tabel {t['name']!r}: tiap kolom wajib 'nama' + 'tipe'")
    workflow = manifest.get("workflows")
    if workflow is not None:
        if not isinstance(workflow, dict):
            problems.append("manifes: 'alur' harus objek")
        else:
            problems += validate_trigger(workflow.get("triggers", {}))
            problems += validate_workflow(workflow.get("step", []))
    notif = manifest.get("notifications", [])
    if not isinstance(notif, list):
        problems.append("manifes: 'notif' harus daftar")
    else:
        for n in notif:
            if not isinstance(n, dict) or not n.get("condition") or not n.get("message"):
                problems.append("manifes: tiap aturan notif wajib 'kondisi' + 'pesan'")
    example = manifest.get("example_data", {})
    if not isinstance(example, dict):
        problems.append("manifes: 'example_data' harus objek")
    return problems


def scanner_gate(manifest: dict) -> list[str]:
    """Gerbang 2: pemindai komponen dan tipe yang diizinkan + larangan kode."""
    problems: list[str] = []
    for t in manifest.get("views", []) or []:
        if not isinstance(t, dict):
            problems.append("tampilan: tiap tampilan harus objek")
            continue
        if t.get("type") not in ALLOWED_COMPONENTS:
            problems.append(
                f"tampilan: komponen {t.get('type')!r} tidak diizinkan "
                f"(yang boleh: {', '.join(ALLOWED_COMPONENTS)})"
            )
    for t in manifest.get("table", []) or []:
        for k in t.get("columns") or []:
            if k.get("type") not in COLUMN_TYPES:
                problems.append(
                    f"tabel {t.get('name')!r}: tipe kolom {k.get('type')!r} "
                    f"tidak diizinkan (yang boleh: {', '.join(COLUMN_TYPES)})"
                )
            if k.get("type") == "choice" and not isinstance(k.get("choice"), list):
                problems.append(
                    f"tabel {t.get('name')!r} kolom {k.get('name')!r}: "
                    "tipe 'pilihan' wajib punya daftar 'pilihan'"
                )
    # Pindai seluruh manifes (sebagai JSON) untuk pola kode.
    text = json.dumps(manifest, ensure_ascii=False)
    for pattern in FORBIDDEN_CODE_PATTERNS:
        if pattern.lower() in text.lower():
            problems.append(f"manifes memuat kode yang dilarang: {pattern!r}")
            break
    return problems


def permission_gate(manifest: dict) -> dict:
    """Gerbang 3: manifes izin turunan dari blok yang dipakai di alur
    modul. Modul tanpa alur -> izin kosong."""
    workflow = manifest.get("workflows")
    if not workflow:
        return {"connectors": [], "scope": [], "domain": [], "spending_limit": 0, "block": []}
    return compute_manifest(workflow.get("step", []))


def dry_run_gate(manifest: dict, dsn=None) -> list[str]:
    """Gerbang 4: uji kering dengan data contoh — alur modul dijalankan
    mode kering (bila ada), dan tiap tampilan dirender dengan data
    contoh. Masalah = alasan gagal."""
    from puspa.modules.views import render_module

    problems: list[str] = []
    workflow = manifest.get("workflows")
    if workflow:
        from puspa.workflow.runner import WorkflowError  # noqa: F401
        # Uji kering alur butuh baris alur di DB — dilakukan di
        # aktifkan_modul (setelah modul dibuat). Di sini: periksa
        # langkah sudah lolos gerbang 1; jalan kering penuh dicatat
        # di sana. Jadi gerbang 4 di level manifes = render tampilan.
    try:
        render_module(manifest)
    except Exception as exc:
        problems.append(f"pratinjau gagal dirender dengan data contoh: {exc}")
    return problems


def size_gate(manifest: dict) -> list[str]:
    """Gerbang 5: batas ukuran."""
    problems: list[str] = []
    if len(manifest.get("views", []) or []) > MAX_VIEWS:
        problems.append(f"tampilan {len(manifest['views'])} — maks {MAX_VIEWS} per modul")
    if len(manifest.get("table", []) or []) > MAX_TABLES:
        problems.append(f"tabel {len(manifest['table'])} — maks {MAX_TABLES} per modul")
    for t in manifest.get("table", []) or []:
        if len(t.get("columns", []) or []) > MAX_COLUMNS_PER_TABLE:
            problems.append(
                f"tabel {t.get('name')!r}: {len(t['columns'])} kolom — maks {MAX_COLUMNS_PER_TABLE}"
            )
    size = len(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
    if size > MAX_MANIFEST_BYTES:
        problems.append(f"manifes {size} byte — maks {MAX_MANIFEST_BYTES}")
    workflow = manifest.get("workflows")
    if workflow and len(workflow.get("step", []) or []) > 10:
        problems.append("alur modul: maks 10 langkah")
    return problems


def run_gates(manifest: dict, dsn=None) -> dict:
    """Jalankan kelima gerbang. Kembalikan {lolos, masalah, manifes_izin}."""
    problems = schema_gate(manifest)
    problems += scanner_gate(manifest)
    problems += size_gate(manifest)
    problems += dry_run_gate(manifest, dsn=dsn)
    try:
        permission_manifest = permission_gate(manifest)
    except Exception as exc:
        problems.append(f"manifes izin turunan gagal dihitung: {exc}")
        permission_manifest = {
            "connectors": [],
            "scope": [],
            "domain": [],
            "spending_limit": 0,
            "block": [],
        }
    return {"passed": not problems, "masalah": problems, "permission_manifest": permission_manifest}


# ---------------------------------------------------------------- CRUD


def create_module(*, user_id: str, manifest: dict, dsn=None) -> dict:
    """Buat modul draf (versi 1). Gerbang 1-2-5 harus lolos saat dibuat;
    gerbang 3-4 dicek penuh saat aktivasi."""
    result = run_gates(manifest, dsn=dsn)
    if result["masalah"]:
        raise ModuleError("manifes tidak lolos gerbang: " + "; ".join(result["masalah"]))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO modules (user_id, name, manifest, status)"
                " VALUES (%s, %s, %s, 'draft') RETURNING *",
                (user_id, manifest["name"], json.dumps(manifest)),
            )
            row = dict(cur.fetchone())
            cur.execute(
                "INSERT INTO module_versions (module_id, versi, manifest) VALUES (%s, 1, %s)",
                (row["id"], json.dumps(manifest)),
            )
            # Buat tabel milik modul dari manifes.
            from puspa.modules.tables import create_table

            for t in manifest.get("table", []) or []:
                create_table(
                    module_id=str(row["id"]),
                    user_id=user_id,
                    name=t["name"],
                    columns=t["columns"],
                    dsn=dsn,
                    _conn=conn,
                )
        conn.commit()
    return _row(row)


def get_module(module_id: str, *, user_id: str, dsn=None) -> dict | None:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM modules WHERE id = %s AND user_id = %s", (module_id, user_id))
        row = cur.fetchone()
    return _row(row) if row else None


def list_modules(user_id: str, *, status: str | None = None, dsn=None) -> list[dict]:
    with _connect(dsn) as conn, conn.cursor() as cur:
        if status:
            cur.execute(
                "SELECT * FROM modules WHERE user_id = %s AND status = %s ORDER BY created_at",
                (user_id, status),
            )
        else:
            cur.execute("SELECT * FROM modules WHERE user_id = %s ORDER BY created_at", (user_id,))
        return [_row(r) for r in cur.fetchall()]


def activate_module(module_id: str, *, user_id: str, permission_user: dict, dsn=None) -> dict:
    """Aktifkan modul: kelima gerbang + izin turunan himpunan bagian
    dari izin user + maks 3 modul aktif + uji kering alur modul
    (bila ada) dengan data contoh."""
    module = get_module(module_id, user_id=user_id, dsn=dsn)
    if module is None:
        raise ModuleError("modul tidak ditemukan")
    if module["status"] == "deleted":
        raise ModuleError("modul sudah dihapus")
    result = run_gates(module["manifest"], dsn=dsn)
    if not result["passed"]:
        raise ModuleError("manifes tidak lolos gerbang: " + "; ".join(result["masalah"]))
    passed, less = subset_permission(result["permission_manifest"], permission_user)
    if not passed:
        raise ModuleError("manifes izin di luar izin user: " + "; ".join(less))
    active = [
        m for m in list_modules(user_id, status="active", dsn=dsn) if str(m["id"]) != str(module_id)
    ]
    if len(active) >= MAX_ACTIVE_MODULES:
        raise ModuleError(f"sudah ada {MAX_ACTIVE_MODULES} modul aktif — maks di MVP")
    # Gerbang 4 (jalan): uji kering alur modul bila ada.
    workflow = module["manifest"].get("workflows")
    if workflow:
        from puspa.workflow.runner import create_workflow, dry_run

        temp_workflow = create_workflow(
            user_id=user_id,
            agent_id="puspa",
            name=f"uji kering modul {module['name']}",
            trigger=workflow.get("triggers", {"type": "manual"}),
            step=workflow.get("step", []),
            dsn=dsn,
        )
        try:
            dry_run_result = dry_run(str(temp_workflow["id"]), user_id=user_id, dsn=dsn)
        finally:
            from puspa.workflow.runner import delete_workflow

            delete_workflow(str(temp_workflow["id"]), user_id=user_id, dsn=dsn)
        if not dry_run_result.get("success"):
            raise ModuleError(
                "uji kering alur modul gagal: " + (dry_run_result.get("reason") or "")
            )
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE modules SET status = 'active', updated_at = now()"
                " WHERE id = %s RETURNING *",
                (module_id,),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return _row(row)


def change_module(module_id: str, *, user_id: str, manifest: dict, dsn=None) -> dict:
    """Ubah manifes (hanya lewat sesi penyusunan user). Versi baru;
    gerbang 1-2-5 dicek ulang, gerbang 3-4 saat aktivasi ulang."""
    module = get_module(module_id, user_id=user_id, dsn=dsn)
    if module is None:
        raise ModuleError("modul tidak ditemukan")
    if module["status"] == "deleted":
        raise ModuleError("modul sudah dihapus")
    result = run_gates(manifest, dsn=dsn)
    if result["masalah"]:
        raise ModuleError("manifes tidak lolos gerbang: " + "; ".join(result["masalah"]))
    new_version = int(module["active_version"]) + 1
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE modules SET manifest = %s, active_version = %s, status = 'draft',"
                " updated_at = now() WHERE id = %s RETURNING *",
                (json.dumps(manifest), new_version, module_id),
            )
            row = dict(cur.fetchone())
            cur.execute(
                "INSERT INTO module_versions (module_id, versi, manifest) VALUES (%s, %s, %s)",
                (module_id, new_version, json.dumps(manifest)),
            )
        conn.commit()
    return _row(row)


def rollback_module(module_id: str, *, user_id: str, version: int, dsn=None) -> dict:
    """Pulihkan versi lama (menjadi versi baru)."""
    module = get_module(module_id, user_id=user_id, dsn=dsn)
    if module is None:
        raise ModuleError("modul tidak ditemukan")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT manifest FROM module_versions WHERE module_id = %s AND versi = %s",
            (module_id, version),
        )
        old = cur.fetchone()
        if old is None:
            raise ModuleError(f"versi {version} tidak ada")
    return change_module(module_id, user_id=user_id, manifest=old["manifest"], dsn=dsn)


def export_data_module(module_id: str, *, user_id: str, dsn=None) -> dict:
    """Ekspor seluruh data tabel milik modul (JSON) — ditawarkan
    sebelum hapus (§2.13)."""
    from puspa.modules.tables import list_tables, read_table

    module = get_module(module_id, user_id=user_id, dsn=dsn)
    if module is None:
        raise ModuleError("modul tidak ditemukan")
    export = {"modules": module["name"], "table": {}}
    for t in list_tables(module_id, user_id=user_id, dsn=dsn):
        export["table"][t["name"]] = read_table(t["id"], user_id=user_id, dsn=dsn)
    return export


def delete_module(module_id: str, *, user_id: str, dsn=None) -> dict:
    """Hapus modul. Kembalikan ekspor datanya dulu (pemanggil
    menampilkannya ke user sebelum benar-benar menghapus — di sini
    ekspor dikembalikan bersama hasil hapus)."""
    export = export_data_module(module_id, user_id=user_id, dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE modules SET status = 'deleted', updated_at = now()"
                " WHERE id = %s AND user_id = %s RETURNING *",
                (module_id, user_id),
            )
            row = cur.fetchone()
            cur.execute("DELETE FROM module_rows WHERE module_id = %s", (module_id,))
            cur.execute("DELETE FROM module_tables WHERE module_id = %s", (module_id,))
        conn.commit()
    return {"deleted": _row(dict(row)) if row else None, "export": export}
