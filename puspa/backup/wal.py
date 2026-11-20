"""WAL archiving for point-in-time recovery (E-09, plan section 5.4).

Gate 15 requires a restore to a clean host within 30 minutes. A nightly
``pg_dump`` alone cannot do that for data written after the dump: the
missing piece is continuous WAL archiving, so a base backup plus the
archived WAL segments replay the database to any point in time.

Supported backends:

- ``pgbackrest`` (production default): base backups and WAL archiving
  to encrypted object storage outside the VPS. The stanza config is
  rendered by :func:`pgbackrest_config`.
- ``wal-g``: same role, driven by environment variables rendered by
  :func:`walg_env`.
- ``local``: plain ``cp`` into a directory. Only for development and
  for the restore drill on this host: it proves the archive/replay
  path, but it is not off-host storage and must never be the
  production backend.

This module never touches the live cluster configuration itself: it
renders the configuration and inspects archiving health through
``pg_stat_archiver``. Applying the config on the VPS is the runbook's
job (``docs/backup-restore-runbook.md``).
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

from ..memory.schema import resolve_dsn

#: WAL segment file names are 24 uppercase hex characters:
#: 8 timeline + 8 log id + 8 segment number.
_SEGMENT_RE = re.compile(r"^[0-9A-F]{24}$")

#: Segments per WAL log file with the default 16 MB segment size.
_SEGMENTS_PER_LOG = 256


class WalError(Exception):
    """WAL archiving configuration or verification failed."""


def archive_command(backend: str, *, stanza: str = "puspa", archive_dir=None) -> str:
    """Return the ``archive_command`` for ``backend``.

    ``%p`` is the WAL file path and ``%f`` its file name; Postgres
    substitutes both. The local backend guards against re-archiving
    an already present segment (``test ! -f``), which is what makes
    the command idempotent on retry.
    """
    if backend == "pgbackrest":
        return f"pgbackrest --stanza={stanza} archive-push %p"
    if backend == "wal-g":
        return "wal-g wal-push %p"
    if backend == "local":
        if archive_dir is None:
            raise WalError("local backend needs archive_dir")
        directory = Path(archive_dir)
        return f"test ! -f {directory}/%f && cp %p {directory}/%f"
    raise WalError(f"unknown WAL backend: {backend!r}")


def pgbackrest_config(
    *,
    stanza: str = "puspa",
    repo_path: str = "/var/lib/pgbackrest",
    s3_bucket: str | None = None,
    s3_endpoint: str | None = None,
    s3_region: str | None = None,
    cipher_type: str = "aes-256-cbc",
) -> str:
    """Render a pgBackRest stanza config for the Puspa cluster.

    With ``s3_bucket`` set, the repository is S3-compatible object
    storage outside the VPS (the production setup); without it, the
    repository is a local path (development only). The cipher type is
    always set: backups at rest are encrypted, and the cipher
    passphrase lives in the pgBackRest config file (mode 0600, owned
    by postgres), never in the database backup itself.
    """
    lines = [
        "[global]",
        f"repo1-cipher-type={cipher_type}",
        "repo1-retention-full=2",
        "repo1-retention-diff=7",
        "log-level-console=info",
        "log-level-file=detail",
        "",
        f"[{stanza}]",
        "pg1-path=/var/lib/postgresql/16/main",
        "pg1-port=5432",
        "pg1-user=postgres",
    ]
    if s3_bucket is not None:
        lines += [
            "repo1-type=s3",
            f"repo1-s3-bucket={s3_bucket}",
            f"repo1-s3-endpoint={s3_endpoint or ''}",
            f"repo1-s3-region={s3_region or ''}",
            "repo1-s3-key-type=auto",
        ]
    else:
        lines.append(f"repo1-path={repo_path}")
    return "\n".join(lines) + "\n"


def walg_env(
    *,
    s3_prefix: str,
    s3_endpoint: str | None = None,
    s3_region: str | None = None,
) -> dict[str, str]:
    """Return the environment wal-g needs for S3 object storage.

    Secrets (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``) are
    deliberately not part of this dict: they come from the host secret
    store, never from a rendered file.
    """
    env = {"WALG_S3_PREFIX": s3_prefix, "WALG_COMPRESSION_METHOD": "brotli"}
    if s3_endpoint is not None:
        env["AWS_ENDPOINT"] = s3_endpoint
    if s3_region is not None:
        env["AWS_REGION"] = s3_region
    return env


def check_archiving(dsn=None) -> dict:
    """Report WAL archiving health from ``pg_stat_archiver``.

    Returns a dict with ``configured`` (archive_mode is on),
    ``archived_count``, ``failed_count``, the last archived/failed
    WAL file names and timestamps, and ``healthy`` (configured and
    nothing failed). A cluster with archiving off reports
    ``configured=False`` instead of raising: the caller decides
    whether that is an error.
    """
    import psycopg

    with psycopg.connect(resolve_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute("SHOW archive_mode")
        mode_row = cur.fetchone()
        assert mode_row is not None
        archive_mode = mode_row[0]
        cur.execute("SHOW wal_level")
        level_row = cur.fetchone()
        assert level_row is not None
        wal_level = level_row[0]
        cur.execute("SHOW archive_command")
        command_row = cur.fetchone()
        assert command_row is not None
        command = command_row[0]
        cur.execute(
            "SELECT archived_count, failed_count, last_archived_wal,"
            " last_archived_time, last_failed_wal, last_failed_time"
            " FROM pg_stat_archiver"
        )
        row = cur.fetchone()
    assert row is not None
    archived, failed, last_wal, last_time, failed_wal, failed_time = row
    return {
        "configured": archive_mode == "on",
        "archive_mode": archive_mode,
        "wal_level": wal_level,
        "archive_command": command,
        "archived_count": archived,
        "failed_count": failed,
        "last_archived_wal": last_wal,
        "last_archived_time": last_time.isoformat() if last_time else None,
        "last_failed_wal": failed_wal,
        "last_failed_time": failed_time.isoformat() if failed_time else None,
        "healthy": archive_mode == "on" and failed == 0,
    }


def _parse_segment(name: str) -> tuple[int, int, int]:
    return (int(name[0:8], 16), int(name[8:16], 16), int(name[16:24], 16))


def _next_segment(name: str) -> str:
    timeline, log, segment = _parse_segment(name)
    segment += 1
    if segment >= _SEGMENTS_PER_LOG:
        segment = 0
        log += 1
    return f"{timeline:08X}{log:08X}{segment:08X}"


def verify_wal_chain(archive_dir) -> dict:
    """Check an archived WAL directory for continuity gaps.

    Lists the 24-hex segment files, sorts them, and walks the
    expected successor of each one. Any missing successor is a gap:
    point-in-time recovery cannot replay past it. ``.backup`` files
    (written by ``pg_basebackup``) are reported separately and do not
    count as segments.
    """
    directory = Path(archive_dir)
    if not directory.exists():
        raise WalError(f"WAL archive directory does not exist: {directory}")
    segments = sorted(p.name for p in directory.iterdir() if _SEGMENT_RE.match(p.name))
    backup_files = sorted(p.name for p in directory.iterdir() if p.name.endswith(".backup"))
    gaps = []
    for current, following in itertools.pairwise(segments):
        # A timeline switch (promotion after recovery) starts a new
        # sequence; it is a boundary, not a gap.
        if _parse_segment(following)[0] != _parse_segment(current)[0]:
            continue
        if _next_segment(current) != following:
            gaps.append({"after": current, "expected": _next_segment(current)})
    return {
        "archive_dir": str(directory),
        "segments": segments,
        "segment_count": len(segments),
        "backup_files": backup_files,
        "gaps": gaps,
        "ok": not gaps and len(segments) > 0,
    }
