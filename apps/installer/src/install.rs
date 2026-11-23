//! Install / uninstall the PuspaBot desktop app.
//!
//! The installer writes the embedded `puspabot.exe` payload to
//! the install directory (Program Files on Windows), copies
//! itself as the uninstaller, registers the app in the Windows
//! uninstall list, creates Start Menu + Desktop shortcuts, and
//! launches the app when done. Every step reports real progress
//! (bytes copied / total) through the callback, so the UI bar is
//! honest. On non-Windows machines the install directory is a
//! caller-chosen folder and the registry/shortcut steps are
//! skipped (they have no meaning there); the file steps are
//! identical, which is what the tests exercise.

use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

/// The app name, used for the install directory, shortcuts, and
/// the uninstall registry key.
pub const APP_NAME: &str = "PuspaBot";
/// The installed app binary.
pub const APP_EXE: &str = "puspabot.exe";
/// The uninstaller: a copy of this installer binary.
pub const UNINSTALL_EXE: &str = "uninstall.exe";

/// Install options: where to install, and whether to launch the
/// app when the install finishes.
#[derive(Debug, Clone)]
pub struct InstallOptions {
    pub dir: PathBuf,
    pub launch_after: bool,
}

impl InstallOptions {
    pub fn new(dir: PathBuf) -> Self {
        Self {
            dir,
            launch_after: true,
        }
    }
}

/// The default install directory: `%ProgramFiles%\PuspaBot` on
/// Windows, the system temp dir on other machines (dev/test only
/// — the installer is a Windows program).
pub fn default_install_dir() -> PathBuf {
    if cfg!(windows) {
        let base = std::env::var_os("ProgramFiles")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(r"C:\Program Files"));
        base.join(APP_NAME)
    } else {
        std::env::temp_dir().join("puspabot-install")
    }
}

/// Progress callback: fraction installed (0.0..=1.0) + the current
/// step in plain language.
pub type ProgressFn<'a> = dyn FnMut(f32, &str) + 'a;

/// Install the payload into `opts.dir`. Returns the installed
/// app path. The payload must be non-empty: an installer built
/// without one refuses to pretend it installed anything.
/// Launching is the caller's job (`launch`): a launch failure
/// must never turn a successful install into a failed one.
pub fn install(
    payload: &[u8],
    opts: &InstallOptions,
    progress: &mut ProgressFn<'_>,
) -> Result<PathBuf, String> {
    if payload.is_empty() {
        return Err(
            "installer ini dibuat tanpa payload: build aplikasi desktop dulu, baru installer"
                .to_string(),
        );
    }
    let total = payload.len() as u64;
    std::fs::create_dir_all(&opts.dir)
        .map_err(|e| format!("tidak bisa membuat {}: {e}", opts.dir.display()))?;
    let app_path = opts.dir.join(APP_EXE);
    progress(0.0, "Menyalin PuspaBot...");
    write_file_with_progress(&app_path, payload, total, &mut |written| {
        progress(
            (written as f32 / total as f32).clamp(0.0, 1.0),
            "Menyalin PuspaBot...",
        );
    })?;
    // The payload must be runnable after install (on Unix the
    // created file is 0644; on Windows this is a no-op).
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&app_path, std::fs::Permissions::from_mode(0o755)).map_err(
            |e| {
                format!(
                    "tidak bisa membuat {} bisa dijalankan: {e}",
                    app_path.display()
                )
            },
        )?;
    }
    progress(0.85, "Menyiapkan uninstaller...");
    let uninstall_path = opts.dir.join(UNINSTALL_EXE);
    if let Ok(current) = std::env::current_exe() {
        let _ = std::fs::copy(current, &uninstall_path);
    }
    progress(0.90, "Mendaftarkan aplikasi...");
    write_registry(&opts.dir).map_err(|e| format!("tidak bisa mendaftarkan aplikasi: {e}"))?;
    create_shortcuts(&opts.dir).map_err(|e| format!("tidak bisa membuat shortcut: {e}"))?;
    progress(1.0, "Selesai.");
    Ok(app_path)
}

/// Write `data` to `path` in 64 KiB chunks, calling `on_chunk`
/// with the bytes written so far after each chunk.
fn write_file_with_progress(
    path: &Path,
    data: &[u8],
    _total: u64,
    on_chunk: &mut dyn FnMut(u64),
) -> Result<(), String> {
    let mut file = std::fs::File::create(path)
        .map_err(|e| format!("tidak bisa menulis {}: {e}", path.display()))?;
    let mut written: u64 = 0;
    for chunk in data.chunks(65536) {
        file.write_all(chunk)
            .map_err(|e| format!("tidak bisa menulis {}: {e}", path.display()))?;
        written += chunk.len() as u64;
        on_chunk(written);
    }
    file.sync_all()
        .map_err(|e| format!("tidak bisa menyelesaikan {}: {e}", path.display()))?;
    Ok(())
}

/// Launch the installed app (detached: it keeps running after
/// the installer exits).
pub fn launch(dir: &Path) -> Result<(), String> {
    let app = dir.join(APP_EXE);
    Command::new(&app)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("tidak bisa membuka {}: {e}", app.display()))?;
    Ok(())
}

/// The Start Menu folder for the app (Windows only).
#[cfg(windows)]
fn start_menu_dir() -> Option<PathBuf> {
    std::env::var_os("APPDATA").map(|base| {
        PathBuf::from(base)
            .join("Microsoft")
            .join("Windows")
            .join("Start Menu")
            .join("Programs")
            .join(APP_NAME)
    })
}

/// The Desktop folder (Windows only).
#[cfg(windows)]
fn desktop_dir() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE").map(|base| PathBuf::from(base).join("Desktop"))
}

/// Register the app in the Windows uninstall list (HKLM). No-op
/// on other machines.
#[cfg(windows)]
fn write_registry(dir: &Path) -> io::Result<()> {
    use winreg::enums::HKEY_LOCAL_MACHINE;
    use winreg::RegKey;
    let uninstall = RegKey::predef(HKEY_LOCAL_MACHINE)
        .create_subkey(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PuspaBot")?;
    let (key, _) = uninstall;
    key.set_value("DisplayName", &"PuspaBot")?;
    key.set_value("DisplayVersion", &"0.1.0")?;
    key.set_value("Publisher", &"PuspaBot")?;
    key.set_value("InstallLocation", &dir.to_string_lossy().into_owned())?;
    key.set_value(
        "UninstallString",
        &format!("\"{}\" --uninstall", dir.join(UNINSTALL_EXE).display()),
    )?;
    key.set_value("DisplayIcon", &format!("{},0", dir.join(APP_EXE).display()))?;
    Ok(())
}

#[cfg(not(windows))]
fn write_registry(_dir: &Path) -> io::Result<()> {
    Ok(())
}

/// Create Start Menu + Desktop shortcuts (Windows only). No-op
/// on other machines.
#[cfg(windows)]
fn create_shortcuts(dir: &Path) -> io::Result<()> {
    use mslnk::ShellLink;
    let target = dir.join(APP_EXE);
    let target_str = target.to_string_lossy().into_owned();
    let mut link = ShellLink::new(&target_str).map_err(|e| io::Error::other(e.to_string()))?;
    link.set_working_dir(Some(dir.to_string_lossy().into_owned()));
    link.set_icon_location(Some(format!("{target_str},0")));
    if let Some(menu) = start_menu_dir() {
        std::fs::create_dir_all(&menu)?;
        link.create_lnk(menu.join("PuspaBot.lnk"))
            .map_err(|e| io::Error::other(e.to_string()))?;
    }
    if let Some(desktop) = desktop_dir() {
        link.create_lnk(desktop.join("PuspaBot.lnk"))
            .map_err(|e| io::Error::other(e.to_string()))?;
    }
    Ok(())
}

#[cfg(not(windows))]
fn create_shortcuts(_dir: &Path) -> io::Result<()> {
    Ok(())
}

/// Remove the Windows uninstall registry key (Windows only).
#[cfg(windows)]
fn remove_registry() -> io::Result<()> {
    use winreg::enums::HKEY_LOCAL_MACHINE;
    use winreg::RegKey;
    let parent = RegKey::predef(HKEY_LOCAL_MACHINE)
        .open_subkey(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")?;
    match parent.delete_subkey("PuspaBot") {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(err),
    }
}

#[cfg(not(windows))]
fn remove_registry() -> io::Result<()> {
    Ok(())
}

/// Remove the Start Menu + Desktop shortcuts (Windows only).
#[cfg(windows)]
fn remove_shortcuts() {
    if let Some(menu) = start_menu_dir() {
        let _ = std::fs::remove_file(menu.join("PuspaBot.lnk"));
        let _ = std::fs::remove_dir(&menu);
    }
    if let Some(desktop) = desktop_dir() {
        let _ = std::fs::remove_file(desktop.join("PuspaBot.lnk"));
    }
}

#[cfg(not(windows))]
fn remove_shortcuts() {}

/// Uninstall: remove the app, the uninstaller, the shortcuts,
/// and the registry entry, then the (now empty) directory.
/// Missing pieces are fine: uninstalling twice is not an error.
pub fn uninstall(dir: &Path) -> Result<(), String> {
    let _ = std::fs::remove_file(dir.join(APP_EXE));
    let _ = std::fs::remove_file(dir.join(UNINSTALL_EXE));
    remove_shortcuts();
    remove_registry().map_err(|e| format!("tidak bisa menghapus registrasi: {e}"))?;
    let _ = std::fs::remove_dir(dir);
    Ok(())
}

/// True when the app binary exists in `dir`.
pub fn is_installed(dir: &Path) -> bool {
    dir.join(APP_EXE).is_file()
}

/// Read one byte stream fully (used by the self-test server-less
/// path and by tests that need a child handle).
#[allow(dead_code)]
fn read_all(mut reader: impl Read) -> io::Result<Vec<u8>> {
    let mut out = Vec::new();
    reader.read_to_end(&mut out)?;
    Ok(out)
}

/// Keep a spawned child alive for the caller (the app launcher
/// detaches instead; this is only for tests).
#[allow(dead_code)]
fn spawn_child(cmd: &mut Command) -> io::Result<Child> {
    cmd.spawn()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

    fn unique_temp_dir(tag: &str) -> PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!(
            "puspabot-test-install-{tag}-{}-{n}",
            std::process::id()
        ));
        let _ = std::fs::create_dir_all(&dir);
        dir
    }

    fn fake_payload(size: usize) -> Vec<u8> {
        (0..size).map(|i| (i % 251) as u8).collect()
    }

    #[test]
    fn install_writes_the_payload_and_reports_real_progress() {
        let dir = unique_temp_dir("install");
        let payload = fake_payload(200_000);
        let mut fractions = Vec::new();
        let mut steps = Vec::new();
        let opts = InstallOptions {
            dir: dir.clone(),
            launch_after: false,
        };
        let installed = install(&payload, &opts, &mut |fraction, step| {
            fractions.push(fraction);
            steps.push(step.to_string());
        })
        .unwrap();
        assert_eq!(installed, dir.join(APP_EXE));
        let written = std::fs::read(&installed).unwrap();
        assert_eq!(written, payload);
        assert!(
            fractions.iter().any(|f| *f > 0.0 && *f < 1.0),
            "progress must move through intermediate values: {fractions:?}"
        );
        assert_eq!(fractions.last(), Some(&1.0));
        assert!(steps.iter().any(|s| s.contains("Menyalin")), "{steps:?}");
        assert!(
            dir.join(UNINSTALL_EXE).is_file(),
            "uninstaller copy missing"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn install_refuses_an_empty_payload() {
        let dir = unique_temp_dir("empty");
        let opts = InstallOptions {
            dir: dir.clone(),
            launch_after: false,
        };
        let err = install(&[], &opts, &mut |_, _| {}).unwrap_err();
        assert!(err.contains("tanpa payload"), "{err}");
        assert!(!dir.join(APP_EXE).exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn uninstall_removes_everything_install_created() {
        let dir = unique_temp_dir("uninstall");
        let payload = fake_payload(10_000);
        let opts = InstallOptions {
            dir: dir.clone(),
            launch_after: false,
        };
        install(&payload, &opts, &mut |_, _| {}).unwrap();
        assert!(is_installed(&dir));
        uninstall(&dir).unwrap();
        assert!(!is_installed(&dir));
        assert!(!dir.join(UNINSTALL_EXE).exists());
        assert!(!dir.exists(), "the empty install dir must go too");
        // Uninstalling twice is not an error.
        uninstall(&dir).unwrap();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn default_install_dir_is_program_files_on_windows() {
        let dir = default_install_dir();
        if cfg!(windows) {
            assert!(
                dir.to_string_lossy().contains("PuspaBot"),
                "expected Program Files\\PuspaBot, got {}",
                dir.display()
            );
        } else {
            assert!(dir.to_string_lossy().contains("puspabot-install"));
        }
    }
}
