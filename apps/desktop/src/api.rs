//! HTTP client for the local Puspa backend (localhost API).
//!
//! The UI never touches the database directly; every screen reads
//! and writes through these endpoints. Which database the backend
//! uses (SQLite port or Postgres first-run) is decided by the
//! backend, not by this module.

use serde::Serialize;
use thiserror::Error;

/// Errors from the local backend API. The type is `Clone` on
/// purpose: iced widgets need `Message: Clone`, and screen
/// messages carry `Result<_, ApiError>`. The request error keeps
/// only the message text (a `reqwest::Error` itself is not
/// `Clone`), which is all the UI ever shows.
#[derive(Debug, Clone, Error)]
pub enum ApiError {
    #[error("HTTP request failed: {0}")]
    Request(String),
    #[error("backend answered with HTTP {0}")]
    Status(reqwest::StatusCode),
    #[error("not logged in (backend answered 401)")]
    NotLoggedIn,
}

impl From<reqwest::Error> for ApiError {
    fn from(err: reqwest::Error) -> Self {
        ApiError::Request(err.to_string())
    }
}

impl ApiError {
    fn from_status(status: reqwest::StatusCode) -> Self {
        if status == reqwest::StatusCode::UNAUTHORIZED {
            ApiError::NotLoggedIn
        } else {
            ApiError::Status(status)
        }
    }
}

#[derive(Debug, Clone)]
pub struct ApiClient {
    base_url: String,
    client: reqwest::Client,
    /// `puspa_sesi` session token from the desktop login exchange
    /// (`POST /api/desktop/session`). Sent as a `Cookie` header,
    /// the same way the PWA's `credentials: "same-origin"` fetch
    /// sends it. `None` means not logged in.
    session_token: Option<String>,
}

impl ApiClient {
    pub fn new(base_url: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            client: reqwest::Client::new(),
            session_token: None,
        }
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn set_session_token(&mut self, token: Option<String>) {
        self.session_token = token;
    }

    pub fn session_token(&self) -> Option<&str> {
        self.session_token.as_deref()
    }

    fn authed(&self, req: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        match &self.session_token {
            Some(token) => req.header("Cookie", format!("puspa_sesi={token}")),
            None => req,
        }
    }

    async fn decode<T: serde::de::DeserializeOwned>(
        &self,
        resp: reqwest::Response,
    ) -> Result<T, ApiError> {
        if !resp.status().is_success() {
            return Err(ApiError::from_status(resp.status()));
        }
        Ok(resp.json::<T>().await?)
    }

    pub async fn get_json<T: serde::de::DeserializeOwned>(
        &self,
        path: &str,
    ) -> Result<T, ApiError> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self.authed(self.client.get(url)).send().await?;
        self.decode(resp).await
    }

    /// POST a JSON body; 401 maps to [`ApiError::NotLoggedIn`].
    pub async fn post_json<T: serde::de::DeserializeOwned, B: Serialize>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T, ApiError> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self.authed(self.client.post(url)).json(body).send().await?;
        self.decode(resp).await
    }

    /// POST a form body (e.g. `/api/chat` takes `text` as a form
    /// field); 401 maps to [`ApiError::NotLoggedIn`].
    pub async fn post_form<T: serde::de::DeserializeOwned, B: Serialize>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T, ApiError> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self.authed(self.client.post(url)).form(body).send().await?;
        self.decode(resp).await
    }

    /// PATCH a JSON body (e.g. correcting a memory fact);
    /// 401 maps to [`ApiError::NotLoggedIn`].
    pub async fn patch_json<T: serde::de::DeserializeOwned, B: Serialize>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T, ApiError> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self
            .authed(self.client.patch(url))
            .json(body)
            .send()
            .await?;
        self.decode(resp).await
    }

    /// DELETE a resource (e.g. a memory fact or a graph node);
    /// 401 maps to [`ApiError::NotLoggedIn`].
    pub async fn delete<T: serde::de::DeserializeOwned>(&self, path: &str) -> Result<T, ApiError> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self.authed(self.client.delete(url)).send().await?;
        self.decode(resp).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// A stub HTTP server that always answers one canned JSON body.
    async fn stub_server(body: &'static str) -> String {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0u8; 4096];
                let _ = socket.read(&mut buf).await;
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = socket.write_all(response.as_bytes()).await;
            }
        });
        format!("http://127.0.0.1:{port}")
    }

    #[tokio::test]
    async fn get_json_parses_ok_payload() {
        let base = stub_server(r#"{"status":"ok","serve_mode":"local"}"#).await;
        let client = ApiClient::new(base);
        let body: serde_json::Value = client.get_json("/health").await.unwrap();
        assert_eq!(body["status"], "ok");
        assert_eq!(body["serve_mode"], "local");
    }

    #[tokio::test]
    async fn get_json_rejects_non_200() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0u8; 4096];
                let _ = socket.read(&mut buf).await;
                let _ = socket
                    .write_all(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n")
                    .await;
            }
        });
        let client = ApiClient::new(format!("http://127.0.0.1:{port}"));
        let err = client
            .get_json::<serde_json::Value>("/health")
            .await
            .unwrap_err();
        assert!(matches!(err, ApiError::Status(_)));
    }

    #[tokio::test]
    async fn unauthorized_maps_to_not_logged_in() {
        let base = stub_server(r#"{"error":"belum masuk"}"#).await;
        // The stub always answers 200, so drive the 401 path with a
        // raw listener instead.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0u8; 4096];
                let _ = socket.read(&mut buf).await;
                let _ = socket
                    .write_all(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
                    .await;
            }
        });
        let _ = base;
        let client = ApiClient::new(format!("http://127.0.0.1:{port}"));
        let err = client
            .get_json::<serde_json::Value>("/health")
            .await
            .unwrap_err();
        assert!(matches!(err, ApiError::NotLoggedIn));
    }

    #[tokio::test]
    async fn session_token_is_sent_as_puspa_sesi_cookie() {
        // The stub records the Cookie header it saw and echoes it
        // back in the JSON body, so the test reads it directly.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let mut buf = [0u8; 4096];
                let n = socket.read(&mut buf).await.unwrap_or(0);
                let request = String::from_utf8_lossy(&buf[..n]);
                let cookie = request
                    .lines()
                    .find(|line| line.to_ascii_lowercase().starts_with("cookie:"))
                    .unwrap_or("")
                    .to_string();
                let body = format!(
                    "{{\"status\":\"ok\",\"serve_mode\":\"{}\"}}",
                    cookie.replace('"', "")
                );
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = socket.write_all(response.as_bytes()).await;
            }
        });
        let mut client = ApiClient::new(format!("http://127.0.0.1:{port}"));
        client.set_session_token(Some("tok-abc-123".to_string()));
        let body: serde_json::Value = client.get_json("/health").await.unwrap();
        let echoed = body["serve_mode"].as_str().unwrap_or("");
        assert!(
            echoed.contains("puspa_sesi=tok-abc-123"),
            "cookie missing: {echoed}"
        );
    }
}
