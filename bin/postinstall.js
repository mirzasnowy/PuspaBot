#!/usr/bin/env node
// Postinstall: fetch the Python package that this npm wrapper fronts.
//
// The npm package only carries this shim; the agent itself is the
// Python distribution `puspa` on PyPI. The wrapper version in
// package.json tracks the shim, while PYPI_VERSION below tracks the
// Python release the shim fronts — the two diverge on purpose (the
// wrapper 0.1.1 fixes the install, the Python package stays 0.1.0).
//
// Install chain, first success wins: plain `pip install`, then
// `pip install --user` (the one that works on PEP 668 systems like
// Debian/Ubuntu, where a plain install is refused), then `pipx
// install` when pipx is installed. The install is best-effort on
// purpose: if every step fails, the npm install still succeeds and
// prints the exact command to run by hand, instead of failing the
// whole install for a Python-side problem.
//
// `PUSPA_LOCAL_WHEEL` (a path to a .whl file) overrides the PyPI
// target — used to verify the wrapper end to end before the first
// publish, when `puspa` is not on PyPI yet.
"use strict";

const { spawnSync } = require("node:child_process");

const PYPI_VERSION = "0.1.0";

function run(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.error && result.error.code === "ENOENT") {
    return null; // command not installed — the caller tries the next one
  }
  if (result.error) {
    throw result.error;
  }
  return result.status === 0;
}

function pipInstall(python, target, extraArgs) {
  return run(python, ["-m", "pip", "install", "--quiet", ...extraArgs, target]);
}

function pipxInstall(target) {
  return run("pipx", ["install", target]);
}

function install(target) {
  for (const python of ["python3", "python"]) {
    let ok = null;
    try {
      ok = pipInstall(python, target, []);
    } catch (error) {
      console.error(`puspa postinstall: pip failed to run (${error.message})`);
      continue;
    }
    if (ok === null) {
      continue; // no such python — try the next one
    }
    if (ok) {
      return true;
    }
    // Plain install failed (PEP 668 on Debian/Ubuntu refuses it).
    // Retry into the user site before giving up on pip.
    try {
      ok = pipInstall(python, target, ["--user"]);
    } catch (error) {
      console.error(`puspa postinstall: pip failed to run (${error.message})`);
      continue;
    }
    if (ok === null) {
      continue;
    }
    if (ok) {
      return true;
    }
  }
  // Last resort: an isolated pipx install, when pipx is installed.
  let ok = null;
  try {
    ok = pipxInstall(target);
  } catch (error) {
    console.error(`puspa postinstall: pipx failed to run (${error.message})`);
  }
  return ok === true;
}

function main() {
  const localWheel = process.env.PUSPA_LOCAL_WHEEL;
  const target = localWheel ? localWheel : `puspa==${PYPI_VERSION}`;
  let ok = false;
  try {
    ok = install(target);
  } catch (error) {
    console.error(`puspa postinstall: install failed (${error.message})`);
  }
  if (!ok) {
    console.error(
      "puspa postinstall: could not install the Python package automatically.\n" +
        `Install it by hand with: python3 -m pip install --user ${target}\n` +
        `Or with pipx: pipx install ${target}\n` +
        "Then run `puspa` again."
    );
  }
  process.exit(0); // never fail the npm install for a pip problem
}

main();
