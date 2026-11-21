---
name: "Backup"
description: "Keeps the user's local data safe: snapshots of the state database and escrow of the vault key, so a lost machine does not mean a lost vault. Local only — server backups live elsewhere."
author: "Puspa Team"
---

# Backup

This skill keeps a single user's local data safe: snapshots of
the state database and escrow of the vault key. If the machine is
lost, the user can come back with the snapshot and the escrowed
key — without either one, recovery is impossible, and this skill
says so plainly.

## When to Use This Skill

Use this skill when the user:

- Asks to back up their data, or whether their data is backed up
- Asks to restore from a backup
- Asks about the vault key, key escrow, or what happens if they
  lose their machine

## How Backup Works

```python
from puspa.backup import (
    snapshot_state_db,
    escrow_vault_key,
    verify_escrowed_key,
    check_key_separation,
    sha256_file,
)

snapshot = snapshot_state_db(dest_dir=dest_dir)  # {"path", "source", "sha256", "size"}
escrow = escrow_vault_key(key, escrow_path, data_backup_dir=data_dir)
ok = verify_escrowed_key(escrow_path)            # the escrow still opens
separated, reason = check_key_separation(escrow_path, data_dir)
digest = sha256_file(path)                       # integrity of a file
```

- `snapshot_state_db` snapshots the local state database into
  `dest_dir` and returns where the snapshot lives plus its
  checksum. Verify the checksum (`sha256_file`) before calling a
  snapshot good.
- `escrow_vault_key` writes the vault key to a separate escrow
  file (mode 0600); `verify_escrowed_key` proves the escrowed key
  still opens the vault; `check_key_separation` proves the key
  escrow lives outside the data backup directory — a backup where
  the key sits next to the data it unlocks is not a backup, it is
  a leak waiting to happen.

## Limits — Read These First

- **Local only.** This is the vault-key escrow and
  state-database snapshots a single user runs locally. Server
  backups, WAL archiving, and restore drills live elsewhere.
  Do not claim the hosted service is backed up because a local
  snapshot exists.
- **A snapshot without the key is useless, and a key without the
  snapshot is useless.** Recovery needs both. If the user has
  only one, say exactly which half is missing and fix that half
  before calling anything "backed up".
- **Escrow is not sharing.** The escrowed key is for the user's
  own recovery. Never hand it to anyone else, never paste it
  into a chat, and never store it next to the snapshot.
- **Verify before trusting.** A snapshot is only good if its
  checksum matches and the escrowed key verifies. An unverified
  snapshot is a hope, not a backup.

## Example

User: "Data aku aman ga kalau laptop ini hilang?"

1. Check for a recent snapshot in the backup directory and
   verify its checksum (`sha256_file` against the recorded
   `sha256`).
2. Check the vault key escrow and `verify_escrowed_key`.
3. `check_key_separation` to confirm the escrow lives outside
   the data backup directory.
4. Report all three results plainly. If any half is missing, say
   which one and offer to create it now — do not say "you are
   backed up" on the strength of one half alone.
