"""Database-backed retrieval wiring tests (Postgres).

DO NOT RUN directly: these tests use the shared `puspa_test`
database, and parallel runs collide on it. The coordinator runs
this file alone before each push. It needs Postgres up with the
memory schema created (the shared tests/conftest.py autouse
fixture does that).
"""

from puspa.memory import episodic


def test_search_episodes_pipeline_finds_the_fraud_report(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    episodic.record_episode(
        user_id, "Samsung Galaxy S24 SM-S921B is a phone.", "note", dsn=test_dsn
    )
    episodic.record_episode(
        user_id, "Toko Gadget Murah sells Samsung Galaxy S24.", "note", dsn=test_dsn
    )
    episodic.record_episode(
        user_id, "Buyer Ani reports Toko Gadget Murah for a fake promo.", "note", dsn=test_dsn
    )
    target = episodic.record_episode(
        user_id, "Fraud report FR-88: Toko Gadget Murah ran a fake promo.", "note", dsn=test_dsn
    )
    results = episodic.search_episodes_pipeline(
        user_id, "Is the cheap Galaxy S24 deal trustworthy?", mode="mix", limit=5, dsn=test_dsn
    )
    assert results, "pipeline returned nothing"
    assert target["id"] in [r["evidence_id"] for r in results]
    assert all(r["mode"] == "mix" for r in results)
    assert all(r["evidence_id"] for r in results)


def test_search_episodes_pipeline_rejects_bad_input(make_user, test_dsn):
    import pytest

    user = make_user()
    with pytest.raises(ValueError):
        episodic.search_episodes_pipeline(user["user_id"], "", mode="mix", dsn=test_dsn)
    with pytest.raises(ValueError):
        episodic.search_episodes_pipeline(user["user_id"], "Galaxy S24", mode="bogus", dsn=test_dsn)
