//! HTTP client for the local Puspa backend (localhost API).
//!
//! The UI never touches the database directly; every screen reads
//! and writes through these endpoints. Which database the backend
//! uses (SQLite port or Postgres first-run) is decided by the
//! backend, not by this module.

use serde::Deserialize;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ApiError {
    #[error("HTTP request failed: {0}")]
    Request(#[from] reqwest::Error),
    #[error("backend answered with HTTP {0}")]
    Status(reqwest::StatusCode),
}

/// `GET /health` payload (`puspa/pwa/health.py`).
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct Health {
    pub status: String,
    #[serde(default)]
    pub serve_mode: String,
}

impl Health {
    pub fn is_ok(&self) -> bool {
        self.status == "ok"
    }
}

#[derive(Debug, Clone)]
pub struct ApiClient {
    base_url: String,
    client: reqwest::Client,
}

impl ApiClient {
    pub fn new(base_url: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            client: reqwest::Client::new(),
        }
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub async fn health(&self) -> Result<Health, ApiError> {
        let url = format!("{}/health", self.base_url);
        let resp = self.client.get(url).send().await?;
        if !resp.status().is_success() {
            return Err(ApiError::Status(resp.status()));
        }
        Ok(resp.json::<Health>().await?)
    }

    pub async fn get_json<T: serde::de::DeserializeOwned>(
        &self,
        path: &str,
    ) -> Result<T, ApiError> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self.client.get(url).send().await?;
        if !resp.status().is_success() {
            return Err(ApiError::Status(resp.status()));
        }
        Ok(resp.json::<T>().await?)
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
    async fn health_parses_ok_payload() {
        let base = stub_server(r#"{"status":"ok","serve_mode":"local"}"#).await;
        let client = ApiClient::new(base);
        let health = client.health().await.unwrap();
        assert!(health.is_ok());
        assert_eq!(health.serve_mode, "local");
    }

    #[tokio::test]
    async fn health_rejects_non_200() {
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
        let err = client.health().await.unwrap_err();
        assert!(matches!(err, ApiError::Status(_)));
    }
}
