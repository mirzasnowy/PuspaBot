#!/usr/bin/env python3
"""Push the refactored puspa-ai repo via the Git Data API.

Rewrites the 9 original commits to English messages (same trees), then
replays the local refactor commits on top (same trees + messages), then
force-updates main. Uses the custom.github helper's call() (never
contains a real token; never extract/print one).

Safety: verifies remote main is still 547673a immediately before the
ref update; if it moved, STOP and report. Never uses `git push`
(the git CLI has no credentials).

Usage: python3 push_puspa.py [--dry-run]
  --dry-run: print the plan (commit list + new head) without writing.
"""
import subprocess
import sys

sys.path.insert(0, "/home/hatch/workspace/custom-connectors/custom.github/bin")
from github import call  # noqa: E402

OWNER, REPO = "mirzasnowy", "puspa-ai"
EXPECTED_MAIN = "547673a4cc389194131cf1edaa3389f8a9a443b9"
DRY = "--dry-run" in sys.argv

AUTHOR = {"name": "Muhammad Mirza",
          "email": "114640561+mirzasnowy@users.noreply.github.com"}

# Original 9 commits, oldest first: (old_sha, tree_sha, new English message)
ORIGINAL = [
    ("4df190c52f965a66f1a74c792db90c7cc1ade741",
     "34cd159f1224abffece2579badef742c42faf4e1",
     "Add README"),
    ("8e91a923824af66308c98b861af3b5dbe01d048a",
     "5064c72fd7b12a7abd2b42eaab37ef2d54da8fc2",
     "Phase 0: security + backbone + chassis integration (revision 7)"),
    ("657ee781465542f7482f8b939778ab51e7644d0c",
     "834c8342c6058ebb387b00ed2db4aed219a9ad27",
     "Record phase 0 push + fresh-clone verification"),
    ("019c0afdce8a14eae6b417feda354dade1af3a95",
     "8631e0974ccbc44f5c3c691cd1bbacd197eecddf",
     "Phase 0: filter get_definitions per token + pin dependencies + reuse log"),
    ("c8060af43223b97b6b10ece4650cad2238988c53",
     "194a193222c9f67d7c45e60a5e1807e27b5520b4",
     "Phase 1: Persona + Memory + Onboarding"),
    ("e4f4392bc7f1b47fceeac50a0563255dc51744a1",
     "9ca930373b538d099b90b36c8c642fea3aeffadd",
     "Phase 2: Runtime + PWA + Connectors + Scheduler"),
    ("9dfff44d41c6afccec3cd67aa9a3fde020faf395",
     "1059e0fead315390944ef4939ff76ccd7729480e",
     "Phase 3: Wedges + Moat"),
    ("596d5610b6ebc1f33f46e8c7433d559cb3963975",
     "10dc0eff920f3f77304281c7ecdbbbbd5283447d",
     "Phase 4: Workflows + Custom modules"),
    ("547673a4cc389194131cf1edaa3389f8a9a443b9",
     "77ce743671b271a40daf550626aca403bed09fe0",
     "Remove __pycache__ files accidentally included in push"),
]


def git(*args):
    return subprocess.run(["git", *args], cwd="/home/hatch/workspace/puspa-refactor",
                          capture_output=True, text=True, check=True).stdout.strip()


def main():
    # 1. Verify remote main is still the expected SHA (force-with-lease).
    status, ref = call("GET", f"/repos/{OWNER}/{REPO}/git/refs/heads/main")
    if status != 200:
        print(f"STOP: cannot read remote main (HTTP {status}): {ref}"); sys.exit(1)
    remote_main = ref["object"]["sha"]
    print(f"remote main: {remote_main}")
    if remote_main != EXPECTED_MAIN:
        print(f"STOP: remote main moved (expected {EXPECTED_MAIN}). Aborting.")
        sys.exit(1)

    # 2. Local refactor commits (oldest first), with their trees + messages.
    local = git("log", "--format=%H %T %s", "547673a..HEAD").splitlines()
    # git log lists newest first; reverse to oldest first.
    refactor = []
    for line in reversed(local):
        sha, tree, msg = line.split(" ", 2)
        refactor.append((sha, tree, msg))
    print(f"local refactor commits: {len(refactor)}")
    for sha, tree, msg in refactor:
        print(f"  {sha[:7]} {tree[:7]} {msg}")

    # 3. Build the new history.
    plan = []
    parent = None
    for _old, tree, msg in ORIGINAL:
        plan.append((tree, msg, parent))
        parent = None  # placeholder; filled after creation
    for _sha, tree, msg in refactor:
        plan.append((tree, msg, parent))

    if DRY:
        print("\nDRY RUN — plan:")
        for i, (tree, msg, _p) in enumerate(plan):
            print(f"  {i+1:2d}. tree {tree[:7]}  {msg}")
        print(f"\nWould create {len(plan)} commits and force-update main.")
        return

    # Create commits via the Git Data API.
    new_parent = None
    new_shas = []
    for tree, msg, _ in plan:
        body = {"message": msg, "tree": tree,
                "author": AUTHOR, "committer": AUTHOR}
        if new_parent:
            body["parents"] = [new_parent]
        else:
            body["parents"] = []
        status, created = call("POST", f"/repos/{OWNER}/{REPO}/git/commits", body)
        if status != 201:
            print(f"STOP: commit create failed (HTTP {status}): {created}"); sys.exit(1)
        new_parent = created["sha"]
        new_shas.append(new_parent)
        print(f"created {new_parent[:7]}  {msg}")
    new_head = new_shas[-1]

    # 4. Force-update main (verify again right before the write).
    status, ref2 = call("GET", f"/repos/{OWNER}/{REPO}/git/refs/heads/main")
    if status != 200 or ref2["object"]["sha"] != EXPECTED_MAIN:
        print("STOP: remote main moved during push. New commits created but "
              "main NOT updated. New head would have been: " + new_head)
        sys.exit(1)
    status, _ = call("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/main",
         {"sha": new_head, "force": True})
    if status != 200:
        print(f"STOP: ref update failed (HTTP {status}). New head would have been: {new_head}"); sys.exit(1)
    print(f"\nmain updated: {EXPECTED_MAIN[:7]} -> {new_head[:7]}")
    print(f"new head: {new_head}")
    print(f"total commits: {len(new_shas)}")


if __name__ == "__main__":
    main()
