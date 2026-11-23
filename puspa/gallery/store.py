"""Community gallery store (B16: publish + rate cultural works).

One row per published work in ``gallery_works``: the owner, the
B7 mockup it came from, and a snapshot of the design doc with the
version that was published, so a rating is always stamped against
the version the rater actually saw. One row per user per work in
``gallery_ratings``: 1-5 stars plus a short written review.

Rules, all enforced here so the API layer cannot forget them:

- A work flagged ``restricted_customary_use`` is never stored:
  publish refuses it outright, so a restricted work can never
  appear in the gallery as a generic pattern.
- One rating per user per work: a second rating UPDATES the first
  row (new score, new review, new work version), it never creates
  a second row. The primary key ``(work_id, user_id)`` backs this
  in the database as well.
- No anonymous ratings: the store takes a real ``user_id`` and the
  API layer rejects anonymous callers before it gets here.
- No self-ratings: an owner rating their own work would inflate
  the score, so it is refused.
- Rating spam is rate-limited twice: a per-user hourly cap on
  rating submissions, and a per-user-per-work cooldown between
  submissions. Both limits are parameters so tests can use small
  values without waiting an hour.
- The aggregate is always a pair: ``average_rating`` together
  with ``rating_count``, never a score on its own. With no
  ratings yet the average is ``None`` and the count is 0.
- Unpublishing (take-down) flips the status; the row and its
  ratings stay, so re-publishing the same mockup restores the
  work with its history, and the listing only ever shows
  ``published`` works.

The schema lives in migration 0042_gallery (next to the other
migrations) and is applied the same way.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from puspa.db import connect as _db_connect
from puspa.db import dict_row

from puspa.accounts.ports import resolve_dsn
from puspa.migrations import apply_migrations
from puspa.mockup.doc import validate_doc

__all__ = [
    "GALLERY_TABLES",
    "GalleryError",
    "GalleryStore",
    "RateLimitedError",
    "create_schema",
    "reset_tables",
    "schema_path",
]

GALLERY_TABLES = ("gallery_ratings", "gallery_works")

#: Longest written review the gallery accepts (a short review, not an essay).
MAX_REVIEW_CHARS = 500

#: Default cap: at most this many rating submissions per user per rolling hour.
DEFAULT_HOURLY_LIMIT = 20

#: Default pause between two rating submissions by the same user on the same work.
DEFAULT_PER_WORK_COOLDOWN_SECONDS = 60


class GalleryError(ValueError):
    """A gallery rule refused the request (bad input, wrong owner, wrong state)."""


class RateLimitedError(GalleryError):
    """Rating spam guard tripped; the caller should back off."""

    def __init__(self, message: str, retry_after_seconds: int = 60):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def schema_path() -> Path:
    """Absolute path of migration 0042_gallery."""
    return (
        Path(__file__).resolve().parents[2]
        / "puspa"
        / "migrations"
        / "versions"
        / "0042_gallery.sql"
    )


def create_schema(dsn=None):
    """Apply the gallery tables (migration 0042_gallery). Idempotent; safe per test run."""
    apply_migrations(resolve_dsn(dsn), target="0042")


def reset_tables(dsn=None):
    """Empty the gallery tables (for tests)."""
    with _db_connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(GALLERY_TABLES) + " CASCADE")
        conn.commit()


def _average(value) -> float | None:
    if value is None:
        return None
    return round(float(value), 1)


def _summary(row: dict) -> dict:
    """One gallery work as the listing shows it: aggregate always paired with its count."""
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "mockup_id": row["mockup_id"],
        "title": row["title"],
        "template": row["template"],
        "version": row["version"],
        "status": row["status"],
        "published_at": row["published_at"].isoformat(),
        "average_rating": _average(row.get("average_rating")),
        "rating_count": int(row.get("rating_count") or 0),
    }


class GalleryStore:
    """Publish, list, rate and take down gallery works."""

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def publish(self, user_id: str, doc: dict, *, restricted_customary_use: bool = False) -> dict:
        """Publish a B7 mockup doc to the shared gallery; returns the work summary.

        The stored snapshot is the doc as published, with its version,
        so later edits to the mockup do not silently change what the
        gallery shows. A restricted-customary-use work is refused
        outright and never stored.
        """
        if restricted_customary_use:
            raise GalleryError(
                "a work with restricted customary use cannot be published as a generic pattern"
            )
        validate_doc(doc)
        work_id = f"gal-{uuid.uuid4().hex[:12]}"
        with _db_connect(self.dsn, row_factory=dict_row) as conn:
            existing = conn.execute(
                "SELECT id, owner_id, status FROM gallery_works WHERE mockup_id = %s",
                (doc["id"],),
            ).fetchone()
            if existing is not None:
                if existing["owner_id"] != user_id:
                    raise GalleryError("this mockup is already published by someone else")
                if existing["status"] == "published":
                    raise GalleryError("this mockup is already published")
                conn.execute(
                    "UPDATE gallery_works SET doc = %s::jsonb, title = %s, template = %s, "
                    "version = %s, status = 'published', published_at = now(), "
                    "updated_at = now() WHERE id = %s",
                    (
                        json.dumps(doc),
                        doc["title"],
                        doc["template"],
                        doc["version"],
                        existing["id"],
                    ),
                )
                work_id = existing["id"]
            else:
                conn.execute(
                    "INSERT INTO gallery_works (id, owner_id, mockup_id, title, template, "
                    "doc, version, restricted_customary_use) VALUES (%s, %s, %s, %s, %s, "
                    "%s::jsonb, %s, %s)",
                    (
                        work_id,
                        user_id,
                        doc["id"],
                        doc["title"],
                        doc["template"],
                        json.dumps(doc),
                        doc["version"],
                        False,
                    ),
                )
            conn.commit()
            row = conn.execute(
                "SELECT w.*, COUNT(r.user_id) AS rating_count, AVG(r.rating) AS average_rating "
                "FROM gallery_works w LEFT JOIN gallery_ratings r ON r.work_id = w.id "
                "WHERE w.id = %s GROUP BY w.id",
                (work_id,),
            ).fetchone()
            assert row is not None  # the work was just inserted or updated above
        return _summary(row)

    def list_published(self) -> list[dict]:
        """Every published work, newest first, each with its aggregate + count."""
        with _db_connect(self.dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT w.*, COUNT(r.user_id) AS rating_count, AVG(r.rating) AS average_rating "
                "FROM gallery_works w LEFT JOIN gallery_ratings r ON r.work_id = w.id "
                "WHERE w.status = 'published' GROUP BY w.id ORDER BY w.published_at DESC"
            ).fetchall()
        return [_summary(row) for row in rows]

    def get(self, work_id: str, requester_id: str) -> dict:
        """One work with its aggregate and its reviews.

        Unpublished works are visible only to their owner; everyone
        else gets a KeyError, the same as a missing work.
        """
        with _db_connect(self.dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT w.*, COUNT(r.user_id) AS rating_count, AVG(r.rating) AS average_rating "
                "FROM gallery_works w LEFT JOIN gallery_ratings r ON r.work_id = w.id "
                "WHERE w.id = %s GROUP BY w.id",
                (work_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"gallery work {work_id!r} not found")
            if row["status"] != "published" and row["owner_id"] != requester_id:
                raise KeyError(f"gallery work {work_id!r} not found")
            reviews = conn.execute(
                "SELECT user_id, rating, review, work_version, created_at FROM gallery_ratings "
                "WHERE work_id = %s ORDER BY created_at DESC LIMIT 50",
                (work_id,),
            ).fetchall()
        detail = _summary(row)
        detail["reviews"] = [
            {
                "user_id": review["user_id"],
                "rating": review["rating"],
                "review": review["review"],
                "work_version": review["work_version"],
                "created_at": review["created_at"].isoformat(),
            }
            for review in reviews
        ]
        return detail

    def snapshot(self, work_id: str) -> dict:
        """The published design doc of a work (for rendering); KeyError when unpublished."""
        with _db_connect(self.dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT doc FROM gallery_works WHERE id = %s AND status = 'published'",
                (work_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"gallery work {work_id!r} not found")
        return row["doc"]

    def rate(
        self,
        user_id: str,
        work_id: str,
        rating: int,
        review: str = "",
        *,
        hourly_limit: int = DEFAULT_HOURLY_LIMIT,
        per_work_cooldown_seconds: int = DEFAULT_PER_WORK_COOLDOWN_SECONDS,
    ) -> dict:
        """Rate a work 1-5 with a short written review; returns the rating + new aggregate.

        A second rating from the same user on the same work UPDATES
        the first one (new score, new review, stamped with the work's
        current version) instead of adding a second row, so there is
        exactly one rating per user per work.
        """
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise GalleryError("rating must be a whole number from 1 to 5")
        if not isinstance(review, str):
            raise GalleryError("review must be text")
        review = review.strip()
        if len(review) > MAX_REVIEW_CHARS:
            raise GalleryError(f"review must be at most {MAX_REVIEW_CHARS} characters")
        with _db_connect(self.dsn, row_factory=dict_row) as conn:
            work = conn.execute(
                "SELECT id, owner_id, version, status FROM gallery_works WHERE id = %s",
                (work_id,),
            ).fetchone()
            if work is None or work["status"] != "published":
                raise KeyError(f"gallery work {work_id!r} not found")
            if work["owner_id"] == user_id:
                raise GalleryError("you cannot rate your own work")
            recent = conn.execute(
                "SELECT COUNT(*) AS n FROM gallery_ratings WHERE user_id = %s "
                "AND updated_at >= now() - interval '1 hour'",
                (user_id,),
            ).fetchone()
            assert recent is not None  # COUNT(*) always returns one row
            if recent["n"] >= hourly_limit:
                raise RateLimitedError(
                    "too many ratings in the last hour; try again later",
                    retry_after_seconds=3600,
                )
            previous = conn.execute(
                "SELECT updated_at FROM gallery_ratings WHERE work_id = %s AND user_id = %s",
                (work_id, user_id),
            ).fetchone()
            if previous is not None:
                # The cooldown cutoff is computed in Python and
                # bound as a value: the Postgres form
                # `now() - (%s || ' seconds')::interval` has no
                # SQLite equivalent (the cast strips to a text
                # subtraction), and no other product code uses it.
                cooldown_since = datetime.now(UTC) - timedelta(
                    seconds=per_work_cooldown_seconds
                )
                cooling = conn.execute(
                    "SELECT COUNT(*) AS n FROM gallery_ratings WHERE work_id = %s "
                    "AND user_id = %s AND updated_at >= %s",
                    (work_id, user_id, cooldown_since),
                ).fetchone()
                assert cooling is not None  # COUNT(*) always returns one row
                if cooling["n"]:
                    raise RateLimitedError(
                        "you just rated this work; wait a moment before rating it again",
                        retry_after_seconds=per_work_cooldown_seconds,
                    )
            saved = conn.execute(
                "INSERT INTO gallery_ratings (work_id, user_id, rating, review, work_version) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (work_id, user_id) DO UPDATE SET "
                "rating = EXCLUDED.rating, review = EXCLUDED.review, "
                "work_version = EXCLUDED.work_version, updated_at = now() "
                "RETURNING user_id, rating, review, work_version, created_at, updated_at",
                (work_id, user_id, rating, review, work["version"]),
            ).fetchone()
            assert saved is not None  # INSERT ... RETURNING always returns the row
            conn.commit()
            aggregate = conn.execute(
                "SELECT COUNT(*) AS rating_count, AVG(rating) AS average_rating "
                "FROM gallery_ratings WHERE work_id = %s",
                (work_id,),
            ).fetchone()
            assert aggregate is not None  # COUNT(*) always returns one row
        return {
            "work_id": work_id,
            "user_id": saved["user_id"],
            "rating": saved["rating"],
            "review": saved["review"],
            "work_version": saved["work_version"],
            "created_at": saved["created_at"].isoformat(),
            "updated_at": saved["updated_at"].isoformat(),
            "average_rating": _average(aggregate["average_rating"]),
            "rating_count": int(aggregate["rating_count"]),
        }

    def unpublish(self, user_id: str, work_id: str) -> dict:
        """Take a work down: it disappears from the gallery listing.

        Only the owner can take their own work down; anyone else gets
        a KeyError, the same as a missing work. Ratings are kept, so
        re-publishing restores the work with its history.
        """
        with _db_connect(self.dsn) as conn:
            cursor = conn.execute(
                "UPDATE gallery_works SET status = 'unpublished', updated_at = now() "
                "WHERE id = %s AND owner_id = %s AND status = 'published'",
                (work_id, user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"gallery work {work_id!r} not found")
            conn.commit()
        return {"unpublished": work_id}
