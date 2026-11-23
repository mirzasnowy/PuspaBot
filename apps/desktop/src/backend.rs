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

/// First-run download progress in bytes: (downloaded, total).
/// Set while the backend wheel streams down from PyPI, so the UI
/// progress bar shows real bytes, not a spinner. `None` means no
/// download is running (or the total is unknown).
static DOWNLOAD_PROGRESS: Mutex<Option<(u64, u64)>> = Mutex::new(None);

/// Maximum installer log lines kept in memory.
const MAX_LOG_LINES: usize = 500;

/// How many of the installer's last output lines a failure carries.
const FAILURE_TAIL_LINES: usize = 10;

#[derive(Debug, Clone, PartialEq)]
pub enum BackendStatus {
    Checking,
    /// A first-run download is running. `progress` is the fraction
    /// downloaded (0.0..=1.0) when the total size is known (the
    /// PyPI wheel download); `None` means the step has no byte
    /// total (pip install phase, npm fallback, test installer).
    Downloading {
        step: String,
        progress: Option<f32>,
    },
    Ready {
        base_url: String,
    },
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
    let step = download_step()?;
    let progress = download_progress().and_then(|(downloaded, total)| {
        if total > 0 {
            Some((downloaded as f32 / total as f32).clamp(0.0, 1.0))
        } else {
            None
        }
    });
    Some(BackendStatus::Downloading { step, progress })
}

/// Download progress in bytes (downloaded, total), if a download
/// is running.
pub fn download_progress() -> Option<(u64, u64)> {
    *DOWNLOAD_PROGRESS.lock().expect("download progress lock")
}

fn set_download_progress(progress: Option<(u64, u64)>) {
    *DOWNLOAD_PROGRESS.lock().expect("download progress lock") = progress;
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

/// The full path of an executable with this name on PATH, if
/// any. The caller must spawn the full path: spawning the bare
/// name would resolve it through the process PATH instead, which
/// is not necessarily the PATH that was searched here.
fn find_executable(name: &str, path_var: &OsStr) -> Option<PathBuf> {
    for dir in std::env::split_paths(path_var) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate);
        }
        // Windows installers land as `python.exe` / `npm.cmd`.
        if cfg!(windows) {
            for ext in ["exe", "cmd", "bat"] {
                let candidate = dir.join(format!("{name}.{ext}"));
                if candidate.is_file() {
                    return Some(candidate);
                }
            }
        }
    }
    None
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

/// The PyPI index base URL. Overridable with
/// `PUSPA_PYPI_BASE_URL` for tests (a stub server) and power
/// users (a mirror). Defaults to https://pypi.org.
fn pypi_base_url() -> String {
    std::env::var("PUSPA_PYPI_BASE_URL")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "https://pypi.org".to_string())
}

/// Pick the installable file for the latest `puspa` release from a
/// PyPI JSON `urls` list: a wheel first (fast, no build), else the
/// sdist. Returns (filename, download URL).
fn pick_release_file(urls: &[serde_json::Value]) -> Option<(String, String)> {
    let mut sdist: Option<(String, String)> = None;
    for entry in urls {
        let filename = entry.get("filename")?.as_str()?.to_string();
        let url = entry.get("url")?.as_str()?.to_string();
        match entry.get("packagetype").and_then(|v| v.as_str()) {
            Some("bdist_wheel") => return Some((filename, url)),
            Some("sdist") if sdist.is_none() => sdist = Some((filename, url)),
            _ => {}
        }
    }
    sdist
}

/// Download the latest `puspa` release file from PyPI, streaming
/// it to a temp file with real byte progress (see
/// [`download_progress`]). Returns the downloaded file path.
/// Blocking; runs on a worker thread.
pub fn download_puspa_release(base_url: &str) -> Result<PathBuf, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| format!("could not build HTTP client: {e}"))?;
    let index_url = format!("{}/pypi/puspa/json", base_url.trim_end_matches('/'));
    let index: serde_json::Value = client
        .get(&index_url)
        .send()
        .and_then(|resp| resp.error_for_status())
        .map_err(|e| format!("could not read PyPI index {index_url}: {e}"))?
        .json()
        .map_err(|e| format!("PyPI index is not valid JSON: {e}"))?;
    let urls = index
        .get("urls")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let (filename, url) = pick_release_file(&urls)
        .ok_or_else(|| "PyPI has no installable file for the latest puspa release".to_string())?;
    let mut response = client
        .get(&url)
        .send()
        .and_then(|resp| resp.error_for_status())
        .map_err(|e| format!("could not download {url}: {e}"))?;
    let total = response.content_length().unwrap_or(0);
    let path = std::env::temp_dir().join(format!("puspabot-backend-{filename}"));
    let mut file = std::fs::File::create(&path)
        .map_err(|e| format!("could not create download file {}: {e}", path.display()))?;
    set_download_progress(Some((0, total)));
    let mut downloaded: u64 = 0;
    let mut chunk = [0u8; 65536];
    loop {
        use std::io::Read;
        match response.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => {
                use std::io::Write;
                file.write_all(&chunk[..n]).map_err(|e| {
                    format!("could not write download file {}: {e}", path.display())
                })?;
                downloaded += n as u64;
                set_download_progress(Some((downloaded, total)));
            }
            Err(err) => {
                return Err(format!(
                    "download of {url} failed after {downloaded} bytes: {err}"
                ))
            }
        }
    }
    set_download_progress(Some((downloaded, total)));
    Ok(path)
}

/// The pip command that installs one local file: `py -m pip
/// install <file>` on Windows, `python3 -m pip install --user
/// <file>` elsewhere. `None` when no Python is on PATH. The
/// Python path is absolute (see [`find_executable`]).
fn pip_install_command(file: &Path, path_var: &OsStr) -> Option<Vec<String>> {
    let python = if cfg!(windows) {
        find_executable("py", path_var)?
    } else {
        find_executable("python3", path_var)?
    };
    let mut command = vec![
        python.to_string_lossy().into_owned(),
        "-m".to_string(),
        "pip".to_string(),
        "install".to_string(),
    ];
    if !cfg!(windows) {
        command.push("--user".to_string());
    }
    command.push(file.to_string_lossy().into_owned());
    Some(command)
}

/// Install a downloaded release file with pip, then look for the
/// `puspa` binary on the given PATH. Returns the binary path.
/// The wheel file is deleted after a successful install.
///
/// Debian/Ubuntu system Python refuses `pip install` with PEP 668
/// (externally managed environment). The safe attempt runs first;
/// only when pip itself refuses with that error does the install
/// retry once with pip's own documented escape hatch
/// (`--break-system-packages`). The install target is the user
/// site, never the system site.
pub fn install_downloaded_release(file: &Path, path_var: &OsStr) -> Result<PathBuf, String> {
    let mut command = pip_install_command(file, path_var)
        .ok_or_else(|| "no Python on PATH to install the downloaded backend".to_string())?;
    set_download_step(Some("Memasang backend...".to_string()));
    set_download_progress(None);
    if let Err(reason) = run_installer(&command) {
        if reason.contains("break-system-packages") {
            match command.iter().position(|arg| arg == "install") {
                Some(at) => {
                    command.insert(at + 1, "--break-system-packages".to_string());
                    run_installer(&command)?;
                }
                None => return Err(reason),
            }
        } else {
            return Err(reason);
        }
    }
    let found = find_backend_in(path_var).ok_or_else(|| {
        "installer finished but no `puspa` binary was found on PATH afterwards".to_string()
    })?;
    let _ = std::fs::remove_file(file);
    Ok(found)
}

/// Download the backend, then start it and wait for `/health` =
/// ok. The download step and byte progress are published through
/// [`download_step`] / [`download_progress`] while it runs, so the
/// UI can show a real progress bar. No installer available means
/// the backend is [`BackendStatus::Unavailable`], not a failure:
/// nothing is installed and nothing went wrong.
///
/// `Some(command)` runs that installer command as-is (tests and
/// the `PUSPA_DESKTOP_INSTALL_CMD` override). `None` means no
/// explicit command: the latest `puspa` wheel streams down from
/// PyPI with byte progress and pip installs the local file when
/// Python is present; when Python is missing but npm is present,
/// `npm install -g @puspaverse/puspa` runs instead (no byte total,
/// step text only).
pub fn download_backend_with(command: Option<Vec<String>>, path_var: &OsStr) -> BackendStatus {
    set_download_step(Some("Mengunduh backend Puspa...".to_string()));
    set_download_progress(None);
    // The message names the launcher this machine actually looks
    // for (`py` on Windows, `python3` elsewhere).
    let python = if cfg!(windows) { "py" } else { "python3" };
    let status = match command {
        Some(command) => match install_backend_with(&command, path_var) {
            Ok(exe) => {
                set_download_step(Some("Menyalakan backend...".to_string()));
                start_and_wait(&exe)
            }
            Err(reason) => BackendStatus::Failed(reason),
        },
        None => {
            if find_executable(python, path_var).is_some() {
                match download_puspa_release(&pypi_base_url()) {
                    Ok(file) => match install_downloaded_release(&file, path_var) {
                        Ok(exe) => {
                            set_download_step(Some("Menyalakan backend...".to_string()));
                            start_and_wait(&exe)
                        }
                        Err(reason) => BackendStatus::Failed(reason),
                    },
                    Err(reason) => BackendStatus::Failed(reason),
                }
            } else if let Some(npm) = find_executable("npm", path_var) {
                let npm_command: Vec<String> = vec![
                    npm.to_string_lossy().into_owned(),
                    "install".to_string(),
                    "-g".to_string(),
                    "@puspaverse/puspa".to_string(),
                ];
                match install_backend_with(&npm_command, path_var) {
                    Ok(exe) => {
                        set_download_step(Some("Menyalakan backend...".to_string()));
                        start_and_wait(&exe)
                    }
                    Err(reason) => BackendStatus::Failed(reason),
                }
            } else {
                BackendStatus::Unavailable(format!(
                    "Backend Puspa belum terinstal dan tidak ada installer di komputer ini: \
                     butuh {python} dengan pip, atau npm, di PATH untuk mengunduhnya."
                ))
            }
        }
    };
    set_download_step(None);
    set_download_progress(None);
    status
}

/// Download the backend with the detected installer, then start it
/// and wait for `/health` = ok. Blocking (runs on a worker thread).
pub fn download_backend() -> BackendStatus {
    let path_var = std::env::var_os("PATH").unwrap_or_default();
    download_backend_with(override_command(), &path_var)
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
        let status = download_backend_with(None, &path_var);
        assert!(
            matches!(status, BackendStatus::Unavailable(_)),
            "expected Unavailable, got {status:?}"
        );
        if let BackendStatus::Unavailable(reason) = status {
            if cfg!(windows) {
                assert!(reason.contains("py"), "{reason}");
            } else {
                assert!(reason.contains("python3"), "{reason}");
            }
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

    /// A blocking stub PyPI server: serves `/pypi/puspa/json`
    /// (one sdist + one wheel) and the wheel file itself. Returns
    /// the base URL. Serves exactly the two requests the download
    /// makes, then the thread exits.
    fn stub_pypi_server(wheel_bytes: usize) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            let mut served = 0;
            for stream in listener.incoming().take(2) {
                let mut stream = match stream {
                    Ok(stream) => stream,
                    Err(_) => break,
                };
                let mut buf = [0u8; 4096];
                use std::io::Read;
                let n = stream.read(&mut buf).unwrap_or(0);
                let request = String::from_utf8_lossy(&buf[..n]);
                let path = request.split_whitespace().nth(1).unwrap_or("");
                let (status, content_type, body): (&str, &str, Vec<u8>) = if path
                    == "/pypi/puspa/json"
                {
                    let index = serde_json::json!({
                        "info": {"version": "0.1.2"},
                        "urls": [
                            {"filename": "puspa-0.1.2.tar.gz", "packagetype": "sdist",
                             "url": format!("http://127.0.0.1:{port}/files/puspa-0.1.2.tar.gz")},
                            {"filename": "puspa-0.1.2-py3-none-any.whl", "packagetype": "bdist_wheel",
                             "url": format!("http://127.0.0.1:{port}/files/puspa-0.1.2-py3-none-any.whl")},
                        ],
                    });
                    ("200 OK", "application/json", index.to_string().into_bytes())
                } else if path == "/files/puspa-0.1.2-py3-none-any.whl" {
                    (
                        "200 OK",
                        "application/octet-stream",
                        vec![b'W'; wheel_bytes],
                    )
                } else {
                    ("404 Not Found", "text/plain", b"nope".to_vec())
                };
                use std::io::Write;
                let _ = stream.write_all(
                    format!(
                        "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    )
                    .as_bytes(),
                );
                let _ = stream.write_all(&body);
                let _ = stream.flush();
                served += 1;
                if served >= 2 {
                    break;
                }
            }
        });
        format!("http://127.0.0.1:{port}")
    }

    #[test]
    fn pick_release_file_prefers_wheel_then_sdist() {
        let urls = serde_json::json!([
            {"filename": "puspa-0.1.2.tar.gz", "packagetype": "sdist", "url": "http://x/f.tar.gz"},
            {"filename": "puspa-0.1.2-py3-none-any.whl", "packagetype": "bdist_wheel", "url": "http://x/f.whl"},
        ]);
        let picked = pick_release_file(urls.as_array().unwrap()).unwrap();
        assert!(picked.0.ends_with(".whl"), "{picked:?}");
        let sdist_only = serde_json::json!([
            {"filename": "puspa-0.1.2.tar.gz", "packagetype": "sdist", "url": "http://x/f.tar.gz"},
        ]);
        let picked = pick_release_file(sdist_only.as_array().unwrap()).unwrap();
        assert!(picked.0.ends_with(".tar.gz"), "{picked:?}");
        assert_eq!(pick_release_file(&[]), None);
    }

    #[test]
    fn pypi_download_picks_the_wheel_and_reports_real_progress() {
        let base = stub_pypi_server(100_000);
        let path = download_puspa_release(&base).unwrap();
        assert!(
            path.file_name()
                .unwrap()
                .to_string_lossy()
                .ends_with(".whl"),
            "{path:?}"
        );
        assert_eq!(std::fs::metadata(&path).unwrap().len(), 100_000);
        assert_eq!(download_progress(), Some((100_000, 100_000)));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    #[cfg(unix)]
    fn install_downloaded_release_runs_pip_with_the_local_file() {
        // A fake `python3` that records its args and drops a fake
        // `puspa` binary into its own directory.
        let dir = unique_temp_dir("pip-wheel");
        let fake_py = dir.join("python3");
        std::fs::write(
            &fake_py,
            format!(
                "#!/bin/sh\n\
                 echo \"$@\" > \"{dir}/pip-args.txt\"\n\
                 printf '#!/bin/sh\\nexit 0\\n' > \"{dir}/puspa\"\n\
                 chmod +x \"{dir}/puspa\"\n",
                dir = dir.display()
            ),
        )
        .unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&fake_py, std::fs::Permissions::from_mode(0o755)).unwrap();
        let wheel = dir.join("puspa-0.1.2-py3-none-any.whl");
        std::fs::write(&wheel, b"fake wheel").unwrap();
        let path_var = std::env::join_paths([&dir]).unwrap();
        let found = install_downloaded_release(&wheel, &path_var).unwrap();
        assert_eq!(found, dir.join("puspa"));
        let args = std::fs::read_to_string(dir.join("pip-args.txt")).unwrap();
        assert!(args.contains("install"), "{args}");
        assert!(args.contains("puspa-0.1.2-py3-none-any.whl"), "{args}");
        assert!(
            !wheel.exists(),
            "the wheel must be deleted after a successful install"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn install_downloaded_release_retries_with_break_system_packages_on_pep_668() {
        // A fake `python3` that refuses the first pip call the way
        // Debian/Ubuntu system Python does (PEP 668), then
        // succeeds. The install must retry once with pip's own
        // escape hatch and succeed.
        let dir = unique_temp_dir("pip-pep668");
        let fake_py = dir.join("python3");
        std::fs::write(
            &fake_py,
            format!(
                "#!/bin/sh\n\
                 count_file=\"{dir}/pip-calls.txt\"\n\
                 n=$(cat \"$count_file\" 2>/dev/null || echo 0)\n\
                 n=$((n + 1))\n\
                 echo \"$n\" > \"$count_file\"\n\
                 echo \"$@\" > \"{dir}/pip-args-$n.txt\"\n\
                 if [ \"$n\" = \"1\" ]; then\n\
                 echo \"error: externally-managed-environment\" >&2\n\
                 echo \"by passing --break-system-packages\" >&2\n\
                 exit 1\n\
                 fi\n\
                 printf '#!/bin/sh\\nexit 0\\n' > \"{dir}/puspa\"\n\
                 chmod +x \"{dir}/puspa\"\n",
                dir = dir.display()
            ),
        )
        .unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&fake_py, std::fs::Permissions::from_mode(0o755)).unwrap();
        let wheel = dir.join("puspa-0.1.2-py3-none-any.whl");
        std::fs::write(&wheel, b"fake wheel").unwrap();
        let path_var = std::env::join_paths([&dir]).unwrap();
        let found = install_downloaded_release(&wheel, &path_var).unwrap();
        assert_eq!(found, dir.join("puspa"));
        let first = std::fs::read_to_string(dir.join("pip-args-1.txt")).unwrap();
        assert!(
            !first.contains("--break-system-packages"),
            "the safe attempt must run first: {first}"
        );
        let second = std::fs::read_to_string(dir.join("pip-args-2.txt")).unwrap();
        assert!(
            second.contains("--break-system-packages"),
            "the retry must carry the escape hatch: {second}"
        );
        assert!(
            !wheel.exists(),
            "the wheel must be deleted after a successful install"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
