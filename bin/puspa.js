#!/usr/bin/env node
// `puspa` command shim: `npm install -g puspa` puts this file on the
// user's PATH, and it hands off to the real Python entry point.
//
// Resolution order: the `puspa` console script installed by pip (the
// normal case — the postinstall step installs it), then `python -m
// puspa`, then `python3 -m puspa`. Arguments, stdin/stdout/stderr and
// the exit code pass through untouched, so this behaves exactly like
// running `puspa` directly.
//
// Self-recursion guard: npm and npx put the package's own `bin`
// directory FIRST on PATH when they run this file, so a bare `puspa`
// lookup would find this shim itself and spawn itself forever (seen
// 21 Nov 2026: `npx puspa --help` grew a 268-deep chain of node
// processes). The console script is therefore resolved by walking
// PATH and skipping any entry that is this file.
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

function findConsoleScript() {
  let self;
  try {
    self = fs.realpathSync(__filename);
  } catch {
    self = path.resolve(__filename);
  }
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (!dir) {
      continue;
    }
    const candidate = path.join(dir, "puspa");
    let resolved;
    try {
      resolved = fs.realpathSync(candidate);
    } catch {
      continue; // not present in this directory
    }
    if (resolved === self) {
      continue; // this shim — never spawn ourselves
    }
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
    } catch {
      continue;
    }
    return candidate;
  }
  return null;
}

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
  const consoleScript = findConsoleScript();
  const candidates = [];
  if (consoleScript !== null) {
    candidates.push([consoleScript, args]);
  }
  candidates.push(
    ["python", ["-m", "puspa", ...args]],
    ["python3", ["-m", "puspa", ...args]],
  );
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
