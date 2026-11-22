#!/usr/bin/env node
// `puspa` command shim: `npm install -g puspa` puts this file on the
// user's PATH, and it hands off to the real Python entry point.
//
// Resolution order: the `puspa` console script installed by pip (the
// normal case — the postinstall step installs it; on Windows it is
// `puspa.exe`), then `python -m puspa`, then `python3 -m puspa`,
// then `py -m puspa` (the Windows launcher). Arguments,
// stdin/stdout/stderr and the exit code pass through untouched, so
// this behaves exactly like running `puspa` directly.
//
// Self-recursion guard: npm and npx put the package's own `bin`
// directory FIRST on PATH when they run this file, so a bare `puspa`
// lookup would find this shim itself and spawn itself forever (seen
// 21 Nov 2026: `npx puspa --help` grew a 268-deep chain of node
// processes). The console script is therefore resolved by walking
// PATH and skipping any entry that is this file — on POSIX the
// symlink to it, on Windows npm's `puspa.cmd` shim, which is
// recognised because its text calls `bin/puspa.js`.
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { launch } = require("./launch.js");

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
    // The console script is `puspa` on POSIX and `puspa.exe` on
    // Windows; npm's own bin shim is `puspa.cmd`. A `.cmd` entry is
    // only the console script when it does not wrap this shim — npm
    // generates a `.cmd` that calls `bin/puspa.js`, so one whose
    // text mentions `puspa.js` is this shim again and must be
    // skipped, exactly like the POSIX symlink to it.
    for (const name of ["puspa", "puspa.exe", "puspa.cmd"]) {
      const candidate = path.join(dir, name);
      let resolved;
      try {
        resolved = fs.realpathSync(candidate);
      } catch {
        continue; // not present in this directory
      }
      if (resolved === self) {
        continue; // this shim — never spawn ourselves
      }
      if (name === "puspa.cmd") {
        let text = "";
        try {
          text = fs.readFileSync(candidate, "utf-8");
        } catch {
          continue;
        }
        if (text.toLowerCase().includes("puspa.js")) {
          continue; // npm's shim around this file, not the console script
        }
      }
      try {
        fs.accessSync(candidate, fs.constants.X_OK);
      } catch {
        continue;
      }
      return candidate;
    }
  }
  return null;
}

function tryRun(command, args) {
  const result = launch(command, args, { stdio: "inherit" });
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
    ["py", ["-m", "puspa", ...args]],
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
