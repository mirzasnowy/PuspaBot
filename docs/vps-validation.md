# VPS validation + machine-replacement drill (E-02, plan section 4.2)

One VPS with full virtualization, then in order: compose up, health
check, sandbox tests, network allowlist test, backup restore. Plus a
"machine replaced" drill: rebuild from scratch with one command and
time it.

## Honest status — read this first

The Contabo VPS has NOT been bought yet, so NO live check in this
document has run. Nothing below claims otherwise.

| Check | Status | Where it runs |
| --- | --- | --- |
| Rebuild script exists, syntax-checked, dry-run passes | DONE here | `scripts/vps-rebuild.sh` |
| Health checks exist and pass where they can run | DONE here | `puspa/teams/vps.py`, `tests/test_vps_validation_e02.py` |
| Backup-restore round trip on a real Postgres | DONE here (w2 DB) | `tests/test_vps_validation_e02.py` |
| Offline Dockerfile + compose validation | DONE here (already existed, G13) | `puspa/teams/deploy.py` |
| `docker compose up` on a real VPS | PENDING — no VPS yet | step 1 below |
| Health check against the composed app | PENDING — no VPS yet | step 2 below |
| Sandbox tests inside a container | PENDING — no VPS yet | step 3 below |
| Network allowlist test (real egress path) | PENDING — no VPS yet | step 4 below |
| Backup restore on the VPS | PENDING — no VPS yet | step 5 below |
| Machine-replacement drill, timed live run | PENDING — no VPS yet | drill below |

What WAS proven here, on this VM: the rebuild script's dry-run
prints all 10 steps in order and exits 0; the health-check module
passes Postgres (tables + RLS on `puspa_test_w2`), HTTP probes
against a local server, and the offline deploy validation; the
backup module creates an encrypted backup of `puspa_test_w2` and
restores it into a scratch database with identical row counts. The
Docker precondition check correctly FAILS here (this VM has a
`docker` binary but no compose plugin and no daemon, so
containers cannot run on it) — that failure is the honest
signal, not a bug.

## Machine

- One VPS with full virtualization (KVM or equivalent — containers
  must actually run; this dev VM rejects container namespaces, so
  it cannot stand in for the VPS).
- Planned host: Contabo Cloud VPS S (4 vCPU, 8 GB RAM, 200 GB,
  Singapore) — purchase is still Nero's decision, not yet made.
- Ubuntu 24.04, root SSH access, outbound internet (apt, PyPI,
  GitHub, Docker Hub).

## 0. Rebuild the machine (one command)

On the fresh VPS, as root:

```sh
curl -O https://raw.githubusercontent.com/mirzasnowy/puspa-ai/main/scripts/vps-rebuild.sh
bash vps-rebuild.sh
```

The script (10 timed steps, log at `~/vps-rebuild.log`) installs
Python 3.12 + PostgreSQL 16 + pgvector + Docker, starts the
cluster, creates role `root` (superuser, peer auth) BEFORE running
the migration runner, clones the tree to `~/puspa`, installs the
pinned requirements into a venv, creates the `puspa` app database
(the runner applies the schema to whichever database the DSN
names), applies the migrations to the app database, verifies RLS on
both databases, verifies Docker + Compose, and runs the offline
deploy validation pytest. It is idempotent — re-running it
re-applies the same guarded steps. Auth is peer auth everywhere;
the script creates, reads, and stores no credentials.

Record the timings (fill in on the live run):

| Step | Time |
| --- | --- |
| Install system packages (apt update + install) | |
| Start PostgreSQL cluster | |
| Create role root | |
| Fetch Puspa tree | |
| Create venv + install requirements | |
| Create databases | |
| Apply migrations (app + test databases) | |
| Verify RLS | |
| Verify Docker and Compose | |
| Offline deploy validation | |
| **Total** | |

## 1. Compose up

```sh
cd ~/puspa
docker compose up --build -d
docker compose ps
docker compose logs --tail=50
```

Pass criteria: both services (`puspa-gateway`, `puspa-dashboard`)
show healthy/running; no restart loop; logs show the app listening.
If the build fails, record the failing layer verbatim — do not
patch the Dockerfile live on the VPS and call it validated; fix it
in the repo, re-run the rebuild, and repeat this step.

## 2. Health check

From the VPS shell (or from your machine through an SSH tunnel —
never by publishing a port to the LAN):

```sh
cd ~/puspa
.venv/bin/python - <<'EOF'
from puspa.teams.vps import format_report, run_health_checks

print(format_report(run_health_checks()))
EOF
curl -f http://127.0.0.1:8000/manifest.json
```

Pass criteria: every line of the report is PASS — Postgres
connect + tables + RLS, dashboard HTTP 200, Docker available,
Dockerfile + compose clean. Then open the dashboard through the
tunnel (`ssh -L 8000:127.0.0.1:8000 <user>@<host>`) and log in.

## 3. Sandbox tests

The sandbox tests that are skipped on machines without Docker run
inside the composed environment. From the repo on the VPS:

```sh
cd ~/puspa
.venv/bin/python -m pytest tests/test_sandbox.py tests/test_teams_deploy.py -q
```

Pass criteria: the container tests execute (not skipped) and pass.
Record the exact counts. If a test still skips on the VPS, that is
a finding, not a pass — record why.

## 4. Network allowlist test

The sandbox runs with `--network none` today (fail-closed), and
`puspa/sandbox/allowlist.py` is the list of domains allowed once a
networked path is enabled. On the VPS, probe both sides and judge
each outcome with `classify_allowlist_probe` (allowed URL must
fetch, blocked URL must not):

- Allowed, must fetch: `https://api.openai.com/v1/models`,
  `https://api.openrouter.ai/`.
- Blocked, must NOT fetch: `https://169.254.169.254/latest/meta-data/`
  (cloud metadata endpoint), `https://example.com/` (not on the
  list).

Pass criteria: every probe outcome matches the allowlist. A blocked
URL that fetches is a security failure — stop and fix the egress
path before any user data touches the machine.

## 5. Backup restore

```sh
cd ~/puspa
export PUSPA_BACKUP_KEY="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
# Store that key OUTSIDE the VPS (losing it loses every credential
# in the vault). Then:
.venv/bin/python -m pytest tests/test_backup.py -q
```

Pass criteria: the backup tests pass on the VPS, including a
restore into a clean database with matching row counts. Record the
restore wall-clock time: gate 15 requires restore to a clean host
in under 30 minutes.

## Machine-replacement drill

1. Note the wall-clock time and the state to preserve (which
   databases, which files under `~/puspa`, the backup key location).
2. Wipe the machine (or start a second fresh VPS — same thing for
   the drill: nothing on the old machine may be reused).
3. Run the one command from step 0 and record every step time in
   the table above.
4. Run steps 1–5 of this document against the rebuilt machine.
5. Record: total rebuild time, total validation time, and anything
   that needed a manual fix. A manual fix is a drill failure — the
   fix belongs in `scripts/vps-rebuild.sh` or the repo, and the
   drill repeats from step 2 until the rebuild is one command with
   no manual steps.

## Safety notes (do not skip)

- Ports publish on the host's `127.0.0.1` only
  (`127.0.0.1:8000:8000`, `127.0.0.1:8001:8001`). Never change a
  binding to `0.0.0.0` or a bare port. Remote access is only via an
  SSH tunnel or an authenticated reverse proxy.
- `PUSPA_API_KEY` stays unset: the API server stays off unless the
  key is deliberately uncommented in `docker-compose.yml`.
- Postgres auth is peer auth. No credentials live in the repo, the
  rebuild script, or the compose file.
- The two compose services share one home volume on purpose. Do not
  run a third writer against that volume (G26's single-writer rule
  is process-wide, not machine-wide).
