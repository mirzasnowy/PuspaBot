#!/usr/bin/env node
// `puspa` command shim: `npm install -g puspa` puts this file on the
// user's PATH, and it hands off to the real Python entry point.
//
// Resolution order: the `puspa` console script installed by pip (the
// normal case — the postinstall step installs it), then `python -m
// puspa`, then `python3 -m puspa`. Arguments, stdin/stdout/stderr and
// the exit code pass through untouched, so this behaves exactly like
// running `puspa` directly.
"use strict";

const { spawnSync } = require("node:child_process");

function tryRun(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.error && result.error.code === "ENOENT") {
    return null; // command not found — try the next candidate
  }
  if (result.error) {
    throw result.error;
  }
  return result.status;
}

function main() {
  const args = process.argv.slice(2);
  const candidates = [
    ["puspa", args],
    ["python", ["-m", "puspa", ...args]],
    ["python3", ["-m", "puspa", ...args]],
  ];
  for (const [command, commandArgs] of candidates) {
    const status = tryRun(command, commandArgs);
    if (status !== null) {
      process.exit(status === null ? 1 : status);
    }
  }
  console.error(
    "puspa is not installed: the Python package is missing.\n" +
      "Install it with: pip install puspa\n" +
      "Then run `puspa` again."
  );
  process.exit(1);
}

main();
