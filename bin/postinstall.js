#!/usr/bin/env node
// Postinstall: fetch the Python package that this npm wrapper fronts.
//
// The npm package only carries this shim; the agent itself is the
// Python distribution `puspa` (same version as this package). The
// install is best-effort on purpose: if pip is missing or the
// install fails, the npm install still succeeds and prints the exact
// command to run by hand, instead of failing the whole install for
// a Python-side problem. The shim also falls back to `python -m
// puspa`, so a manual `pip install puspa` afterwards just works.
//
// `PUSPA_LOCAL_WHEEL` (a path to a .whl file) overrides the PyPI
// install — used to verify the wrapper end to end before the first
// publish, when `puspa` is not on PyPI yet.
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

function pipInstall(target) {
  for (const python of ["python3", "python"]) {
    const result = spawnSync(python, ["-m", "pip", "install", "--quiet", target], {
      stdio: "inherit",
    });
    if (result.error && result.error.code === "ENOENT") {
      continue; // no such python — try the next one
    }
    if (result.error) {
      throw result.error;
    }
    return result.status === 0;
  }
  return false;
}

function main() {
  const pkg = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf-8")
  );
  const localWheel = process.env.PUSPA_LOCAL_WHEEL;
  const target = localWheel ? localWheel : `puspa==${pkg.version}`;
  let ok = false;
  try {
    ok = pipInstall(target);
  } catch (error) {
    console.error(`puspa postinstall: pip failed to run (${error.message})`);
  }
  if (!ok) {
    console.error(
      "puspa postinstall: could not install the Python package automatically.\n" +
        `Install it by hand with: pip install ${localWheel ? localWheel : `puspa==${pkg.version}`}\n` +
        "Then run `puspa` again."
    );
  }
  process.exit(0); // never fail the npm install for a pip problem
}

main();
