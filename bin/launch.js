// Spawn helper shared by postinstall.js and puspa.js.
//
// `spawnSync("python3", args)` starts a real executable by name on
// every OS, and that stays the first thing tried here, unchanged.
// What it cannot do on Windows is start a `.cmd` / `.bat` file:
//   - libuv only appends `.com` and `.exe` when it searches PATH, so
//     a bare `python` that is really `python.bat` (pyenv-win ships its
//     shims that way) is reported as ENOENT, "not installed";
//   - Node 18.20.2+, 20.12.2+ and 21.7.3+ refuse to start a `.cmd` /
//     `.bat` by its full path too (EINVAL, the CVE-2024-27980 fix)
//     unless a shell is involved.
// So on Windows only, a command that was not found as an executable
// is looked up as `<name>.cmd` / `<name>.bat` on PATH, and a path that
// already ends in `.cmd` / `.bat` is run through `cmd.exe`. Elsewhere
// this is a plain spawnSync.
//
// It returns the same object spawnSync does: `error.code === "ENOENT"`
// still means "no such command", so callers keep their fallback chain.
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const WINDOWS = process.platform === "win32";

function isBatchFile(command) {
  return /\.(cmd|bat)$/i.test(command);
}

function findBatchFile(command) {
  if (command.includes("/") || command.includes("\\")) {
    return null; // a path, not a bare name: nothing to search for
  }
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (!dir) {
      continue;
    }
    for (const ext of [".cmd", ".bat"]) {
      const candidate = path.join(dir, command + ext);
      if (fs.existsSync(candidate)) {
        return candidate;
      }
    }
  }
  return null;
}

function quoteForCmd(arg) {
  // Inside double quotes cmd.exe treats & | < > ( ) ^ as plain text.
  // The two characters that cannot be made safe are " and %, so an
  // argument with either is refused instead of being mis-quoted.
  if (/["%]/.test(arg)) {
    throw new Error(`cannot pass ${JSON.stringify(arg)} through cmd.exe safely`);
  }
  return /^[A-Za-z0-9_@+:.\\/-]+$/.test(arg) ? arg : `"${arg}"`;
}

function spawnBatch(file, args, options) {
  // `cmd /d /s /c "<line>"`: /s makes cmd strip exactly the outer
  // pair of quotes, so quoted paths inside <line> survive intact.
  const line = [file, ...args].map(quoteForCmd).join(" ");
  return spawnSync(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", `"${line}"`], {
    ...options,
    windowsVerbatimArguments: true,
  });
}

function launch(command, args, options) {
  if (WINDOWS && isBatchFile(command)) {
    return spawnBatch(command, args, options);
  }
  const result = spawnSync(command, args, options);
  if (WINDOWS && result.error && result.error.code === "ENOENT") {
    const batch = findBatchFile(command);
    if (batch !== null) {
      return spawnBatch(batch, args, options);
    }
  }
  return result;
}

module.exports = { launch };
