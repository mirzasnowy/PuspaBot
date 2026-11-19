"""E-03: warm pool (plan 4.3) — delete and recreate, never "clean".

The pool runs against a fake runtime that records every
create/remove, so the delete-not-clean invariant is proven
without a daemon.
"""

from __future__ import annotations

import pytest

from puspa.sandbox.warmpool import (
    ContainerRuntime,
    PoolError,
    WarmPool,
)


class FakeRuntime(ContainerRuntime):
    """A runtime that records creates/removes instead of calling Docker."""

    def __init__(self) -> None:
        self.created: list[tuple[str, list[str], list[str]]] = []
        self.removed: list[str] = []
        self.started: list[str] = []
        self._live: set[str] = set()
        self._counter = 0

    def create(self, image: str, flags: list[str], command: list[str]) -> str:
        self._counter += 1
        container_id = f"container-{self._counter}"
        self.created.append((container_id, list(flags), list(command)))
        self._live.add(container_id)
        return container_id

    def start(self, container_id: str, *, input: str, timeout_s: int) -> dict | None:
        self.started.append(container_id)
        return {
            "ok": True,
            "stdout": "warm-ok",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
        }

    def remove(self, container_id: str) -> None:
        self.removed.append(container_id)
        self._live.discard(container_id)

    def exists(self, container_id: str) -> bool:
        return container_id in self._live


def _pool(runtime=None, *, size=2, flags=None):
    runtime = runtime if runtime is not None else FakeRuntime()
    return WarmPool(
        runtime,
        image="puspa-sandbox:3.12-local",
        flags_builder=lambda: list(flags or ["--network", "none"]),
        size=size,
    )


# --- lifecycle -----------------------------------------------------------------


def test_warm_up_fills_the_pool_to_size():
    runtime = FakeRuntime()
    pool = _pool(runtime, size=3)
    pool.warm_up()
    assert pool.warm_count == 3
    assert len(runtime.created) == 3
    pool.warm_up()
    assert pool.warm_count == 3, "warm_up must not overfill"
    assert len(runtime.created) == 3


def test_acquire_hands_out_a_never_run_container():
    runtime = FakeRuntime()
    pool = _pool(runtime)
    pool.warm_up()
    slot = pool.acquire("user-a")
    assert slot.user_id == "user-a"
    assert pool.warm_count == 1
    assert pool.checked_out_count == 1
    assert runtime.exists(slot.container_id)
    assert slot.container_id not in runtime.started


def test_release_destroys_the_container_and_refills():
    runtime = FakeRuntime()
    pool = _pool(runtime, size=2)
    pool.warm_up()
    slot = pool.acquire("user-a")
    pool.release(slot)
    assert slot.container_id in runtime.removed
    assert not runtime.exists(slot.container_id)
    assert pool.warm_count == 2, "the pool refills with a fresh container"
    assert pool.checked_out_count == 0


def test_a_released_container_id_is_never_acquired_again():
    runtime = FakeRuntime()
    pool = _pool(runtime, size=1)
    pool.warm_up()
    seen: set[str] = set()
    for user in ("user-a", "user-b", "user-c"):
        slot = pool.acquire(user)
        assert slot.container_id not in seen, (
            "a container id served one user must never serve another"
        )
        seen.add(slot.container_id)
        pool.release(slot)
    assert len(seen) == 3
    assert len(runtime.removed) == 3


def test_acquire_past_warm_stock_creates_on_demand():
    runtime = FakeRuntime()
    pool = _pool(runtime, size=1)
    pool.warm_up()
    first = pool.acquire("user-a")
    second = pool.acquire("user-b")
    assert first.container_id != second.container_id
    assert pool.warm_count == 0
    pool.release(first)
    pool.release(second)
    assert pool.warm_count == 1


def test_flags_are_built_fresh_at_every_creation():
    holder = {"flag": "--network"}
    runtime = FakeRuntime()
    pool = WarmPool(
        runtime,
        image="puspa-sandbox:3.12-local",
        flags_builder=lambda: [holder["flag"], "none"],
        size=1,
    )
    pool.warm_up()
    assert runtime.created[0][1] == ["--network", "none"]
    holder["flag"] = "--cap-drop"
    slot = pool.acquire("user-a")
    pool.release(slot)  # the refill creates with the new flags
    assert runtime.created[-1][1] == ["--cap-drop", "none"]


def test_shutdown_destroys_warm_containers_but_not_checked_out_ones():
    runtime = FakeRuntime()
    pool = _pool(runtime, size=2)
    pool.warm_up()
    slot = pool.acquire("user-a")
    pool.shutdown()
    assert pool.warm_count == 0
    assert runtime.exists(slot.container_id), "a live run must not be killed"
    warm_ids = {cid for cid, _, _ in runtime.created} - {slot.container_id}
    assert warm_ids <= set(runtime.removed)
    pool.release(slot)


# --- misuse is refused --------------------------------------------------------------


def test_double_release_raises():
    pool = _pool()
    pool.warm_up()
    slot = pool.acquire("user-a")
    pool.release(slot)
    with pytest.raises(PoolError, match="already released"):
        pool.release(slot)


def test_release_by_the_wrong_user_raises():
    runtime = FakeRuntime()
    pool = _pool(runtime)
    pool.warm_up()
    slot = pool.acquire("user-a")
    with pytest.raises(PoolError, match="belongs to user 'user-a'"):
        pool.release(slot, user_id="user-b")
    assert runtime.exists(slot.container_id), "the container must survive a refused release"
    pool.release(slot, user_id="user-a")


def test_start_requires_a_checked_out_slot():
    runtime = FakeRuntime()
    pool = _pool(runtime)
    pool.warm_up()
    slot = pool.acquire("user-a")
    pool.release(slot)
    with pytest.raises(PoolError, match="not checked out"):
        pool.start(slot, input="print(1)", timeout_s=5)


def test_start_runs_and_returns_the_result():
    pool = _pool()
    pool.warm_up()
    slot = pool.acquire("user-a")
    result = pool.start(slot, input="print(1)", timeout_s=5)
    assert result is not None and result["stdout"] == "warm-ok"
    pool.release(slot)


def test_pool_rejects_bad_config():
    with pytest.raises(PoolError, match="non-empty image"):
        WarmPool(FakeRuntime(), image="", flags_builder=list)
    with pytest.raises(PoolError, match="size must be >= 1"):
        WarmPool(FakeRuntime(), image="img", flags_builder=list, size=0)
    pool = _pool()
    with pytest.raises(PoolError, match="non-empty user_id"):
        pool.acquire("")
