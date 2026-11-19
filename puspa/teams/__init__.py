"""Multi-tenant teams for Puspa hosting (G13).

Pattern sources (DuskBot ``server/cloud-team*.ts`` +
``server/team-*.ts`` + ``server/cloud-entitlement.ts`` +
``server/bot-moves.ts`` + ``server/move-freeze.ts``,
PolyForm Noncommercial — study only; OpenBot organisations/roles/
audit, MIT; hermes-agent multiplex + hosted room + Dockerfile/
compose, MIT — all patterns rewritten as Puspa code, never copied
verbatim):

- ``teams`` — teams as their own entity (billing stays in G2):
  create/delete, owner/member roles, membership in Postgres
  with RLS (``infra/schema_teams.sql``).
- ``invitations`` — single-use invitation tokens, hash-only in
  the DB, 7-day TTL, revocation, idempotent acceptance.
- ``visibility`` — visibility as a function of (thread, member),
  not a global flag; per-member bot access with access requests.
- ``entitlements`` — Ed25519-signed entitlements per machine-id,
  verified against a pinned public key; the home gate
  ("may this home run") with a signed-admin override.
- ``moves`` — workspace moves with move-freeze: bot automation
  is frozen while a move is active; a failed move stays frozen
  and resumable, never silently half-moved.
- ``deploy`` — offline validation for the multi-tenant deploy
  files (``Dockerfile`` + ``docker-compose.yml``). Docker cannot
  run on this VM, so the validator parses the files instead of
  building them; a live deploy check on a VPS is still required.
- ``vps`` — VPS validation + health checks (E-02): Postgres
  tables + RLS, dashboard HTTP probe, Docker precondition, offline
  deploy-file validation, allowlist probe judging. The live steps
  (compose up, sandbox tests in a container, restore on a real
  VPS) wait for a VPS; see ``docs/vps-validation.md``.
- ``schema`` — applies ``infra/schema_teams.sql``.

Google login (``puspa.auth``) stays in front; the G11 hosting auth
(pairing, loopback, gateway) stays the server-to-server path
behind it. This package is the team layer on top of both.
"""

from .deploy import (
    DockerfileInfo,
    DockerfileStage,
    parse_dockerfile,
    validate_compose,
    validate_dockerfile,
)
from .entitlements import (
    ENTITLEMENT_VERSION,
    KNOWN_TIERS,
    Entitlement,
    EntitlementVerdict,
    HomeDecision,
    HomeGate,
    issue_entitlement,
    public_key_from_hex,
    public_key_from_raw,
    revoke_entitlement,
    verify_entitlement,
)
from .errors import (
    BotAccessError,
    EntitlementError,
    InvitationError,
    LastOwnerError,
    MoveError,
    MoveFrozenError,
    NotTeamMemberError,
    NotTeamOwnerError,
    TeamError,
    TeamNotFoundError,
    ThreadNotVisibleError,
)
from .invitations import (
    INVITATION_TTL_DAYS,
    Invitation,
    InvitationStore,
    accept_invitation,
    get_invitation,
    issue_invitation,
    list_invitations,
    purge_expired,
    revoke_invitation,
)
from .moves import FROZEN_STATUSES, MOVE_STATUSES, MoveCoordinator, MoveRecord
from .schema import create_schema, reset_tables, schema_path
from .teams import (
    ROLE_MEMBER,
    ROLE_OWNER,
    Team,
    TeamMember,
    TeamStore,
    add_member,
    create_team,
    delete_team,
    get_team,
    is_member,
    is_owner,
    list_members,
    list_teams_for_user,
    remove_member,
    role_of,
    set_role,
)
from .visibility import (
    BotAccessGate,
    BotAccessRequest,
    BotRef,
    ThreadRef,
    can_access_bot,
    can_see_thread,
    require_thread_visible,
)
from .vps import (
    DEFAULT_DASHBOARD_URL,
    EXPECTED_TABLES,
    RLS_TABLES,
    CheckResult,
    all_passed,
    check_docker_available,
    check_http,
    check_postgres,
    classify_allowlist_probe,
    format_report,
    run_health_checks,
    validate_deploy_files,
)

__all__ = [
    "DEFAULT_DASHBOARD_URL",
    "ENTITLEMENT_VERSION",
    "EXPECTED_TABLES",
    "FROZEN_STATUSES",
    "INVITATION_TTL_DAYS",
    "KNOWN_TIERS",
    "MOVE_STATUSES",
    "RLS_TABLES",
    "ROLE_MEMBER",
    "ROLE_OWNER",
    "BotAccessError",
    "BotAccessGate",
    "BotAccessRequest",
    "BotRef",
    "CheckResult",
    "DockerfileInfo",
    "DockerfileStage",
    "Entitlement",
    "EntitlementError",
    "EntitlementVerdict",
    "HomeDecision",
    "HomeGate",
    "Invitation",
    "InvitationError",
    "InvitationStore",
    "LastOwnerError",
    "MoveCoordinator",
    "MoveError",
    "MoveFrozenError",
    "MoveRecord",
    "NotTeamMemberError",
    "NotTeamOwnerError",
    "Team",
    "TeamError",
    "TeamMember",
    "TeamNotFoundError",
    "TeamStore",
    "ThreadNotVisibleError",
    "ThreadRef",
    "accept_invitation",
    "add_member",
    "all_passed",
    "can_access_bot",
    "can_see_thread",
    "check_docker_available",
    "check_http",
    "check_postgres",
    "classify_allowlist_probe",
    "create_schema",
    "create_team",
    "delete_team",
    "format_report",
    "get_invitation",
    "get_team",
    "is_member",
    "is_owner",
    "issue_entitlement",
    "issue_invitation",
    "list_invitations",
    "list_members",
    "list_teams_for_user",
    "parse_dockerfile",
    "public_key_from_hex",
    "public_key_from_raw",
    "purge_expired",
    "remove_member",
    "require_thread_visible",
    "reset_tables",
    "revoke_entitlement",
    "revoke_invitation",
    "role_of",
    "run_health_checks",
    "schema_path",
    "set_role",
    "validate_compose",
    "validate_deploy_files",
    "validate_dockerfile",
    "verify_entitlement",
]
