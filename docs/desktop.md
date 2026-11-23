# PuspaBot native desktop client

Native desktop app for PuspaBot: Rust + iced UI, no webview, no
HTML/CSS/JS. The Python brain stays Python — the native UI talks to
the local backend over the localhost API only, never to the
database directly.

## Build

```sh
cd apps/desktop
cargo build            # debug build
cargo build --release  # release build (LTO, single codegen unit)
cargo test             # unit tests (lifecycle manager, API client)
cargo fmt --check      # formatting
cargo clippy --all-targets -- -D warnings
```

Requires a stable Rust toolchain (`rustup toolchain install stable`).
The Windows installer cannot be built on a Linux machine; it is
produced by CI (`.github/workflows/desktop.yml`, `windows-latest`).

## Run (development)

The app starts the local backend itself: it looks for a `puspa`
binary on PATH, starts `puspa serve` on a free loopback port, polls
`GET /health` until it answers `ok`, then shows the native UI.
Closing the app stops the backend.

```sh
# from the repo root, with the Python backend installed:
pip install -e .          # or: npm install -g @puspaverse/puspa
cargo run --manifest-path apps/desktop/Cargo.toml
```

If no `puspa` binary is found, the app says so honestly on the
home screen instead of pretending to work.

## First run (installed app)

On first run the app downloads the backend (pip/npm `puspa`) if it
is not on PATH yet. The database for v1 is still an open decision
(full SQLite port vs Postgres installed at first run) — see the
desktop plan, section 11, in the goal workspace
(`hidden_files/desktop-cow10-plan.md`). Neither the port nor a
Postgres install is built into v1 until that decision lands.

## Package (Windows)

CI builds the release binary on `windows-latest` and runs NSIS
(`apps/desktop/installer/windows.nsi`) to produce
`PuspaBot-Setup.exe`, uploaded as a workflow artifact. The
installer puts `puspabot.exe` in Program Files with Start Menu and
Desktop shortcuts plus an uninstaller. Distribution: GitHub
Releases + a download link on the landing page.
