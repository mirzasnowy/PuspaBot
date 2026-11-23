//! Profile tab ("Saya"): model settings and BYOK key, provider
//! states, installed skills, language, telemetry, and logout.
//!
//! Data comes from the local backend only: `GET /api/model-
//! settings`, `GET /api/providers`, `GET /api/hub/skills`, `GET
//! /api/language`, `GET /api/telemetry/settings`. Writes go
//! through the matching POST/DELETE endpoints. There is no
//! credits/billing API, so there is no Keuangan section here —
//! only features that genuinely work exist in this tab.

use iced::widget::{button, column, row, scrollable, text, text_input};
use iced::{Color, Element, Length, Task};
use serde::Deserialize;

use super::section_title;
use crate::api::{ApiClient, ApiError};
use crate::screens::chat::ModelSettings;

const RED: Color = Color {
    r: 0xD7 as f32 / 255.0,
    g: 0x40 as f32 / 255.0,
    b: 0x4D as f32 / 255.0,
    a: 1.0,
};
const GREEN: Color = Color {
    r: 0x4C as f32 / 255.0,
    g: 0xAF as f32 / 255.0,
    b: 0x50 as f32 / 255.0,
    a: 1.0,
};

/// One provider (`puspa/pwa/app.py` — `GET /api/providers`).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Provider {
    #[serde(default)]
    pub provider: String,
    #[serde(default)]
    pub flow: String,
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub expires_at: Option<String>,
    #[serde(default)]
    pub local_only: bool,
    #[serde(default)]
    pub login_ready: bool,
}

/// `GET /api/providers` answer.
#[derive(Debug, Clone, Deserialize)]
pub struct ProvidersResponse {
    #[serde(default)]
    providers: Vec<Provider>,
}

/// One skill (`GET /api/hub/skills`).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Skill {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub installed: bool,
}

/// `GET /api/hub/skills` answer.
#[derive(Debug, Clone, Deserialize)]
pub struct SkillsResponse {
    #[serde(default)]
    skills: Vec<Skill>,
}

/// `GET /api/language` answer.
#[derive(Debug, Clone, Deserialize)]
pub struct LanguageResponse {
    #[serde(default)]
    locale: String,
    #[serde(default)]
    available_locales: Vec<String>,
}

/// `GET /api/telemetry/settings` answer.
#[derive(Debug, Clone, Deserialize)]
// Wire format: mirrors the backend payload; not every field is shown in the v1 UI.
#[allow(dead_code)]
pub struct TelemetryResponse {
    #[serde(default)]
    enabled: bool,
    #[serde(default)]
    effective_enabled: bool,
}

#[derive(Debug, Clone)]
pub enum Message {
    ModelLoaded(Result<ModelSettings, ApiError>),
    ProvidersLoaded(Result<ProvidersResponse, ApiError>),
    SkillsLoaded(Result<SkillsResponse, ApiError>),
    LanguageLoaded(Result<LanguageResponse, ApiError>),
    TelemetryLoaded(Result<TelemetryResponse, ApiError>),
    Saved(Result<serde_json::Value, ApiError>),
    ByokProviderChanged(String),
    ByokKeyChanged(String),
    ByokSaved,
    ByokDeleted,
    LanguageChanged(String),
    TelemetryToggled,
    LogoutPressed,
    ReloadPressed,
}

pub struct State {
    pub model: Option<ModelSettings>,
    pub providers: Vec<Provider>,
    pub skills: Vec<Skill>,
    pub locale: String,
    pub available_locales: Vec<String>,
    pub telemetry_enabled: bool,
    pub byok_provider: String,
    pub byok_key: String,
    pub error: Option<String>,
    pub loaded: bool,
}

impl State {
    pub fn new() -> Self {
        Self {
            model: None,
            providers: Vec::new(),
            skills: Vec::new(),
            locale: "id".to_string(),
            available_locales: Vec::new(),
            telemetry_enabled: false,
            byok_provider: String::new(),
            byok_key: String::new(),
            error: None,
            loaded: false,
        }
    }

    /// Load model settings + providers + skills + language +
    /// telemetry (first load only).
    pub fn load(client: ApiClient) -> Task<Message> {
        let model = client.clone();
        let providers = client.clone();
        let skills = client.clone();
        let language = client.clone();
        let telemetry = client;
        Task::batch([
            Task::perform(
                async move { model.get_json::<ModelSettings>("/api/model-settings").await },
                Message::ModelLoaded,
            ),
            Task::perform(
                async move {
                    providers
                        .get_json::<ProvidersResponse>("/api/providers")
                        .await
                },
                Message::ProvidersLoaded,
            ),
            Task::perform(
                async move { skills.get_json::<SkillsResponse>("/api/hub/skills").await },
                Message::SkillsLoaded,
            ),
            Task::perform(
                async move { language.get_json::<LanguageResponse>("/api/language").await },
                Message::LanguageLoaded,
            ),
            Task::perform(
                async move {
                    telemetry
                        .get_json::<TelemetryResponse>("/api/telemetry/settings")
                        .await
                },
                Message::TelemetryLoaded,
            ),
        ])
    }

    fn reload(client: &ApiClient) -> Task<Message> {
        Self::load(client.clone())
    }

    pub fn update(&mut self, message: Message, client: &ApiClient) -> Task<Message> {
        match message {
            Message::ModelLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(model) => {
                        if self.byok_provider.is_empty() {
                            self.byok_provider = model.provider.clone().unwrap_or_default();
                        }
                        self.model = Some(model);
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat pengaturan model: {err}")),
                }
                Task::none()
            }
            Message::ProvidersLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.providers = data.providers;
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat provider: {err}")),
                }
                Task::none()
            }
            Message::SkillsLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.skills = data.skills;
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat skill: {err}")),
                }
                Task::none()
            }
            Message::LanguageLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.locale = data.locale;
                        self.available_locales = data.available_locales;
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat bahasa: {err}")),
                }
                Task::none()
            }
            Message::TelemetryLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.telemetry_enabled = data.effective_enabled;
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat telemetri: {err}")),
                }
                Task::none()
            }
            Message::Saved(result) => match result {
                Ok(_) => {
                    self.byok_key.clear();
                    self.error = None;
                    Self::reload(client)
                }
                Err(err) => {
                    self.error = Some(format!("Gagal menyimpan: {err}"));
                    Task::none()
                }
            },
            Message::ByokProviderChanged(provider) => {
                self.byok_provider = provider;
                Task::none()
            }
            Message::ByokKeyChanged(key) => {
                self.byok_key = key;
                Task::none()
            }
            Message::ByokSaved => {
                let provider = self.byok_provider.trim().to_string();
                let key = self.byok_key.trim().to_string();
                if provider.is_empty() || key.is_empty() {
                    return Task::none();
                }
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                "/api/model-settings/byok-key",
                                &serde_json::json!({ "provider": provider, "key": key }),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::ByokDeleted => {
                let provider = self
                    .model
                    .as_ref()
                    .and_then(|model| model.provider.clone())
                    .unwrap_or_default();
                if provider.is_empty() {
                    return Task::none();
                }
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .delete::<serde_json::Value>(&format!(
                                "/api/model-settings/byok-key?provider={provider}"
                            ))
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::LanguageChanged(locale) => {
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                "/api/language",
                                &serde_json::json!({ "locale": locale }),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::TelemetryToggled => {
                let enabled = !self.telemetry_enabled;
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                "/api/telemetry/settings",
                                &serde_json::json!({ "enabled": enabled }),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::LogoutPressed => Task::none(),
            Message::ReloadPressed => Self::reload(client),
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let mut content = column![].spacing(12).padding(16).width(Length::Fill);

        content = content.push(
            row![
                section_title("Saya"),
                button("Muat ulang").on_press(Message::ReloadPressed),
            ]
            .spacing(8),
        );

        if let Some(error) = &self.error {
            content = content.push(text(error).size(13).color(RED));
        }
        if !self.loaded {
            return content.push(text("Memuat profil...").size(14)).into();
        }

        content = content.push(section_title("Pengaturan model"));
        match &self.model {
            Some(model) => {
                let provider = model.provider.clone().unwrap_or_else(|| "-".to_string());
                let model_name = model.model.clone().unwrap_or_else(|| "-".to_string());
                content = content
                    .push(text(format!("Provider: {provider} — Model: {model_name}")).size(14));
                let key_state = if model.has_byok_key {
                    "Kunci sendiri (BYOK) sudah disimpan."
                } else {
                    "Belum ada kunci sendiri. Tanpa kunci, agen tidak bisa menjawab."
                };
                let key_color = if model.has_byok_key { GREEN } else { RED };
                content = content.push(text(key_state).size(14).color(key_color));
                if model.has_byok_key {
                    content =
                        content.push(button("Hapus kunci sendiri").on_press(Message::ByokDeleted));
                }
            }
            None => {
                content = content.push(text("Pengaturan model tidak tersedia.").size(14));
            }
        }
        content = content.push(
            column![
                text_input("Provider (mis. openai, anthropic)", &self.byok_provider)
                    .on_input(Message::ByokProviderChanged),
                text_input("Kunci API sendiri", &self.byok_key)
                    .on_input(Message::ByokKeyChanged)
                    .secure(true),
                button("Simpan kunci sendiri").on_press(Message::ByokSaved),
            ]
            .spacing(8),
        );

        content = content.push(section_title("Provider"));
        if self.providers.is_empty() {
            content = content.push(text("Belum ada provider.").size(14));
        }
        for provider in &self.providers {
            content =
                content.push(text(format!("{} — {}", provider.provider, provider.state)).size(14));
        }

        content = content.push(section_title("Skill terpasang"));
        let installed: Vec<&Skill> = self.skills.iter().filter(|skill| skill.installed).collect();
        if installed.is_empty() {
            content = content.push(text("Belum ada skill terpasang.").size(14));
        }
        for skill in installed {
            content = content.push(text(format!("{} (v{})", skill.name, skill.version)).size(14));
        }

        content = content.push(section_title("Bahasa"));
        let locales = if self.available_locales.is_empty() {
            vec!["id".to_string(), "en".to_string()]
        } else {
            self.available_locales.clone()
        };
        let mut lang_row = row![text(format!("Bahasa aktif: {}", self.locale)).size(14)].spacing(8);
        for locale in locales {
            lang_row = lang_row
                .push(button(text(locale.clone())).on_press(Message::LanguageChanged(locale)));
        }
        content = content.push(lang_row);

        content = content.push(section_title("Telemetri"));
        content = content.push(
            row![
                text(if self.telemetry_enabled {
                    "Telemetri aktif."
                } else {
                    "Telemetri tidak aktif."
                })
                .size(14)
                .width(Length::Fill),
                button(if self.telemetry_enabled {
                    "Matikan"
                } else {
                    "Nyalakan"
                })
                .on_press(Message::TelemetryToggled),
            ]
            .spacing(8),
        );

        content = content.push(section_title("Akun"));
        content = content.push(button("Keluar").on_press(Message::LogoutPressed));

        scrollable(content).into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn providers_response_parses_live_shape() {
        let data: ProvidersResponse = serde_json::from_str(
            r#"{"providers":[{"provider":"claude","flow":"setup_token","state":"not_logged_in","expires_at":null,"local_only":true,"login_ready":false}]}"#,
        )
        .unwrap();
        assert_eq!(data.providers.len(), 1);
        assert_eq!(data.providers[0].state, "not_logged_in");
    }

    #[test]
    fn skills_response_parses_live_shape() {
        let data: SkillsResponse = serde_json::from_str(
            r#"{"skills":[{"id":"backup","name":"Backup","description":"Keeps data safe.","version":"1.0.0","origin":"builtin","status":"builtin","source":"puspa-builtin","license":"MIT","price_credits":0,"installed":true}]}"#,
        )
        .unwrap();
        assert_eq!(data.skills.len(), 1);
        assert!(data.skills[0].installed);
    }

    #[test]
    fn language_response_parses_live_shape() {
        let data: LanguageResponse =
            serde_json::from_str(r#"{"locale":"id","available_locales":["id","en"]}"#).unwrap();
        assert_eq!(data.locale, "id");
        assert_eq!(data.available_locales.len(), 2);
    }

    #[test]
    fn telemetry_response_parses_live_shape() {
        let data: TelemetryResponse = serde_json::from_str(
            r#"{"enabled":false,"env_override":null,"effective_enabled":false}"#,
        )
        .unwrap();
        assert!(!data.effective_enabled);
    }
}
