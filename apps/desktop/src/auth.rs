//! Desktop login: loopback OAuth callback, one-time code exchange,
//! and session token persistence.
//!
//! Flow: the app listens on a free loopback port, opens the system
//! browser at `{base_url}/auth/google?desktop_port={port}`, the
//! backend's Google callback redirects the browser to
//! `http://127.0.0.1:{port}/puspabot-callback?code=...`, the app
//! exchanges that one-time code at `POST /api/desktop/session` for
//! a session token, and the token is saved to disk (0600 on Unix).
//! The session token itself never travels in a URL.

use std::io::{self, Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::api::{ApiClient, ApiError};

/// How long [`login_via_browser`] waits for the loopback callback.
const CALLBACK_TIMEOUT: Duration = Duration::from_secs(300);

/// The loopback callback path the backend redirects the browser to.
const CALLBACK_PATH: &str = "/puspabot-callback";

/// A logged-in desktop session: the token the API client sends as
/// the `puspa_sesi` cookie, plus the user it belongs to.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DesktopSession {
    pub session_token: String,
    pub user_id: String,
}

/// `POST /api/desktop/session` response body.
#[derive(Debug, Clone, Deserialize)]
struct SessionResponse {
    #[serde(default)]
    ok: bool,
    #[serde(default)]
    session_token: String,
    #[serde(default)]
    user_id: String,
}

/// Result of restoring a saved session (see [`restore_session`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LoginStatus {
    LoggedIn { user_id: String },
    NotLoggedIn,
    Failed(String),
}

#[derive(Debug, Error)]
pub enum AuthError {
    #[error("could not do local login work: {0}")]
    Io(#[from] io::Error),
    #[error("login timed out after 5 minutes: no callback arrived, the browser login may not have finished")]
    Timeout,
    #[error("loopback callback failed: {0}")]
    Callback(String),
    #[error("session exchange failed: {0}")]
    Exchange(#[from] ApiError),
}

// ---------------------------------------------------------------------
// Loopback callback listener
// ---------------------------------------------------------------------

/// Percent-decode a query value (`%XX` escapes and `+` as space).
/// One-time codes are URL-safe, so this is mostly a pass-through.
fn percent_decode(raw: &str) -> String {
    let mut out = Vec::with_capacity(raw.len());
    let bytes = raw.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or("");
                if let Ok(byte) = u8::from_str_radix(hex, 16) {
                    out.push(byte);
                    i += 3;
                    continue;
                }
                out.push(b'%');
                i += 1;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            byte => {
                out.push(byte);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The `code` query value of a `GET /puspabot-callback?code=...`
/// request line, or `None` when the request is not that callback.
fn callback_code(request_line: &str) -> Option<String> {
    let target = request_line.split_whitespace().nth(1)?;
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    if path != CALLBACK_PATH {
        return None;
    }
    for pair in query.split('&') {
        if let Some((key, value)) = pair.split_once('=') {
            if key == "code" && !value.is_empty() {
                return Some(percent_decode(value));
            }
        }
    }
    None
}

fn write_response(stream: &mut std::net::TcpStream, status: &str, body: &str) {
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.flush();
}

const SUCCESS_PAGE: &str = "<!doctype html><html><body><h1>Login berhasil</h1>\
    <p>Login PuspaBot berhasil. Silakan kembali ke aplikasi PuspaBot.</p>\
    </body></html>";

const BAD_REQUEST_PAGE: &str = "<!doctype html><html><body><h1>Permintaan tidak dikenal</h1>\
    <p>Tautan ini bukan callback login PuspaBot.</p></body></html>";

/// Handle one accepted connection. Returns the one-time code when
/// the request was the login callback, or `None` when it was some
/// other request (the listener keeps waiting in that case).
fn handle_connection(mut stream: std::net::TcpStream) -> Option<String> {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let mut buf = [0u8; 8192];
    let mut request = Vec::new();
    loop {
        match stream.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                request.extend_from_slice(&buf[..n]);
                if request.windows(4).any(|w| w == b"\r\n\r\n") || request.len() >= 8192 {
                    break;
                }
            }
            Err(_) => break,
        }
    }
    let text = String::from_utf8_lossy(&request);
    let request_line = text.lines().next().unwrap_or("");
    match callback_code(request_line) {
        Some(code) => {
            write_response(&mut stream, "200 OK", SUCCESS_PAGE);
            Some(code)
        }
        None => {
            write_response(&mut stream, "400 Bad Request", BAD_REQUEST_PAGE);
            None
        }
    }
}

/// Wait for one `GET /puspabot-callback?code=...` on an already
/// bound loopback listener. Blocking; run it on a worker thread.
/// Requests that are not the callback get a 400 page and the wait
/// continues until the timeout.
pub fn wait_for_code_on(listener: TcpListener, timeout: Duration) -> Result<String, String> {
    let _ = listener.set_nonblocking(true);
    let deadline = Instant::now() + timeout;
    loop {
        match listener.accept() {
            Ok((stream, _)) => {
                if let Some(code) = handle_connection(stream) {
                    return Ok(code);
                }
            }
            Err(ref err) if err.kind() == io::ErrorKind::WouldBlock => {
                if Instant::now() >= deadline {
                    return Err(format!(
                        "login timed out: no callback arrived within {timeout:?}"
                    ));
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(err) => return Err(format!("could not accept loopback connection: {err}")),
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "login timed out: no callback arrived within {timeout:?}"
            ));
        }
    }
}

// ---------------------------------------------------------------------
// Browser + code exchange
// ---------------------------------------------------------------------

/// The system command that opens a URL in the default browser.
fn browser_command() -> Vec<String> {
    if cfg!(windows) {
        vec![
            "cmd".to_string(),
            "/C".to_string(),
            "start".to_string(),
            String::new(),
        ]
    } else if cfg!(target_os = "macos") {
        vec!["open".to_string()]
    } else {
        vec!["xdg-open".to_string()]
    }
}

/// Open a URL in the system browser (fire and forget).
pub fn open_browser(url: &str) -> io::Result<()> {
    let command = browser_command();
    let (program, args) = command.split_first().expect("browser command");
    Command::new(program)
        .args(args)
        .arg(url)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()?;
    Ok(())
}

/// The Google login URL the browser opens for a desktop login.
pub fn login_url(base_url: &str, port: u16) -> String {
    format!(
        "{}/auth/google?desktop_port={port}",
        base_url.trim_end_matches('/')
    )
}

/// Exchange a one-time code for a session token at
/// `POST /api/desktop/session`. A 401 (unknown or expired code)
/// surfaces as [`ApiError::NotLoggedIn`].
pub async fn exchange_code(client: &ApiClient, code: &str) -> Result<DesktopSession, ApiError> {
    let response: SessionResponse = client
        .post_json("/api/desktop/session", &serde_json::json!({ "code": code }))
        .await?;
    if !response.ok || response.session_token.is_empty() {
        return Err(ApiError::NotLoggedIn);
    }
    Ok(DesktopSession {
        session_token: response.session_token,
        user_id: response.user_id,
    })
}

/// Full desktop login: listen on a free loopback port, open the
/// system browser at the Google login with that port attached, wait
/// for the callback (5 minute timeout), exchange the one-time code
/// for a session token, set it on the client, and save it to disk.
/// A live full exchange needs a minted one-time code from a real
/// Google login, so it cannot be exercised without a browser login.
pub async fn login_via_browser(
    client: &mut ApiClient,
    base_url: &str,
) -> Result<DesktopSession, AuthError> {
    let base = if base_url.is_empty() {
        client.base_url().to_string()
    } else {
        base_url.trim_end_matches('/').to_string()
    };
    let port = crate::backend::free_port()?;
    let listener = TcpListener::bind(("127.0.0.1", port))?;
    let waiter = tokio::task::spawn_blocking(move || wait_for_code_on(listener, CALLBACK_TIMEOUT));
    open_browser(&login_url(&base, port))?;
    let code = match waiter.await {
        Ok(Ok(code)) => code,
        Ok(Err(reason)) if reason.starts_with("login timed out") => {
            return Err(AuthError::Timeout);
        }
        Ok(Err(reason)) => return Err(AuthError::Callback(reason)),
        Err(err) => {
            return Err(AuthError::Callback(format!(
                "callback listener stopped: {err}"
            )))
        }
    };
    let session = exchange_code(client, &code).await?;
    client.set_session_token(Some(session.session_token.clone()));
    save_session(&session.session_token, &session.user_id)?;
    Ok(session)
}

// ---------------------------------------------------------------------
// Session persistence
// ---------------------------------------------------------------------

/// The session file inside a platform data dir: `puspabot/
/// session.json` on Unix, `Puspa\session.json` on Windows.
pub fn session_file_path_in(data_home: &Path) -> PathBuf {
    if cfg!(windows) {
        data_home.join("Puspa").join("session.json")
    } else {
        data_home.join("puspabot").join("session.json")
    }
}

/// The session file for this machine: `$XDG_DATA_HOME/puspabot/
/// session.json`, else `~/.local/share/puspabot/session.json`
/// (Linux/macOS); `%APPDATA%\Puspa\session.json` (Windows).
pub fn session_file_path() -> PathBuf {
    if cfg!(windows) {
        let base = std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        session_file_path_in(&base)
    } else if let Some(xdg) = std::env::var_os("XDG_DATA_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
    {
        session_file_path_in(&xdg)
    } else {
        let home = std::env::var_os("HOME").map(PathBuf::from);
        match home {
            Some(home) => session_file_path_in(&home.join(".local").join("share")),
            None => session_file_path_in(Path::new(".")),
        }
    }
}

/// Save a session token to a file with 0600 permissions on Unix,
/// creating the parent directory first.
pub fn save_session_to(path: &Path, token: &str, user_id: &str) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let session = DesktopSession {
        session_token: token.to_string(),
        user_id: user_id.to_string(),
    };
    let text = serde_json::to_string(&session)
        .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?;
    std::fs::write(path, text)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

/// Save a session token to the platform session file.
pub fn save_session(token: &str, user_id: &str) -> io::Result<()> {
    save_session_to(&session_file_path(), token, user_id)
}

/// Load a saved session from a file. `None` means no file, or a
/// file that cannot be read or parsed (a stale/corrupt file is
/// treated the same as no session, never an error).
pub fn load_session_from(path: &Path) -> Option<DesktopSession> {
    let text = std::fs::read_to_string(path).ok()?;
    let session: DesktopSession = serde_json::from_str(&text).ok()?;
    if session.session_token.is_empty() {
        return None;
    }
    Some(session)
}

/// Delete the saved session file. True when a file was removed.
pub fn clear_saved_session() -> bool {
    std::fs::remove_file(session_file_path()).is_ok()
}

/// Load the saved session (if any) and check it still works with
/// `GET /api/chat/messages`. A 401 means the token is dead: the
/// file is deleted and [`LoginStatus::NotLoggedIn`] is returned.
pub async fn restore_session_from(client: &mut ApiClient, path: &Path) -> LoginStatus {
    let Some(saved) = load_session_from(path) else {
        return LoginStatus::NotLoggedIn;
    };
    client.set_session_token(Some(saved.session_token.clone()));
    match client
        .get_json::<serde_json::Value>("/api/chat/messages")
        .await
    {
        Ok(_) => LoginStatus::LoggedIn {
            user_id: saved.user_id,
        },
        Err(ApiError::NotLoggedIn) => {
            let _ = std::fs::remove_file(path);
            client.set_session_token(None);
            LoginStatus::NotLoggedIn
        }
        Err(err) => LoginStatus::Failed(format!("could not validate saved session: {err}")),
    }
}

/// Load the saved session from the platform file and validate it
/// (see [`restore_session_from`]).
pub async fn restore_session(client: &mut ApiClient) -> LoginStatus {
    restore_session_from(client, &session_file_path()).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

    fn unique_temp_dir(tag: &str) -> PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!(
            "puspabot-test-auth-{tag}-{}-{n}",
            std::process::id()
        ));
        let _ = std::fs::create_dir_all(&dir);
        dir
    }

    /// A stub HTTP server answering one canned status + JSON body.
    /// Follows the `stub_server` pattern already in `api.rs` tests.
    async fn stub_server(status: u16, body: &'static str) -> String {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0u8; 4096];
                let _ = socket.read(&mut buf).await;
                let reason = if status == 200 { "OK" } else { "Unauthorized" };
                let response = format!(
                    "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = socket.write_all(response.as_bytes()).await;
            }
        });
        format!("http://127.0.0.1:{port}")
    }

    /// Send one raw HTTP GET at the loopback listener and return the
    /// full response text.
    fn raw_get(port: u16, target: &str) -> String {
        let mut stream = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
        stream
            .write_all(format!("GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").as_bytes())
            .unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut response = String::new();
        let _ = stream.read_to_string(&mut response);
        response
    }

    #[test]
    fn login_url_carries_the_loopback_port() {
        assert_eq!(
            login_url("http://127.0.0.1:8901/", 54321),
            "http://127.0.0.1:8901/auth/google?desktop_port=54321"
        );
    }

    #[test]
    fn session_file_path_in_uses_the_puspabot_dir() {
        let base = Path::new("/data");
        let path = session_file_path_in(base);
        if cfg!(windows) {
            assert_eq!(path, base.join("Puspa").join("session.json"));
        } else {
            assert_eq!(path, base.join("puspabot").join("session.json"));
        }
    }

    #[test]
    fn browser_command_opens_with_the_system_opener() {
        let command = browser_command();
        if cfg!(target_os = "macos") {
            assert_eq!(command, vec!["open".to_string()]);
        } else if cfg!(windows) {
            assert_eq!(command[0], "cmd");
        } else {
            assert_eq!(command, vec!["xdg-open".to_string()]);
        }
    }

    #[test]
    fn callback_code_parses_only_the_login_callback() {
        assert_eq!(
            callback_code("GET /puspabot-callback?code=abc-123 HTTP/1.1"),
            Some("abc-123".to_string())
        );
        assert_eq!(
            callback_code("GET /puspabot-callback?code=abc%2D123&other=x HTTP/1.1"),
            Some("abc-123".to_string())
        );
        assert_eq!(callback_code("GET /puspabot-callback HTTP/1.1"), None);
        assert_eq!(callback_code("GET /other?code=abc HTTP/1.1"), None);
        assert_eq!(callback_code("garbage"), None);
    }

    #[test]
    fn wait_for_code_returns_the_code_and_a_success_page() {
        let port = crate::backend::free_port().unwrap();
        let listener = TcpListener::bind(("127.0.0.1", port)).unwrap();
        let waiter =
            std::thread::spawn(move || wait_for_code_on(listener, Duration::from_secs(10)));
        // Give the listener a moment to start waiting.
        std::thread::sleep(Duration::from_millis(200));
        let response = raw_get(port, "/puspabot-callback?code=code-abc-123");
        assert!(response.contains("200 OK"), "{response}");
        assert!(response.contains("Login berhasil"), "{response}");
        assert!(
            response.contains("kembali ke aplikasi PuspaBot"),
            "{response}"
        );
        assert_eq!(waiter.join().unwrap().unwrap(), "code-abc-123");
    }

    #[test]
    fn wait_for_code_times_out_with_nobody_connecting() {
        let port = crate::backend::free_port().unwrap();
        let listener = TcpListener::bind(("127.0.0.1", port)).unwrap();
        let err = wait_for_code_on(listener, Duration::from_millis(300)).unwrap_err();
        assert!(err.contains("timed out"), "{err}");
    }

    #[test]
    fn wait_for_code_ignores_a_bad_request_then_accepts_a_good_one() {
        let port = crate::backend::free_port().unwrap();
        let listener = TcpListener::bind(("127.0.0.1", port)).unwrap();
        let waiter =
            std::thread::spawn(move || wait_for_code_on(listener, Duration::from_secs(10)));
        std::thread::sleep(Duration::from_millis(200));
        let bad = raw_get(port, "/puspabot-callback");
        assert!(bad.contains("400 Bad Request"), "{bad}");
        let good = raw_get(port, "/puspabot-callback?code=code-second-try");
        assert!(good.contains("200 OK"), "{good}");
        assert_eq!(waiter.join().unwrap().unwrap(), "code-second-try");
    }

    #[tokio::test]
    async fn exchange_code_returns_token_and_user() {
        let base = stub_server(
            200,
            r#"{"ok":true,"session_token":"tok-1","user_id":"user-1"}"#,
        )
        .await;
        let client = ApiClient::new(base);
        let session = exchange_code(&client, "code-1").await.unwrap();
        assert_eq!(session.session_token, "tok-1");
        assert_eq!(session.user_id, "user-1");
    }

    #[tokio::test]
    async fn exchange_code_unknown_code_maps_to_not_logged_in() {
        let base = stub_server(401, r#"{"error":"kode tidak dikenal"}"#).await;
        let client = ApiClient::new(base);
        let err = exchange_code(&client, "nope").await.unwrap_err();
        assert!(matches!(err, ApiError::NotLoggedIn));
    }

    #[tokio::test]
    async fn exchange_code_ok_false_maps_to_not_logged_in() {
        let base = stub_server(200, r#"{"ok":false,"session_token":"","user_id":""}"#).await;
        let client = ApiClient::new(base);
        let err = exchange_code(&client, "code-1").await.unwrap_err();
        assert!(matches!(err, ApiError::NotLoggedIn));
    }

    #[test]
    fn save_and_load_session_round_trips() {
        let dir = unique_temp_dir("save-load");
        let path = dir.join("session.json");
        save_session_to(&path, "tok-secret", "user-9").unwrap();
        let loaded = load_session_from(&path).unwrap();
        assert_eq!(loaded.session_token, "tok-secret");
        assert_eq!(loaded.user_id, "user-9");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn saved_session_file_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = unique_temp_dir("perms");
        let path = dir.join("session.json");
        save_session_to(&path, "tok-secret", "user-9").unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600, "session file must be 0600, got {mode:o}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn load_session_returns_none_for_missing_or_corrupt_file() {
        let dir = unique_temp_dir("load-none");
        assert_eq!(load_session_from(&dir.join("nope.json")), None);
        let bad = dir.join("bad.json");
        std::fs::write(&bad, "not json").unwrap();
        assert_eq!(load_session_from(&bad), None);
        let empty = dir.join("empty.json");
        std::fs::write(&empty, r#"{"session_token":"","user_id":"u"}"#).unwrap();
        assert_eq!(load_session_from(&empty), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_session_validates_and_keeps_a_good_token() {
        let base = stub_server(200, r#"{"messages":[]}"#).await;
        let dir = unique_temp_dir("restore-ok");
        let path = dir.join("session.json");
        save_session_to(&path, "tok-good", "user-1").unwrap();
        let mut client = ApiClient::new(base);
        let status = restore_session_from(&mut client, &path).await;
        assert_eq!(
            status,
            LoginStatus::LoggedIn {
                user_id: "user-1".to_string()
            }
        );
        assert_eq!(client.session_token(), Some("tok-good"));
        assert!(path.is_file(), "good token must keep its file");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_session_discards_a_401_token() {
        let base = stub_server(401, r#"{"error":"belum masuk"}"#).await;
        let dir = unique_temp_dir("restore-401");
        let path = dir.join("session.json");
        save_session_to(&path, "tok-dead", "user-1").unwrap();
        let mut client = ApiClient::new(base);
        let status = restore_session_from(&mut client, &path).await;
        assert_eq!(status, LoginStatus::NotLoggedIn);
        assert_eq!(client.session_token(), None);
        assert!(!path.exists(), "dead token file must be deleted");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_session_with_no_file_is_not_logged_in() {
        let base = stub_server(200, r#"{"messages":[]}"#).await;
        let dir = unique_temp_dir("restore-none");
        let mut client = ApiClient::new(base);
        let status = restore_session_from(&mut client, &dir.join("nope.json")).await;
        assert_eq!(status, LoginStatus::NotLoggedIn);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
