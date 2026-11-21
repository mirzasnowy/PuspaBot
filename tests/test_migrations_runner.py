"""Migration runner: the versions-directory hook.

The runner reads ``puspa/migrations/versions/`` by default; the
hosted service keeps its own full set elsewhere and points the
runner at it with ``directory=``. These tests prove the hook works
without touching the live schema: listing reads only the given
directory, and applying from the public directory is a no-op on a
database that already has every version.
"""

from __future__ import annotations

from pathlib import Path

from puspa.migrations import apply_migrations, list_migrations

_MIGRATION = """-- migrate:up
CREATE TABLE IF NOT EXISTS hook_probe_{name} (id INT PRIMARY KEY);
-- migrate:down
DROP TABLE IF EXISTS hook_probe_{name};
"""


def _write_migration(directory: Path, version: str, name: str) -> None:
    (directory / f"{version}_{name}.sql").write_text(_MIGRATION.format(name=name), encoding="utf-8")


def test_list_migrations_reads_the_given_directory(tmp_path):
    _write_migration(tmp_path, "0001", "first")
    _write_migration(tmp_path, "0002", "second")
    found = list_migrations(tmp_path)
    assert [m.version for m in found] == ["0001", "0002"]
    assert [m.name for m in found] == ["0001_first", "0002_second"]


def test_apply_migrations_from_the_public_directory_is_a_noop(test_dsn):
    versions = Path(__file__).resolve().parents[1] / "puspa" / "migrations" / "versions"
    assert versions.is_dir()
    assert apply_migrations(test_dsn, directory=versions) == []
