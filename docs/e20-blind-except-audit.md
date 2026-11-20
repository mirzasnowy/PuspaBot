# E-20 audit: blind-except handlers, bandit baseline, mypy strict next steps

Recorded 20 Nov 2026 (E-20, plan section 9.5). This document is the audit
behind the two ratchets in `tests/test_blind_except_ratchet.py` (ruff BLE001)
and `tests/test_bandit_baseline.py` (bandit over `puspa/`, sandbox excluded)
and behind the mypy strict plan for `policy`, `ledger`, `accounts`,
`chassis`, and `credentials`.

## 1. Blind-except: what was counted

Tool: `ruff check --select BLE001 puspa tests demo` (ruff 0.17.5, the same
version pinned in `requirements-dev.txt`). Scope matches the lint lane
(`ruff check puspa tests demo`).

Ruff BLE001 counts only handlers that swallow the exception (return a
fallback, log and continue, or pass). A handler that re-raises — `raise`,
`raise ... from exc`, or a conditional raise — is NOT flagged. That is
exactly the distinction this audit needs, because the re-raising handlers
are the error-translation pattern: they catch a low-level failure and
raise a domain error (`SigningError`, `TrustRootError`, `TokenError`).

Counts, measured 20 Nov 2026:

| Measure | Count |
| --- | --- |
| Blind handlers in scope by AST (`except Exception`, `except BaseException`, bare `except`, or a tuple containing `Exception`) | 248 |
| Of those, re-raising translators (not flagged by ruff) | 56 |
| Of those, swallowing handlers (ruff BLE001) before E-20 | 203 |
| Narrowed during E-20 (section 3) | 11 |
| Swallowing handlers (ruff BLE001) after E-20 — the ratchet baseline | 192 |
| Of the baseline, `except Exception` | 189 |
| Of the baseline, `except BaseException` | 3 |

The "~98 handlers" figure in the old `pyproject.toml` comment was wrong:
it was never measured. The real count is 203 before this ticket and 192
after it. The comment is corrected as part of this ticket (see section 6).

No bare `except:` exists anywhere in scope (0 found).

## 2. Money, credential, and approval paths: verdicts

Every BLE001 handler on a money, credential, or approval path was read.
The verdicts fall into three groups. "Fail closed" means the handler's
fallback denies the operation, returns no value, or reports failure —
it never grants money, access, or approval by default.

### 2.1 Safe as written — must stay blind-except, and why

* **Approval callbacks** (`puspa/tools/approval.py:262, 281`): the
  approval callback is caller-supplied UI code; any exception type can
  escape it. The handler sets `approved = False`. Fail closed by
  construction: a crashing approval prompt refuses the command. Narrowing
  to a fixed exception list would let an unexpected callback failure
  propagate and bypass the approval decision entirely.
* **Signature verification, fail-closed** (`puspa/policy/sigstore_verify.py`
  4 sites, `puspa/policy/skill_signing.py` 3 sites,
  `puspa/policy/signing.py:326`, `puspa/policy/revocation.py:482, 519`):
  every handler returns `(False, reason)` or a "failed" fetch result.
  A verification that cannot complete is a verification that failed;
  the broad catch is what makes an unknown verifier failure deny instead
  of crash past the check. Narrowing these would trade a known-deny for
  an unknown-propagate.
* **Thread-boundary re-raise, `except BaseException`** (`puspa/decider/decider.py:234`,
  `puspa/turns/interrupt.py:115`): the worker thread captures any
  throwable — including `KeyboardInterrupt` and `SystemExit` — into a
  queue so the calling thread re-raises it unchanged. Narrowing to
  `Exception` would swallow a cancellation on the worker thread and the
  caller would hang or time out instead of seeing the real cause. These
  three `BaseException` sites (the third is a test thread harness in
  `tests/test_router_oauth_translate.py:452`, same pattern) must stay.
* **Billing read, fail-closed** (`puspa/billing/billing.py:166, 199`,
  `puspa/billing/client.py:240`): a bill-site read failure returns
  `success: False`, a "Cannot be determined" verdict, or a `failed`
  overlay state. No amount is ever assumed from a failed read. The
  failure modes of a scraped provider page (network, HTML layout,
  encoding) are too varied to enumerate.
* **Credential refresh diagnostics** (`puspa/router/oauth.py:574`,
  narrowed to `ValueError` in section 3): only the provider's error
  code string is read; an unparsable body yields `None`.
* **Trust-root refresh** (`puspa/policy/trust_root.py:346, 375`): a
  download or install failure returns `status: failed` and keeps the
  previous cached root; it never installs a half-written root.
* **Per-connection audit daemon** (`puspa/ledger/audit_daemon.py:158`):
  one bad socket/JSON request gets an error response; the daemon keeps
  serving. The failure modes are socket, decode, and dispatch errors.
* **Hook listener isolation** (`puspa/chassis/hooks.py:325`): one broken
  listener is recorded as `{"ok": False, ...}` and the remaining
  listeners still run. Isolating listeners is the point of the registry.
* **Learning-loop restore** (`puspa/chassis/agent_session.py:109`): a
  failed restore of a saved learning loop is skipped; the agent starts
  with a fresh loop. Best-effort restore, no decision depends on it.
* **Optional-dependency imports** that were NOT narrowed because the
  import can fail for more than a missing module (kept as-is):
  `puspa/chassis/tools.py:318` (package metadata lookup) and
  `puspa/chassis/tools.py:568, 581` (MCP transport call, compression
  fallback — both return an error dict or the uncompressed result).
* **Room job runner** (`puspa/rooms/job_queue.py`, P-05, 20 Nov 2026):
  a claimed job runs a caller-supplied handler, so any exception type
  can escape it. The handler marks the job `failed` and the queue
  moves on; a failed job must never kill the queue. Narrowing to a
  fixed exception list would let an unexpected handler failure
  propagate and stop every later job for that room.

### 2.2 Narrowed during E-20 (11 sites, baseline 203 -> 192)

Each of these had a provably narrow failure mode, verified by reading
the called code, and each keeps the same fallback on the narrowed
exceptions:

| Site | Before | After | Why it is safe |
| --- | --- | --- | --- |
| `puspa/policy/revocation.py:180` | `except (InvalidSignature, ValueError, TypeError, Exception)` | `except (InvalidSignature, ValueError, TypeError)` | `Exception` in the tuple was redundant: the other three already cover every failure of `verify()` and base64 decode. Returns `False` either way. |
| `puspa/policy/skill_signing.py:153` | same tuple | same narrowing | Same reason: signature verification only raises those three. Returns `False`. |
| `puspa/chassis/tools.py:285` | `except Exception` on MCP SDK import | `except ImportError` | Only an import can fail here; the fallback marks the SDK unavailable. |
| `puspa/chassis/tools.py:294` | `except Exception` on pydantic import | `except ImportError` | Same: import-only try block, pinned dependency. |
| `puspa/chassis/context.py:78` | `except Exception` on nanobot import | `except ImportError` | Import-only try block; missing nanobot means no token payload. |
| `puspa/credentials/resolver.py:409` | `except Exception` on router `CATALOG` import | `except ImportError` | Import-only try block; a malformed catalog now fails loudly instead of silently registering zero OpenRouter models. |
| `puspa/router/oauth.py:574` | `except Exception` on `response.json()` | `except ValueError` | A bad body raises `JSONDecodeError`, a `ValueError` subclass. Returns `None`. |
| `puspa/policy/sigstore_verify.py:147` | `except Exception` on `json.loads` | `except ValueError` | `json.loads` raises `ValueError` subclasses (`JSONDecodeError`, `UnicodeDecodeError`) on bad input. Returns `(False, ...)`. |
| `puspa/marketplace/trust.py:196` | `except Exception` on manifest read + parse | `except (OSError, ValueError)` | `read_text` raises `OSError`/`UnicodeDecodeError`; `json.loads` raises `ValueError`. Falls back to an empty manifest. |
| `puspa/pwa/payments_api.py:276` | `except Exception` on `request.json()` | `except ValueError` | Starlette raises `JSONDecodeError` (a `ValueError`) on an unparsable body; the handler then treats the body as `{}` and the later `isinstance` check rejects it. |
| `puspa/policy/tokens.py:135` | `except Exception` on base64url decode | `except (ValueError, TypeError)` | `urlsafe_b64decode` raises `binascii.Error` (a `ValueError`) on bad input; `TypeError` covers a non-string segment. Falls through to returning the raw id. |

### 2.3 Must be narrowed next (recorded, not done here)

These are the highest-value follow-ups. None was attempted here because
each needs a behaviour decision or a wider test sweep than this ticket's
neighbour-test budget allows:

* `puspa/pwa/app.py` (12 sites, the largest file in the baseline): the
  PWA route handlers catch `Exception` around whole request handlers and
  return 500s. Narrowing means naming the per-route failure modes
  (validation, database, provider) one route at a time.
* `puspa/tools/check.py` (7 sites) and `puspa/workflow/runner.py`
  (6 sites): check/runner fallbacks that swallow step failures; each
  needs its failure taxonomy written down before narrowing.
* `puspa/scheduler/cron.py` (5 sites) and `puspa/scheduler/curriculum.py`
  (4 sites): per-job isolation — a crashing job must not kill the
  scheduler, so these stay broad until a per-job error type list exists.
* `puspa/plugins/loader.py` / `adapters.py` (6 sites combined): third-party
  plugin code can raise anything on load; narrowing needs a plugin
  failure taxonomy.
* The 10 test-file handlers (`tests/test_*.py`): connection-probe
  helpers that return `False` on any database failure. Narrow to
  `psycopg.Error` / `OSError` when the probes are next touched.
* `puspa/policy/tokens.py` other sites and `puspa/policy/signing.py`
  re-raise translators are already safe (section 1) and need no work.

Rule for future narrowing: a handler may only be narrowed when the
narrowed list is proven from the called code, the fallback is unchanged,
and the neighbour tests pass. A narrowing that changes the fallback
outcome is a behaviour change, not a lint fix.

## 3. Bandit baseline

Tool: `bandit==1.8.6` (pinned in `requirements-dev.txt` by this ticket),
command `python -m bandit -r puspa --exclude puspa/sandbox -f json`.

Scope decisions, each with a reason:

* **Only `puspa/` is scanned.** Scanning `tests/` adds 12,600 `B101`
  (assert) findings — test asserts are the test mechanism, not a
  security signal — and scanning `demo/` adds throwaway provider code.
  The production code is what bandit is for.
* **The whole `puspa/sandbox/` directory is excluded.** The sandbox
  executor intentionally runs untrusted code: subprocess calls,
  `exec`-shaped helpers, and temp files are its purpose, not findings.
  It has its own containment tests instead. Excluding only
  `exec.py`/`runner.py` would leave 17 more findings in `egress.py`,
  `filesystem.py`, `runtime.py`, `terminal.py`, and `warmpool.py` that
  are the same intentional behaviour.
* **Baseline (bandit 1.8.6, 20 Nov 2026): 119 findings over `puspa/`
  excluding `puspa/sandbox/`** — 100 LOW, 19 MEDIUM, 0 HIGH. By test id:

| Test id | Count | What it is | Verdict |
| --- | --- | --- | --- |
| B101 | 36 | `assert` in production code | Real debt: asserts vanish under `python -O`. Replace with explicit checks where the assert guards a security invariant; baseline until then. |
| B110 | 15 | `try/except: pass` | Overlaps BLE001; same narrowing queue as section 2.3. |
| B603 | 13 | subprocess without `shell=True` check | LOW; call sites pass fixed argv lists. |
| B608 | 10 | possible SQL injection via string query | False positives in the sampled sites: the interpolated values are table names from fixed internal tuples (for example `puspa/sessions/store.py:537` interpolates from `("sessions", "messages", "session_lineage", "model_config")`), never user input. Recorded, not suppressed per line, so a real one still stands out. |
| B112 | 10 | `try/except: continue` | Same queue as B110. |
| B404 | 8 | `subprocess` module imported | LOW, informational. |
| B105 | 6 | hardcoded password string | LOW; all six are test/dummy placeholder strings in non-credential code paths — verify per site before removing from the baseline. |
| B311 | 5 | `random` used where a secure generator fits | LOW; none of the five feeds a token or key. |
| B310 | 4 | `urllib` URL open | MEDIUM; `puspa/plugins/catalog.py:202, 208` opens plugin catalog URLs — check the scheme allowlist there first. |
| B607 | 4 | partial path in subprocess call | LOW; fixed binary names. |
| B108 | 3 | `tempfile.mktemp` | MEDIUM in `puspa/sandbox/filesystem.py` is excluded with the sandbox; the remaining three are in non-sandbox code and should move to `mkstemp`. |
| B106 | 3 | `random` module import | LOW, informational. |
| B103 | 1 | permissive `chmod 0o755` | MEDIUM, `puspa/backup/drill.py:267` on a directory, not a secret file — confirm and record. |
| B113 | 1 | `requests` call without timeout | MEDIUM, `puspa/memory/learning_loop.py:168` — add a timeout; the call already has a caller-side budget. |

Full-tree bandit (including tests/demo/cli/apps) reports 12,836 findings;
that number is not the baseline and must not be quoted as one — it is
almost entirely test asserts.

## 4. Mypy strict: where the five packages stand

Target packages: `puspa/policy`, `puspa/ledger`, `puspa/accounts`,
`puspa/chassis`, `puspa/credentials`. Command:
`python -m mypy --strict puspa/policy puspa/ledger puspa/accounts
puspa/chassis puspa/credentials` (mypy 2.4.1).

Status at the end of E-20: **not strict yet, and not faked.** No
`strict = true` override was added for these packages, because the run
does not pass, and no blanket `ignore_errors` or `type: ignore` was
added to make it pass.

Measured 20 Nov 2026 (mypy 2.4.1), after the fixes below:
`python -m mypy --strict puspa/policy puspa/ledger puspa/accounts
puspa/chassis puspa/credentials` reports **1,713 errors in 123 files**
(the run follows imports, so it also covers modules the five packages
import, such as `puspa/memory`, `puspa/world`, and `demo/`). Inside
exactly the five target packages: **602 errors** — `policy` 237,
`chassis` 105, `credentials` 103, `ledger` 90, `accounts` 67. By error
code inside the targets: `no-untyped-def` 260, `type-arg` 193,
`no-untyped-call` 121, `no-any-return` 18, plus 10 others (`misc`,
`str`, `assignment`, `attr-defined`). Before the E-20 fixes the same
run reported 1,726 errors (615 inside the targets), so the fixes below
removed 13. Overall the same four codes dominate: `no-untyped-def`
680, `type-arg` 532, `no-untyped-call` 413, `no-any-return` 54. The
strict run takes several minutes on a cold cache (it follows imports
into `demo/` and the nanobot boundary) and seconds warm; the default
non-strict run is unaffected.

Fixed during E-20 (cheap, mechanical, each verified by the strict run
and the neighbour tests):

* `puspa/policy/revocations.py` is now fully strict-clean (4 errors
  to 0): `_connect` gained its return annotation
  (`psycopg.Connection[dict[str, Any]]`), `is_revoked_payload` gained
  its `dict[str, Any]` parameter type, and the three `row["revoked_at"]`
  returns go through a `_revoked_at` helper that checks
  `isinstance(value, datetime)` and raises `RevocationError` on corrupt
  data instead of returning `Any` as `datetime`. The helper is a real
  guard, not a cast: a non-datetime cutoff now fails loudly.
* `puspa/accounts/__init__.py` is now strict-clean (2 errors to 0):
  `create_schema` gained `(dsn: str | None = None) -> None`, and
  `puspa/accounts/users.py:resolve_dsn` gained
  `(dsn: str | None = None) -> str`, which also clears the
  `no-untyped-call` on its callers in `__init__.py` and `access.py`'s
  call sites that resolve through it.

Error shape, from the strict run: the dominant codes are
`no-untyped-def` (functions missing parameter or return annotations),
`type-arg` (bare `dict` / `list` generics), `no-any-return` (returning
`Any` from a declared return type), and `no-untyped-call` (calling an
untyped helper from typed code). A smaller tail is real: `attr-defined`
(for example `puspa/observability/logging.py:87`, a `Logger` attribute
set elsewhere) and functions returning `Any` from JSON parsing.

Next steps, in order (each is a small, reviewable change):

1. Annotate the remaining untyped public functions in
   `puspa/accounts/` (`users.py`, `vault.py`, `user_sessions.py`,
   `rls.py`, `access.py`: `no-untyped-def` plus the `_connect` /
   `_normalize_email` / `_token_hash` helpers behind their
   `no-untyped-call` errors) and in `puspa/policy/conflicts.py`, and
   give the bare `dict` / `list` annotations type arguments
   (`type-arg`). These are mechanical and need no behaviour change.
   (`puspa/policy/revocations.py` and `puspa/accounts/__init__.py`
   are already strict-clean after E-20; see above.)
2. Fix the `no-any-return` sites by narrowing the `Any` at the source
   (JSON loads, `getattr`) instead of casting at the return.
3. Annotate or type the untyped helpers behind the `no-untyped-call`
   errors (`_connect` in `puspa/ledger/ledger.py` and friends) so typed
   callers stop inheriting `Any`.
4. Fix the `attr-defined` tail one site at a time; do not silence it
   with `type: ignore`.
5. Only when a package reports zero strict errors, add a
   `[[tool.mypy.overrides]]` block with `strict = true` for that
   package, one package per commit, with the package's neighbour tests
   green. `policy` is the best first candidate (smallest surface after
   step 1); `chassis` is last (widest third-party boundary).

What is NOT strict after E-20: all five packages, and everything else
in the repo. The existing non-strict mypy run (`python -m mypy puspa
tests demo`, CI `typecheck` lane) is unchanged and still green.
