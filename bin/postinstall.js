#!/usr/bin/env node
// Postinstall: fetch the Python package that this npm wrapper fronts.
//
// The npm package only carries this shim; the agent itself is the
// Python distribution `puspa` on PyPI. The wrapper version in
// package.json tracks the shim, while PYPI_VERSION below tracks the
// Python release the shim fronts — the two are 0.1.1 together now:
// the wrapper fix and the Python release that carries the Windows
// fcntl fix.
//
// Python lookup is cross-platform: `python3`, then `python`, then
// `py` (the Windows launcher — `python3` often does not exist on
// Windows). For each interpreter the install chain is, first
// success wins: plain `pip install`, then `pip install --user`,
// then `pip install --target <user site>` (the one that works on
// PEP 668 systems like Debian/Ubuntu, where a plain install is
// refused — and where `--user` is refused too, because pip checks
// the EXTERNALLY-MANAGED marker before looking at --user at all.
// --target is exempt from that check, installs flat into the
// interpreter's user site (which is on sys.path on every OS, so
// `python -m puspa` finds it right away), and never uninstalls a
// system package, so the Debian urllib3 problem never happens),
// then `pipx install` when pipx is installed. The install is
// best-effort on purpose: if
// every step fails, the npm install still succeeds and prints the
// exact command to run by hand, instead of failing the whole
// install for a Python-side problem.
//
// `PUSPA_LOCAL_WHEEL` (a path to a .whl file) overrides the PyPI
// target — used to verify the wrapper end to end before the first
// publish, when `puspa` is not on PyPI yet.
"use strict";

const { launch } = require("./launch.js");

const PYPI_VERSION = "0.1.1";
const PYTHONS = ["python3", "python", "py"];

function run(command, args, options) {
  const result = launch(command, args, {
    stdio: "inherit",
    ...(options || {}),
  });
  if (result.error && result.error.code === "ENOENT") {
    return null; // command not installed — the caller tries the next one
  }
  if (result.error) {
    throw result.error;
  }
  return result.status === 0;
}

function probe(python) {
  // True when the interpreter runs at all (its --version output is
  // irrelevant); false when the command does not exist.
  const result = launch(python, ["--version"], { stdio: "ignore" });
  if (result.error && result.error.code === "ENOENT") {
    return false;
  }
  return true;
}

function userSite(python) {
  // The interpreter's user site directory (on sys.path on every OS).
  // Null when the interpreter cannot tell us, in which case the
  // --target step is skipped.
  const result = launch(
    python,
    ["-c", "import site; print(site.getusersitepackages())"],
    { encoding: "utf-8" }
  );
  if (result.error || result.status !== 0) {
    return null;
  }
  const site = (result.stdout || "").trim();
  return site || null;
}

function pipInstall(python, target, extraArgs) {
  return run(python, ["-m", "pip", "install", "--quiet", ...extraArgs, target]);
}

function pipxInstall(target) {
  return run("pipx", ["install", target]);
}

function install(target) {
  let first = null; // { python, site } of the first interpreter found
  for (const python of PYTHONS) {
    if (!probe(python)) {
      continue; // no such interpreter — try the next one
    }
    const site = userSite(python);
    if (first === null) {
      first = { python, site };
    }
    let ok;
    try {
      ok = pipInstall(python, target, []);
    } catch (error) {
      console.error(`puspa postinstall: pip failed to run (${error.message})`);
      continue;
    }
    if (ok) {
      return { ok: true, first };
    }
    try {
      ok = pipInstall(python, target, ["--user"]);
    } catch (error) {
      console.error(`puspa postinstall: pip failed to run (${error.message})`);
      continue;
    }
    if (ok) {
      return { ok: true, first };
    }
    // Plain and --user both refused (PEP 668). Retry flat into
    // the interpreter's user site, which pip exempts from the
    // check and which is on sys.path on every OS.
    if (site !== null) {
      try {
        ok = pipInstall(python, target, ["--target", site]);
      } catch (error) {
        console.error(`puspa postinstall: pip failed to run (${error.message})`);
        continue;
      }
      if (ok) {
        return { ok: true, first };
      }
    }
  }
  // Last resort: an isolated pipx install, when pipx is installed.
  let ok = null;
  try {
    ok = pipxInstall(target);
  } catch (error) {
    console.error(`puspa postinstall: pipx failed to run (${error.message})`);
  }
  return { ok: ok === true, first };
}

function main() {
  const localWheel = process.env.PUSPA_LOCAL_WHEEL;
  const target = localWheel ? localWheel : `puspa==${PYPI_VERSION}`;
  let result = { ok: false, first: null };
  try {
    result = install(target);
  } catch (error) {
    console.error(`puspa postinstall: install failed (${error.message})`);
  }
  if (!result.ok) {
    if (result.first === null) {
      console.error(
        "puspa postinstall: no Python 3 found on PATH.\n" +
          "Install Python 3.12 or newer, then run `puspa` again."
      );
    } else {
      const { python, site } = result.first;
      const manual =
        site !== null
          ? `${python} -m pip install --target ${site} ${target}`
          : `${python} -m pip install ${target}`;
      console.error(
        "puspa postinstall: could not install the Python package automatically.\n" +
          `Install it by hand with: ${manual}\n` +
          `Or with pipx: pipx install ${target}\n` +
          "Then run `puspa` again."
      );
    }
  }
  process.exit(0); // never fail the npm install for a pip problem
}

main();
