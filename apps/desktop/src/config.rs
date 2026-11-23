//! Google OAuth credentials for the desktop app, stored in the
//! user-owned config file the backend also reads
//! (`puspa.auth.google_oauth.oauth_config_path` — the two sides
//! must agree on the path, see the tests).
//!
//! The file holds the OAuth client ID + secret Nero creates in
//! Google Cloud Console (Desktop app) and pastes into the app's
//! "Pengaturan Lainnya" screen. It is never sent anywhere, never
//! logged, and written with 0600 permissions on Unix. Env vars
//! (`PUSPA_GOOGLE_CLIENT_ID` / `PUSPA_GOOGLE_CLIENT_SECRET`) win
//! over the file on the backend side; this module only manages
//! the file.

use std::io;
use std::path::{Path, PathBuf};

/// The OAuth config file: `%APPDATA%\Puspa\google-oauth.json` on
/// Windows, `$XDG_CONFIG_HOME/puspabot/google-oauth.json` (else
/// `~/.config/puspabot/google-oauth.json`) elsewhere.
pub fn oauth_config_path() -> PathBuf {
    if let Ok(override_path) = std::env::var("PUSPA_OAUTH_CONFIG_FILE") {
        if !override_path.is_empty() {
            return PathBuf::from(override_path);
        }
    }
    if cfg!(windows) {
        let base = std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        base.join("Puspa").join("google-oauth.json")
    } else if let Some(xdg) = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
    {
        xdg.join("puspabot").join("google-oauth.json")
    } else {
        let home = std::env::var_os("HOME").map(PathBuf::from);
        match home {
            Some(home) => home
                .join(".config")
                .join("puspabot")
                .join("google-oauth.json"),
            None => PathBuf::from(".").join("google-oauth.json"),
        }
    }
}

/// The stored credentials, if the file exists and parses.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize)]
pub struct OauthConfig {
    pub client_id: String,
    pub client_secret: String,
}

impl OauthConfig {
    /// True when a Google login can start with these credentials.
    pub fn is_complete(&self) -> bool {
        !self.client_id.trim().is_empty() && !self.client_secret.trim().is_empty()
    }
}

/// Load the config file. `None` means no file, or a file that
/// cannot be read or parsed (a stale/corrupt file is treated the
/// same as no credentials, never an error).
pub fn load_oauth_config_from(path: &Path) -> Option<OauthConfig> {
    let text = std::fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let client_id = value
        .get("client_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let client_secret = value
        .get("client_secret")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    Some(OauthConfig {
        client_id,
        client_secret,
    })
}

/// Load the config file for this machine.
pub fn load_oauth_config() -> Option<OauthConfig> {
    load_oauth_config_from(&oauth_config_path())
}

/// Save credentials to a file with 0600 permissions on Unix,
/// creating the parent directory first.
pub fn save_oauth_config_to(path: &Path, config: &OauthConfig) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let text = serde_json::to_string(config)
        .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?;
    std::fs::write(path, text)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

/// Save credentials to the platform config file.
pub fn save_oauth_config(config: &OauthConfig) -> io::Result<()> {
    save_oauth_config_to(&oauth_config_path(), config)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_COUNTER: AtomicU64 = AtomicU64::new(0);

    fn unique_temp_dir(tag: &str) -> PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!(
            "puspabot-test-config-{tag}-{}-{n}",
            std::process::id()
        ));
        let _ = std::fs::create_dir_all(&dir);
        dir
    }

    #[test]
    fn save_and_load_round_trips() {
        let dir = unique_temp_dir("save-load");
        let path = dir.join("google-oauth.json");
        let config = OauthConfig {
            client_id: "client-123".to_string(),
            client_secret: "secret-abc".to_string(),
        };
        save_oauth_config_to(&path, &config).unwrap();
        let loaded = load_oauth_config_from(&path).unwrap();
        assert_eq!(loaded, config);
        assert!(loaded.is_complete());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn saved_config_file_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = unique_temp_dir("perms");
        let path = dir.join("google-oauth.json");
        let config = OauthConfig {
            client_id: "client-123".to_string(),
            client_secret: "secret-abc".to_string(),
        };
        save_oauth_config_to(&path, &config).unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600, "config file must be 0600, got {mode:o}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn load_returns_none_for_missing_or_corrupt_file() {
        let dir = unique_temp_dir("load-none");
        assert_eq!(load_oauth_config_from(&dir.join("nope.json")), None);
        let bad = dir.join("bad.json");
        std::fs::write(&bad, "not json").unwrap();
        assert_eq!(load_oauth_config_from(&bad), None);
        let empty = dir.join("empty.json");
        std::fs::write(&empty, r#"{"client_id":"","client_secret":""}"#).unwrap();
        let loaded = load_oauth_config_from(&empty).unwrap();
        assert!(!loaded.is_complete());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn incomplete_config_is_not_complete() {
        let only_id = OauthConfig {
            client_id: "client-123".to_string(),
            client_secret: String::new(),
        };
        assert!(!only_id.is_complete());
        assert!(!OauthConfig::default().is_complete());
    }
}
