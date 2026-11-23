//! Local backend lifecycle: find, start, poll, stop.
//!
//! Database-agnostic: this module only runs `puspa serve` and talks
//! HTTP to it. Which database the backend uses (SQLite port or
//! Postgres first-run) is decided by the backend, not by this module.
//!
//! First run: when no `puspa` binary is on PATH, [`probe_and_start`]
//! downloads it (see [`download_backend`]) instead of giving up, so
//! a fresh install just works. The installer command is overridable
//! with `PUSPA_DESKTOP_INSTALL_CMD` for tests and power users.

use std::ffi::OsStr;
use std::io;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// The backend process handle, kept alive in a static so the UI
/// state (which must stay `Clone`-friendly for iced messages) never
/// has to carry it. Killed explicitly on app exit.
static BACKEND_CHILD: Mutex<Option<Child>> = Mutex::new(None);

/// Installer output lines, newest last, capped so a long install
/// cannot grow memory without bound. The UI polls [`install_log`].
static INSTALL_LOG: Mutex<Vec<String>> = Mutex::new(Vec::new());

/// The current first-run download step, if a download is running.
/// The UI polls [`download_step`]; [`current_download_status`]
/// turns it into a [`BackendStatus::Downloading`] value.
static DOWNLOAD_STEP: Mutex<Option<String>> = Mutex::new(None);

/// Maximum installer log lines kept in memory.
const MAX_LOG_LINES: usize = 500;

/// How many of the installer's last output lines a failure carries.
const FAILURE_TAIL_LINES: usize = 10;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BackendStatus {
    Checking,
    Downloading { step: String },
    Ready { base_url: String },
    Unavailable(String),
    Failed(String),
}

/// Installer output so far (oldest first). Empty when nothing has
/// been installed in this run.
pub fn install_log() -> Vec<String> {
    INSTALL_LOG.lock().expect("install log lock").clone()
}

fn push_log_line(line: String) {
    let mut log = INSTALL_LOG.lock().expect("install log lock");
    log.push(line);
    if log.len() > MAX_LOG_LINES {
        let excess = log.len() - MAX_LOG_LINES;
        log.drain(..excess);
    }
}

fn set_download_step(step: Option<String>) {
    *DOWNLOAD_STEP.lock().expect("download step lock") = step;
}

/// The current download step, if a first-run download is running.
pub fn download_step() -> Option<String> {
    DOWNLOAD_STEP.lock().expect("download step lock").clone()
}

/// The live download state as a status value, if downloading.
pub fn current_download_status() -> Option<BackendStatus> {
    download_step().map(|step| BackendStatus::Downloading { step })
}

/// Find a `puspa` backend binary on PATH (pip install) or, on
/// Windows, `puspa.exe` / `puspa.cmd` (npm install).
pub fn find_backend() -> Option<PathBuf> {
    let path_var = std::env::var_os("PATH")?;
    find_backend_in(&path_var)
}

pub fn find_backend_in(path_var: &OsStr) -> Option<PathBuf> {
    for dir in std::env::split_paths(path_var) {
        for name in ["puspa", "puspa.exe", "puspa.cmd"] {
            let candidate = dir.join(name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

/// Pick a free loopback port for the backend.
pub fn free_port() -> io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    Ok(listener.local_addr()?.port())
}

/// Spawn `puspa serve --host 127.0.0.1 --port <port>`.
/// The caller owns the child handle and must kill it (see
/// [`shutdown`]); std's `Command` has no kill-on-drop.
pub fn start_backend(exe: &Path, port: u16) -> io::Result<Child> {
    Command::new(exe)
        .arg("serve")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
}

/// Poll `GET <base_url>/health` until it answers `ok`, or time out.
/// Blocking (runs on a worker thread, never on the UI thread).
pub fn wait_ready_blocking(base_url: &str, timeout: Duration) -> Result<(), String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .map_err(|e| format!("could not build HTTP client: {e}"))?;
    let url = format!("{base_url}/health");
    let deadline = Instant::now() + timeout;
    loop {
        if let Ok(resp) = client.get(&url).send() {
            if resp.status().is_success() {
                // `{"status": "ok", ...}`; read as a plain JSON
                // value so this check never depends on the shape
                // of the API client's health type.
                if let Ok(body) = resp.json::<serde_json::Value>() {
                    if body.get("status").and_then(|s| s.as_str()) == Some("ok") {
                        return Ok(());
                    }
                }
            }
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "backend did not answer /health with ok within {timeout:?}"
            ));
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

/// Start the backend on a free port and wait for `/health` = ok.
/// On success the child handle is stored in the static and the
/// base URL is returned as [`BackendStatus::Ready`].
fn start_and_wait(exe: &Path) -> BackendStatus {
    let port = match free_port() {
        Ok(port) => port,
        Err(err) => return BackendStatus::Failed(format!("tidak bisa cari port bebas: {err}")),
    };
    let child = match start_backend(exe, port) {
        Ok(child) => child,
        Err(err) => {
            return BackendStatus::Failed(format!("gagal menyalakan backend: {err}"));
        }
    };
    let base_url = format!("http://127.0.0.1:{port}");
    match wait_ready_blocking(&base_url, Duration::from_secs(30)) {
        Ok(()) => {
            *BACKEND_CHILD.lock().expect("backend child lock") = Some(child);
            BackendStatus::Ready { base_url }
        }
        Err(reason) => {
            let mut child = child;
            let _ = child.kill();
            let _ = child.wait();
            BackendStatus::Failed(reason)
        }
    }
}

/// Full probe, run on a worker thread at startup: find the backend,
/// start it on a free port, wait for `/health` = ok. On success the
/// child handle is stored in the static and the base URL is returned.
/// When no backend binary is on PATH, this downloads it first
/// (see [`download_backend`]) instead of returning Unavailable.
pub fn probe_and_start() -> BackendStatus {
    let Some(exe) = find_backend() else {
        return download_backend();
    };
    start_and_wait(&exe)
}

/// Stop the backend, if one was started. Called on app exit.
pub fn shutdown() {
    if let Ok(mut guard) = BACKEND_CHILD.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

// ---------------------------------------------------------------------
// First-run download
// ---------------------------------------------------------------------

/// The `PUSPA_DESKTOP_INSTALL_CMD` override, if set to a non-empty
/// command (space-separated, e.g. `pip install puspa`). Tests use
/// it to point at a fake installer; real users never need it.
fn override_command() -> Option<Vec<String>> {
    let raw = std::env::var("PUSPA_DESKTOP_INSTALL_CMD").ok()?;
    let parts: Vec<String> = raw.split_whitespace().map(str::to_string).collect();
    if parts.is_empty() {
        None
    } else {
        Some(parts)
    }
}

/// True when an executable with this name exists on PATH.
fn executable_in(name: &str, path_var: &OsStr) -> bool {
    for dir in std::env::split_paths(path_var) {
        if dir.join(name).is_file() {
            return true;
        }
        // Windows installers land as `python.exe` / `npm.cmd`.
        if cfg!(windows) {
            for ext in ["exe", "cmd", "bat"] {
                if dir.join(format!("{name}.{ext}")).is_file() {
                    return true;
                }
            }
        }
    }
    false
}

/// The installer command for this machine: the
/// `PUSPA_DESKTOP_INSTALL_CMD` override wins; otherwise `python3 -m
/// pip install --user puspa` (`py -m pip install puspa` on
/// Windows) when Python is present, else `npm install -g
/// @puspaverse/puspa` when npm is present. `None` means no installer
/// is available at all.
pub fn install_command_in(path_var: &OsStr) -> Option<Vec<String>> {
    if let Some(cmd) = override_command() {
        return Some(cmd);
    }
    if cfg!(windows) {
        if executable_in("py", path_var) {
            return Some(
                ["py", "-m", "pip", "install", "puspa"]
                    .iter()
                    .map(|s| s.to_string())
                    .collect(),
            );
        }
    } else if executable_in("python3", path_var) {
        return Some(
            ["python3", "-m", "pip", "install", "--user", "puspa"]
                .iter()
                .map(|s| s.to_string())
                .collect(),
        );
    }
    if executable_in("npm", path_var) {
        return Some(
            ["npm", "install", "-g", "@puspaverse/puspa"]
                .iter()
                .map(|s| s.to_string())
                .collect(),
        );
    }
    None
}

/// The installer command for this machine, searching the real PATH.
pub fn install_command() -> Option<Vec<String>> {
    if let Some(cmd) = override_command() {
        return Some(cmd);
    }
    let path_var = std::env::var_os("PATH").unwrap_or_default();
    install_command_in(&path_var)
}

/// Run an installer command to completion, capturing its stdout and
/// stderr lines into the shared install log ([`install_log`]).
/// On a non-zero exit the error carries the installer's last output
/// lines, so the UI can show what actually went wrong.
pub fn run_installer(command: &[String]) -> Result<(), String> {
    let Some((program, args)) = command.split_first() else {
        return Err("installer command is empty".to_string());
    };
    let output = Command::new(program)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|err| format!("could not run installer {program}: {err}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    for line in stdout.lines().chain(stderr.lines()) {
        push_log_line(line.to_string());
    }
    if output.status.success() {
        return Ok(());
    }
    let log = INSTALL_LOG.lock().expect("install log lock");
    let tail: Vec<&str> = log
        .iter()
        .rev()
        .take(FAILURE_TAIL_LINES)
        .map(String::as_str)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    Err(format!(
        "installer exited with {} ({}); last output:\n{}",
        output.status,
        command.join(" "),
        tail.join("\n")
    ))
}

/// Install the backend with an explicit command, then look for the
/// `puspa` binary on the given PATH. Returns the binary path on
/// success. No real install happens when the command is a fake
/// installer (see the tests).
pub fn install_backend_with(command: &[String], path_var: &OsStr) -> Result<PathBuf, String> {
    run_installer(command)?;
    find_backend_in(path_var).ok_or_else(|| {
        "installer finished but no `puspa` binary was found on PATH afterwards".to_string()
    })
}

/// Download the backend with an explicit installer choice
/// (`None` = no installer available), then start it and wait for
/// `/health` = ok. The download step is published through
/// [`download_step`] while it runs, so the UI can show progress.
/// No installer available means the backend is [`BackendStatus::Unavailable`],
/// not a failure: nothing is installed and nothing went wrong.
pub fn download_backend_with(command: Option<Vec<String>>, path_var: &OsStr) -> BackendStatus {
    set_download_step(Some("Mengunduh backend Puspa...".to_string()));
    let status = match command {
        None => BackendStatus::Unavailable(
            "Backend Puspa belum terinstal dan tidak ada installer di komputer ini: \
             butuh python3 dengan pip, atau npm, di PATH untuk mengunduhnya."
                .to_string(),
        ),
        Some(command) => match install_backend_with(&command, path_var) {
            Ok(exe) => {
                set_download_step(Some("Menyalakan backend...".to_string()));
                start_and_wait(&exe)
            }
            Err(reason) => BackendStatus::Failed(reason),
        },
    };
    set_download_step(None);
    status
}

/// Download the backend with the detected installer, then start it
/// and wait for `/health` = ok. Blocking (runs on a worker thread).
pub fn download_backend() -> BackendStatus {
    let path_var = std::env::var_os("PATH").unwrap_or_default();
    download_backend_with(install_command(), &path_var)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

    /// A fresh temp dir per test, so parallel tests never share one.
    fn unique_temp_dir(tag: &str) -> PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir =
            std::env::temp_dir().join(format!("puspabot-test-{tag}-{}-{n}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        dir
    }

    /// Write a fake installer shell script into `dir` and return the
    /// command that runs it with `dir` as its argument. The script
    /// writes `body` to stdout/stderr, then either drops a fake
    /// `puspa` executable into `dir` and exits 0, or exits 3.
    #[cfg(unix)]
    fn fake_installer_script(dir: &Path, succeed: bool) -> (PathBuf, Vec<String>) {
        let script = dir.join("fake-installer.sh");
        let content = if succeed {
            "#!/bin/sh\n\
             echo 'fake installer: installing puspa'\n\
             printf '#!/bin/sh\\nexit 0\\n' > \"$1/puspa\"\n\
             chmod +x \"$1/puspa\"\n\
             echo 'fake installer: done' >&2\n"
        } else {
            "#!/bin/sh\n\
             echo 'fake installer: starting' \n\
             echo 'boom baris terakhir: fake failure' >&2\n\
             exit 3\n"
        };
        std::fs::write(&script, content).unwrap();
        let command = vec![
            "/bin/sh".to_string(),
            script.to_string_lossy().into_owned(),
            dir.to_string_lossy().into_owned(),
        ];
        (script, command)
    }

    #[test]
    fn free_port_returns_a_bindable_port() {
        let port = free_port().unwrap();
        let listener = TcpListener::bind(("127.0.0.1", port)).unwrap();
        assert_eq!(listener.local_addr().unwrap().port(), port);
    }

    #[test]
    fn find_backend_in_searches_path_entries() {
        let dir = unique_temp_dir("find-backend");
        let fake = dir.join("puspa");
        std::fs::write(&fake, "#!/bin/sh\nexit 0\n").unwrap();
        let path_var = std::env::join_paths([&dir, Path::new("/nonexistent")]).unwrap();
        assert_eq!(find_backend_in(&path_var), Some(fake));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn find_backend_in_returns_none_when_absent() {
        let path_var = std::env::join_paths([Path::new("/nonexistent-dir-xyz")]).unwrap();
        assert_eq!(find_backend_in(&path_var), None);
    }

    #[test]
    fn wait_ready_times_out_without_a_server() {
        let port = free_port().unwrap();
        let base_url = format!("http://127.0.0.1:{port}");
        let err = wait_ready_blocking(&base_url, Duration::from_millis(600)).unwrap_err();
        assert!(err.contains("did not answer"), "{err}");
    }

    #[test]
    #[cfg(unix)]
    fn fake_installer_success_is_found_by_find_backend() {
        let dir = unique_temp_dir("install-ok");
        let (_script, command) = fake_installer_script(&dir, true);
        let path_var = std::env::join_paths([&dir]).unwrap();
        let found = install_backend_with(&command, &path_var).unwrap();
        assert_eq!(found, dir.join("puspa"));
        assert!(found.is_file());
        assert_eq!(find_backend_in(&path_var), Some(dir.join("puspa")));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn no_installer_available_is_an_honest_failure() {
        let path_var = std::env::join_paths([Path::new("/nonexistent-dir-xyz")]).unwrap();
        assert_eq!(install_command_in(&path_var), None);
        let status = download_backend_with(None, &path_var);
        assert!(
            matches!(status, BackendStatus::Unavailable(_)),
            "expected Unavailable, got {status:?}"
        );
        if let BackendStatus::Unavailable(reason) = status {
            assert!(reason.contains("python3"), "{reason}");
            assert!(reason.contains("npm"), "{reason}");
        }
    }

    #[test]
    #[cfg(unix)]
    fn failing_installer_reports_its_last_output_lines() {
        let dir = unique_temp_dir("install-fail");
        let (_script, command) = fake_installer_script(&dir, false);
        let path_var = std::env::join_paths([&dir]).unwrap();
        let err = install_backend_with(&command, &path_var).unwrap_err();
        assert!(err.contains("boom baris terakhir"), "{err}");
        assert!(err.contains("fake failure"), "{err}");
        let status = download_backend_with(Some(command), &path_var);
        assert!(
            matches!(status, BackendStatus::Failed(_)),
            "expected Failed, got {status:?}"
        );
        if let BackendStatus::Failed(reason) = status {
            assert!(reason.contains("boom baris terakhir"), "{reason}");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn install_command_detects_python3_on_path() {
        // `python3` is on PATH in this environment; npm is only a
        // fallback, so the detected command must start with it.
        let path_var = std::env::var_os("PATH").unwrap_or_default();
        let command = install_command_in(&path_var).unwrap();
        assert_eq!(command[0], "python3", "{command:?}");
        assert!(command.contains(&"puspa".to_string()), "{command:?}");
    }
}
