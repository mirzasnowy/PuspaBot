//! Local backend lifecycle: find, start, poll, stop.
//!
//! Database-agnostic: this module only runs `puspa serve` and talks
//! HTTP to it. Which database the backend uses (SQLite port or
//! Postgres first-run) is decided by the backend, not by this module.

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

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BackendStatus {
    Checking,
    Ready { base_url: String },
    Unavailable(String),
    Failed(String),
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
                if let Ok(health) = resp.json::<crate::api::Health>() {
                    if health.is_ok() {
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

/// Full probe, run on a worker thread at startup: find the backend,
/// start it on a free port, wait for `/health` = ok. On success the
/// child handle is stored in the static and the base URL is returned.
pub fn probe_and_start() -> BackendStatus {
    let Some(exe) = find_backend() else {
        return BackendStatus::Unavailable(
            "Backend Puspa belum terinstal di komputer ini. Pasang dulu lewat first-run setup."
                .to_string(),
        );
    };
    let port = match free_port() {
        Ok(port) => port,
        Err(err) => return BackendStatus::Failed(format!("tidak bisa cari port bebas: {err}")),
    };
    let child = match start_backend(&exe, port) {
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

/// Stop the backend, if one was started. Called on app exit.
pub fn shutdown() {
    if let Ok(mut guard) = BACKEND_CHILD.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn free_port_returns_a_bindable_port() {
        let port = free_port().unwrap();
        let listener = TcpListener::bind(("127.0.0.1", port)).unwrap();
        assert_eq!(listener.local_addr().unwrap().port(), port);
    }

    #[test]
    fn find_backend_in_searches_path_entries() {
        let dir = std::env::temp_dir().join("puspabot-test-find-backend");
        let _ = std::fs::create_dir_all(&dir);
        let fake = dir.join("puspa");
        std::fs::write(&fake, "#!/bin/sh\nexit 0\n").unwrap();
        let path_var = std::env::join_paths([&dir, Path::new("/nonexistent")]).unwrap();
        assert_eq!(find_backend_in(&path_var), Some(fake));
        let _ = std::fs::remove_file(dir.join("puspa"));
        let _ = std::fs::remove_dir(&dir);
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
}
