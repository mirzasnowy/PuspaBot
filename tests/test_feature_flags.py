"""Feature flags + canary releases (E-21, plan section 9.6).

Unit tests: no database, no network — except one integration test
(the PWA execute-endpoint check), which needs Postgres. Every
flag-off test proves the surface is unreachable — the gated
function raises before any database write, network call, or
provider call — and every flag-on test proves the same surface
is reachable again. Run with:

    pytest tests/test_feature_flags.py -q

The unit subset alone runs with ``--noconftest`` (``-m unit``).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from puspa.billing.config import default_config
from puspa.billing.ledger import MemoryUsageStore
from puspa.billing.providers import FakePaymentProvider
from puspa.billing.routes import BillingDeps, handle_billing_request
from puspa.billing.webhook import MemoryIdempotencyStore
from puspa.feature_flags import (
    BETA_DEFAULTS,
    DEFAULTS,
    FLAG_DISCOVERY,
    FLAG_ENV_VARS,
    FLAG_MARKETPLACE,
    FLAG_PAYMENT_EXECUTION,
    FLAG_SKILL_INSTALL,
    FLAG_TEAMS,
    FLAG_WORKFLOW,
    PROFILE_ENV_VAR,
    FeatureDisabledError,
    beta_profile,
    describe,
    enabled_flags,
    is_enabled,
    require_enabled,
)
from puspa.marketplace import MarketplaceRegistry
from puspa.marketplace.discovery import (
    SKILL_INSTALL_ENV_VAR,
    DiscoveredSkill,
    SkillsShClient,
    install_discovered,
    search_skills,
    search_with_cache,
    skill_install_enabled,
)
from puspa.marketplace.errors import DiscoveryError
from puspa.payments.prepare import PreparationService, execute_payment
from puspa.payments.schema import create_schema as create_payments_schema
from puspa.payments.schema import reset_tables as reset_payments_tables
from puspa.pwa.app import create_app
from puspa.teams import TeamStore
from puspa.teams.invitations import InvitationStore
from puspa.workflow import composer, runner
from tests.marketplace_helpers import make_skill_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLLBACK_PATH = REPO_ROOT / "scripts" / "rollback.py"
BETA_ENV_PATH = REPO_ROOT / "config" / "beta.env"
MIGRATIONS_DIR = REPO_ROOT / "puspa" / "migrations" / "versions"

ALL_FLAG_VARS = list(FLAG_ENV_VARS.values())

DUMMY_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


@pytest.fixture(autouse=True)
def _clean_flag_env(monkeypatch):
    """Every test starts from the built-in defaults: no profile, no
    per-flag variable set. Tests opt into a profile or a value."""
    for var in ALL_FLAG_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv(PROFILE_ENV_VAR, raising=False)


def _flags_off(monkeypatch, *names):
    for name in names:
        monkeypatch.setenv(FLAG_ENV_VARS[name], "0")


# -- flag resolution ------------------------------------------------------


def test_defaults_are_on_except_skill_install():
    assert enabled_flags() == {
        FLAG_MARKETPLACE: True,
        FLAG_DISCOVERY: True,
        FLAG_WORKFLOW: True,
        FLAG_TEAMS: True,
        FLAG_PAYMENT_EXECUTION: True,
        FLAG_SKILL_INSTALL: False,
    }


def test_beta_profile_turns_everything_off(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV_VAR, "beta")
    assert enabled_flags() == {name: False for name in FLAG_ENV_VARS}
    assert beta_profile() == BETA_DEFAULTS
    assert all(value is False for value in BETA_DEFAULTS.values())


def test_explicit_variable_overrides_beta_profile(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV_VAR, "beta")
    monkeypatch.setenv(FLAG_ENV_VARS[FLAG_WORKFLOW], "1")
    assert is_enabled(FLAG_WORKFLOW) is True
    assert is_enabled(FLAG_MARKETPLACE) is False
    assert is_enabled(FLAG_TEAMS) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", "on", "ON"])
def test_truthy_spellings_turn_a_flag_on(monkeypatch, value):
    monkeypatch.setenv(FLAG_ENV_VARS[FLAG_MARKETPLACE], value)
    assert is_enabled(FLAG_MARKETPLACE) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe", "2"])
def test_anything_else_counts_as_off(monkeypatch, value):
    monkeypatch.setenv(FLAG_ENV_VARS[FLAG_MARKETPLACE], value)
    assert is_enabled(FLAG_MARKETPLACE) is False


def test_unknown_flag_raises():
    with pytest.raises(ValueError, match="unknown feature flag"):
        is_enabled("nope")


def test_unknown_profile_raises(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV_VAR, "staging")
    with pytest.raises(ValueError, match="unknown PUSPA_PROFILE"):
        is_enabled(FLAG_WORKFLOW)


def test_require_enabled_raises_with_flag_name(monkeypatch):
    _flags_off(monkeypatch, FLAG_TEAMS)
    with pytest.raises(FeatureDisabledError, match="teams") as excinfo:
        require_enabled(FLAG_TEAMS)
    assert excinfo.value.flag == FLAG_TEAMS


def test_describe_reports_source(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV_VAR, "beta")
    monkeypatch.setenv(FLAG_ENV_VARS[FLAG_WORKFLOW], "1")
    info = describe()
    assert info[FLAG_WORKFLOW]["source"] == "explicit"
    assert info[FLAG_WORKFLOW]["enabled"] is True
    assert info[FLAG_MARKETPLACE]["source"] == "profile"
    assert info[FLAG_MARKETPLACE]["enabled"] is False
    assert info[FLAG_MARKETPLACE]["env_var"] == "PUSPA_FLAG_MARKETPLACE"
    assert info[FLAG_MARKETPLACE]["default"] is True
    assert info[FLAG_MARKETPLACE]["beta_default"] is False


def test_skill_install_flag_is_one_source_of_truth(monkeypatch):
    """E-12's variable name is the central flag's variable name:
    discovery delegates, it does not keep its own copy."""
    assert FLAG_ENV_VARS[FLAG_SKILL_INSTALL] == SKILL_INSTALL_ENV_VAR
    assert skill_install_enabled() is False
    monkeypatch.setenv(SKILL_INSTALL_ENV_VAR, "1")
    assert skill_install_enabled() is True
    assert is_enabled(FLAG_SKILL_INSTALL) is True


def test_beta_env_file_matches_beta_defaults():
    """config/beta.env, parsed as environment, turns every flag off."""
    env: dict[str, str] = {}
    for raw_line in BETA_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    assert env[PROFILE_ENV_VAR] == "beta"
    assert set(env) == set(ALL_FLAG_VARS) | {PROFILE_ENV_VAR}
    assert enabled_flags(env=env) == {name: False for name in FLAG_ENV_VARS}
    assert DEFAULTS[FLAG_SKILL_INSTALL] is False


# -- marketplace + discovery ----------------------------------------------


def test_registry_import_refused_when_marketplace_off(tmp_path, monkeypatch):
    _flags_off(monkeypatch, FLAG_MARKETPLACE)
    registry = MarketplaceRegistry()
    with pytest.raises(FeatureDisabledError, match="marketplace"):
        registry.import_skill(make_skill_dir(tmp_path))


def test_registry_install_refused_when_marketplace_off(tmp_path, monkeypatch):
    registry = MarketplaceRegistry()
    registry.import_skill(make_skill_dir(tmp_path))
    _flags_off(monkeypatch, FLAG_MARKETPLACE)
    with pytest.raises(FeatureDisabledError, match="marketplace"):
        registry.install("price-compare", user_id="user-1")


def test_registry_import_and_install_work_when_marketplace_on(tmp_path):
    """Flag on (the default) leaves the existing behaviour alone."""
    registry = MarketplaceRegistry()
    entry = registry.import_skill(make_skill_dir(tmp_path))
    assert entry.status == "disabled"
    assert registry.get_entry("price-compare") is entry
    with pytest.raises(Exception, match="trust"):
        registry.install("price-compare", user_id="user-1")


def _search_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"skills": [{"id": "demo-skill", "name": "Demo", "installs": 5000}]},
        )

    return SkillsShClient(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_discovery_search_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_DISCOVERY)
    with pytest.raises(FeatureDisabledError, match="discovery"):
        search_skills(_search_client(), "demo")


def test_discovery_search_with_cache_inherits_the_gate(monkeypatch):
    _flags_off(monkeypatch, FLAG_DISCOVERY)
    from puspa.marketplace.discovery import DiscoveryCache

    with pytest.raises(FeatureDisabledError, match="discovery"):
        search_with_cache(_search_client(), DiscoveryCache(), "demo")


def test_discovery_search_works_when_flag_on():
    outcome = search_skills(_search_client(), "demo")
    assert [skill.slug for skill in outcome.skills] == ["demo-skill"]


def test_install_discovered_refused_when_marketplace_off(monkeypatch):
    """Beta refuses the install even when skills.sh installs are on."""
    monkeypatch.setenv(SKILL_INSTALL_ENV_VAR, "1")
    _flags_off(monkeypatch, FLAG_MARKETPLACE)
    registry = MarketplaceRegistry()
    skill = DiscoveredSkill(slug="demo-skill", name="Demo", source="x/y", installs=1)
    with pytest.raises(FeatureDisabledError, match="marketplace"):
        install_discovered(registry, skill, user_id="user-1", approval=None)


def test_install_discovered_refused_when_skill_install_off(monkeypatch):
    """E-12 behaviour is preserved: skill installs stay off by default."""
    registry = MarketplaceRegistry()
    skill = DiscoveredSkill(slug="demo-skill", name="Demo", source="x/y", installs=1)
    with pytest.raises(DiscoveryError, match="feature flag"):
        install_discovered(registry, skill, user_id="user-1", approval=None)


# -- workflow ---------------------------------------------------------------


def test_workflow_create_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_WORKFLOW)
    with pytest.raises(FeatureDisabledError, match="workflow"):
        runner.create_workflow(
            user_id="user-1",
            agent_id="agent-1",
            name="test",
            trigger={"type": "manual"},
            step=[],
        )


def test_workflow_activate_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_WORKFLOW)
    with pytest.raises(FeatureDisabledError, match="workflow"):
        runner.activate_workflow("wf-1", user_id="user-1", permission_user={})


def test_workflow_run_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_WORKFLOW)
    with pytest.raises(FeatureDisabledError, match="workflow"):
        runner.run_workflow("wf-1", user_id="user-1", mode="run")


def test_workflow_dry_run_is_not_gated(monkeypatch):
    """A dry run only simulates, so the flag does not block it: the
    call gets past the gate and reaches the workflow lookup."""
    _flags_off(monkeypatch, FLAG_WORKFLOW)

    def _missing(*args, **kwargs):
        raise RuntimeError("gate passed")

    monkeypatch.setattr(runner, "get_workflow", _missing)
    with pytest.raises(RuntimeError, match="gate passed"):
        runner.run_workflow("wf-1", user_id="user-1", mode="dry")


def test_workflow_tick_and_execute_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_WORKFLOW)
    with pytest.raises(FeatureDisabledError, match="workflow"):
        runner.tick_workflow()
    with pytest.raises(FeatureDisabledError, match="workflow"):
        runner.execute_approved_action("approval-1", user_id="user-1")


def test_workflow_compose_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_WORKFLOW)
    with pytest.raises(FeatureDisabledError, match="workflow"):
        composer.compose_draft("remind me tomorrow", user_id="user-1")


# -- teams ------------------------------------------------------------------


def test_team_create_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_TEAMS)
    store = TeamStore(DUMMY_DSN)
    with pytest.raises(FeatureDisabledError, match="teams"):
        store.create_team("Team", "00000000-0000-0000-0000-000000000001")


def test_team_add_member_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_TEAMS)
    store = TeamStore(DUMMY_DSN)
    with pytest.raises(FeatureDisabledError, match="teams"):
        store.add_member(
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            added_by="00000000-0000-0000-0000-000000000001",
        )


def test_team_invitations_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_TEAMS)
    store = InvitationStore(DUMMY_DSN)
    with pytest.raises(FeatureDisabledError, match="teams"):
        store.issue_invitation(
            "00000000-0000-0000-0000-000000000001",
            inviter_user_id="00000000-0000-0000-0000-000000000001",
            invited_email="friend@example.com",
        )
    with pytest.raises(FeatureDisabledError, match="teams"):
        store.accept_invitation("token", user_id="00000000-0000-0000-0000-000000000002")


# -- payment execution ------------------------------------------------------


def test_payment_execute_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_PAYMENT_EXECUTION)
    service = PreparationService(dsn=DUMMY_DSN)
    with pytest.raises(FeatureDisabledError, match="payment_execution"):
        service.execute_payment("prep-1", user_id="user-1")


def test_payment_execute_module_function_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_PAYMENT_EXECUTION)
    with pytest.raises(FeatureDisabledError, match="payment_execution"):
        execute_payment("prep-1", user_id="user-1", dsn=DUMMY_DSN)


def _billing_deps(**overrides):
    values = {
        "config": default_config(),
        "provider": FakePaymentProvider(),
        "usage_store": MemoryUsageStore(),
        "webhook_store": MemoryIdempotencyStore(),
        "webhook_secret": "hook-secret",
        "entitlement_secret": "ent-secret",
    }
    values.update(overrides)
    return BillingDeps(**values)


def test_billing_checkout_refused_when_flag_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_PAYMENT_EXECUTION)
    response = handle_billing_request(
        "POST",
        "/billing/checkout",
        {"amount_idr": 9000, "description": "test"},
        _billing_deps(),
    )
    assert response["status"] == 403
    assert response["body"]["error"] == "feature_disabled"


def test_billing_checkout_works_when_flag_on():
    response = handle_billing_request(
        "POST",
        "/billing/checkout",
        {"amount_idr": 9000, "description": "test"},
        _billing_deps(),
    )
    assert response["status"] == 201
    assert response["body"]["amount_idr"] == 9000


def test_billing_reads_stay_available_when_execution_off(monkeypatch):
    _flags_off(monkeypatch, FLAG_PAYMENT_EXECUTION)
    response = handle_billing_request("GET", "/billing/catalog", {}, _billing_deps())
    assert response["status"] == 200
    assert [tier["tier_id"] for tier in response["body"]["tiers"]] == [
        "free",
        "plus",
        "pro",
        "team",
    ]


def _payment_body():
    return {
        "recipient": {
            "name": "Toko Kopi Senja",
            "account_ref": "1234567890",
            "verified": True,
            "verified_by": "seller page on Tokopedia",
        },
        "amount_rupiah": 150_000,
        "rail": "bayar pake qris",
        "evidence": [
            {
                "kind": "seller_page",
                "source_url": "https://tokopedia.com/toko-kopi-senja",
                "recipient_name": "Toko Kopi Senja",
                "account_ref": "1234567890",
            }
        ],
    }


@pytest.mark.integration
def test_pwa_execute_endpoint_is_403_when_flag_off(make_user, test_dsn, monkeypatch):
    """HTTP level: the execute endpoint answers 403 (not 500) when
    the payment_execution flag is off, for an approved preparation
    that would otherwise be payable."""
    create_payments_schema(test_dsn)
    reset_payments_tables(test_dsn)
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    with TestClient(app) as client:
        prepared = client.post("/api/payments", json=_payment_body())
        assert prepared.status_code == 200
        preparation_id = prepared.json()["id"]
        approved = client.post(f"/api/payments/{preparation_id}/approve")
        assert approved.status_code == 200
        _flags_off(monkeypatch, FLAG_PAYMENT_EXECUTION)
        refused = client.post(f"/api/payments/{preparation_id}/execute")
        assert refused.status_code == 403
        assert "payment_execution" in refused.json()["error"]
    reset_payments_tables(test_dsn)


# -- rollback script --------------------------------------------------------


def _load_rollback():
    spec = importlib.util.spec_from_file_location("rollback_under_test", ROLLBACK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rollback_accepts_pinned_tags():
    rollback = _load_rollback()
    assert rollback.validate_tag("puspa:81016e3a") == "puspa:81016e3a"
    assert rollback.validate_tag("puspa:" + "a" * 40) == "puspa:" + "a" * 40
    assert rollback.validate_tag("puspa:v0.1.0-beta") == "puspa:v0.1.0-beta"


@pytest.mark.parametrize(
    "tag", ["puspa:latest", "puspa:local", "latest", "81016e3a", "puspa:", "other:81016e3a"]
)
def test_rollback_refuses_unpinned_tags(tag):
    rollback = _load_rollback()
    with pytest.raises(rollback.RollbackError, match="unpinned"):
        rollback.validate_tag(tag)


def test_rollback_rewrites_only_puspa_images():
    rollback = _load_rollback()
    text = (
        "services:\n"
        "  gateway:\n"
        "    image: puspa:local\n"
        "  db:\n"
        "    image: pgvector/pgvector:0.6.0-pg16\n"
    )
    new_text, changes = rollback.rewrite_images(text, "puspa:81016e3a")
    assert changes == [(3, "puspa:local", "puspa:81016e3a")]
    assert "image: puspa:81016e3a" in new_text
    assert "image: pgvector/pgvector:0.6.0-pg16" in new_text


def test_rollback_refuses_compose_without_puspa_image():
    rollback = _load_rollback()
    with pytest.raises(rollback.RollbackError, match="no 'image: puspa"):
        rollback.rewrite_images("services:\n  db:\n    image: pgvector/pgvector:0.6.0-pg16\n", "puspa:abc1234")


def test_rollback_dry_run_changes_nothing(tmp_path):
    rollback = _load_rollback()
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  gateway:\n    image: puspa:deadbee\n", encoding="utf-8")
    code = rollback.main(
        ["--to-tag", "puspa:81016e3a", "--compose-file", str(compose)]
    )
    assert code == 0
    assert "puspa:deadbee" in compose.read_text(encoding="utf-8")


def test_rollback_apply_rewrites_the_file(tmp_path):
    rollback = _load_rollback()
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n  gateway:\n    image: puspa:deadbee\n  dashboard:\n    image: puspa:deadbee\n",
        encoding="utf-8",
    )
    code = rollback.main(
        ["--to-tag", "puspa:81016e3a", "--compose-file", str(compose), "--apply"]
    )
    assert code == 0
    assert compose.read_text(encoding="utf-8").count("image: puspa:81016e3a") == 2


def test_rollback_run_needs_apply(tmp_path):
    rollback = _load_rollback()
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  gateway:\n    image: puspa:deadbee\n", encoding="utf-8")
    code = rollback.main(
        ["--to-tag", "puspa:81016e3a", "--compose-file", str(compose), "--run"]
    )
    assert code == 2


def test_canary_override_is_one_pinned_container():
    import yaml

    data = yaml.safe_load((REPO_ROOT / "docker-compose.canary.yml").read_text(encoding="utf-8"))
    canary = data["services"]["gateway-canary"]
    assert "PUSPA_IMAGE_TAG" in canary["image"]
    assert canary["container_name"] == "puspa-gateway-canary"
    assert canary["restart"] == "no"
    ports = [str(port) for port in canary["ports"]]
    assert ports and all(port.startswith("127.0.0.1:") for port in ports)
    assert "puspa-home-canary" in str(canary["volumes"])


# -- expand-then-contract migrations ----------------------------------------


def test_no_migration_up_section_drops_a_column_or_table():
    """Expand-then-contract (E-21, plan 9.6): an `up` section may only
    add. A release whose migration drops something cannot roll back
    by image alone, so the suite fails it before it can ship."""
    drop_pattern = re.compile(
        r"(?im)^\s*(?:DROP\s+(?:TABLE|COLUMN)|ALTER\s+TABLE\s+\S+\s+DROP\s+COLUMN)\b"
    )
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert files, "no migration files found"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "-- migrate:up" in text, f"{path.name} has no up section"
        assert "-- migrate:down" in text, f"{path.name} has no down section"
        up = text.split("-- migrate:down")[0]
        assert not drop_pattern.search(up), (
            f"{path.name} drops a column or table in its up section — "
            "expand first, contract in a later migration"
        )
