# Backup and Restore Drill Log (E-09, plan section 5.4)

Monthly restore drills, timed against the Gate 15 target of
30 minutes to a clean host. Each entry records the measured total,
the phase times, and what kind of drill it was.

## Drill: 2026-11-20 05:32 WIB (2026-11-19T22:32:58Z)

- Result: **PASS**
- Total: **5.86s** (target: 1800s)
- Kind: **local scratch-instance drill** (NOT a live clean-host
  drill — that waits for the validation VPS, E-02).
- Command: `python -m puspa.backup.drill --keep --work-root /tmp/drill-debug2`
- Report: JSON report from the run (13 checks, all PASS).

### Phases

| Phase | Seconds |
| --- | ---: |
| init_scratch_instance | 2.2 |
| apply_migrations | 0.62 |
| seed_before_backup | 0.68 |
| snapshot_state_db | 0.03 |
| base_backup | 0.47 |
| seed_after_backup | 0.03 |
| switch_wal_and_archive | 0.57 |
| simulate_disaster | 0.2 |
| restore_and_recover | 0.8 |
| verify | 0.09 |

### Checks (13/13 PASS)

- migrations_applied (19 migrations)
- state_snapshot_hash
- base_backup_taken
- wal_segment_archived (000000010000000000000003)
- wal_archiving_healthy (archived_count 4, failed_count 0)
- wal_chain_continuous (3 segments, no gaps)
- data_dir_destroyed
- recovery_completed (replayed WAL to latest)
- vault_canary_before_backup (decrypts to `secret-before`)
- vault_canary_after_backup (post-backup write survived via WAL replay)
- ledger_chain_22222222 (hash chain verifies)
- ledger_chain_33333333 (hash chain verifies)
- escrowed_key_unlocks_vault (checked 2 rows)

### Honest limits

- The scratch instance ran on this host with a local `cp` WAL
  archive backend. It proves the archive/replay path, not off-host
  object storage (pgBackRest to S3) and not a clean VPS host.
- The 5.86s total is a tiny database (canary rows only). A
  production-size restore will be slower; the first VPS drill must
  measure the real number.
- The shared dev cluster (`puspa_test_w1`) still has
  `archive_mode = off`; WAL archiving is proven only inside the
  drill's scratch instance, not on the dev cluster.
