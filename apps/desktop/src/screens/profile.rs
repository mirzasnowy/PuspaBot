//! Profile tab ("Saya"): model settings and BYOK key, provider
//! states, installed skills, language, telemetry, and logout.
//!
//! Data comes from the local backend only: `GET /api/model-
//! settings`, `GET /api/providers`, `GET /api/hub/skills`, `GET
//! /api/language`, `GET /api/telemetry/settings`. Writes go
//! through the matching POST/DELETE endpoints. There is no
//! credits/billing API, so there is no Keuangan section here —
//! only features that genuinely work exist in this tab.

use iced::alignment::{Horizontal, Vertical};
use iced::widget::{button, column, container, image, row, scrollable, text, text_input};
use iced::{Element, Length, Task};
use serde::Deserialize;

use crate::api::{ApiClient, ApiError};
use crate::screens::chat::ModelSettings;
use crate::theme;

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
        let mut content = column![].spacing(16).padding(24).width(Length::Fill);

        content = content.push(
            row![
                column![
                    text("Saya")
                        .font(theme::font_bold())
                        .size(22)
                        .color(theme::CHARCOAL),
                    text("Profil, model, provider, dan pengaturan aplikasi")
                        .font(theme::font_regular())
                        .size(13)
                        .color(theme::GRAY),
                ]
                .spacing(2)
                .width(Length::Fill),
                button(text("Muat ulang").font(theme::font_semibold()).size(14))
                    .on_press(Message::ReloadPressed)
                    .style(theme::outline_button)
                    .padding([8, 16]),
            ]
            .spacing(8)
            .align_y(Vertical::Center),
        );

        if let Some(error) = &self.error {
            content = content.push(
                container(
                    text(error)
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::CHARCOAL),
                )
                .padding(16)
                .style(|_| theme::tint_style(theme::PEACH_TINT))
                .width(Length::Fill),
            );
        }
        if !self.loaded {
            content = content.push(loading_card("Memuat profil…"));
            return container(scrollable(content).width(Length::Fill).height(Length::Fill))
                .style(|_| theme::cream_style())
                .width(Length::Fill)
                .height(Length::Fill)
                .into();
        }

        // Profile card on top.
        let model_line = match &self.model {
            Some(model) => format!(
                "{} · {}",
                model.provider.clone().unwrap_or_else(|| "-".to_string()),
                model.model.clone().unwrap_or_else(|| "-".to_string()),
            ),
            None => "Pengaturan model tidak tersedia.".to_string(),
        };
        content = content.push(card(
            row![
                image(image::Handle::from_bytes(theme::MASCOT))
                    .width(Length::Fixed(56.0))
                    .height(Length::Fixed(56.0)),
                column![
                    text("Saya")
                        .font(theme::font_bold())
                        .size(18)
                        .color(theme::CHARCOAL),
                    text("Akun PuspaBot di komputer ini — masuk dengan Google.")
                        .font(theme::font_regular())
                        .size(13)
                        .color(theme::GRAY),
                    text(format!("Model: {model_line}"))
                        .font(theme::font_regular())
                        .size(13)
                        .color(theme::GRAY),
                ]
                .spacing(4)
                .width(Length::Fill),
            ]
            .spacing(16)
            .align_y(Vertical::Center),
        ));

        // Pengaturan model + BYOK.
        let mut model_card = column![section_header("settings", "Pengaturan model")].spacing(12);
        match &self.model {
            Some(model) => {
                let provider = model.provider.clone().unwrap_or_else(|| "-".to_string());
                let model_name = model.model.clone().unwrap_or_else(|| "-".to_string());
                model_card = model_card.push(setting_row("Provider", &provider));
                model_card = model_card.push(setting_row("Model", &model_name));
                let (key_label, key_bg) = if model.has_byok_key {
                    ("Tersimpan", theme::GREEN_TINT)
                } else {
                    ("Belum ada", theme::RED_TINT)
                };
                model_card = model_card.push(
                    row![
                        theme::icon("settings", theme::RED, 20.0),
                        text("Kunci sendiri (BYOK)")
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::CHARCOAL)
                            .width(Length::Fill),
                        chip(key_label, key_bg),
                    ]
                    .spacing(8)
                    .align_y(Vertical::Center),
                );
                if !model.has_byok_key {
                    model_card = model_card.push(
                        text("Tanpa kunci, agen tidak bisa menjawab.")
                            .font(theme::font_regular())
                            .size(13)
                            .color(theme::GRAY),
                    );
                }
                if model.has_byok_key {
                    model_card = model_card.push(
                        button(
                            text("Hapus kunci sendiri")
                                .font(theme::font_semibold())
                                .size(13),
                        )
                        .on_press(Message::ByokDeleted)
                        .style(theme::outline_button)
                        .padding([6, 14]),
                    );
                }
            }
            None => {
                model_card = model_card.push(
                    text("Pengaturan model tidak tersedia.")
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::GRAY),
                );
            }
        }
        model_card = model_card.push(
            column![
                text_input("Provider (mis. openai, anthropic)", &self.byok_provider)
                    .on_input(Message::ByokProviderChanged)
                    .font(theme::font_regular())
                    .size(14)
                    .style(theme::input_style)
                    .padding(12)
                    .width(Length::Fill),
                text_input("Kunci API sendiri", &self.byok_key)
                    .on_input(Message::ByokKeyChanged)
                    .secure(true)
                    .font(theme::font_regular())
                    .size(14)
                    .style(theme::input_style)
                    .padding(12)
                    .width(Length::Fill),
                button(
                    text("Simpan kunci sendiri")
                        .font(theme::font_semibold())
                        .size(14)
                )
                .on_press(Message::ByokSaved)
                .style(theme::primary_button)
                .padding([10, 20]),
            ]
            .spacing(8),
        );
        content = content.push(card(model_card));

        // Providers (full live list).
        let mut providers_card = column![section_header(
            "box",
            format!("Provider ({})", self.providers.len()),
        )]
        .spacing(12);
        if self.providers.is_empty() {
            providers_card = providers_card.push(empty_state(
                "Belum ada provider.",
                "Provider akan muncul di sini setelah terhubung lewat Puspa.",
            ));
        }
        for provider in &self.providers {
            let state_bg = if provider.state == "logged_in" || provider.state == "ready" {
                theme::GREEN_TINT
            } else {
                theme::PEACH_TINT
            };
            providers_card = providers_card.push(
                row![
                    theme::icon("box", theme::RED, 20.0),
                    text(&provider.provider)
                        .font(theme::font_semibold())
                        .size(14)
                        .color(theme::CHARCOAL)
                        .width(Length::Fill),
                    chip(&provider.state, state_bg),
                ]
                .spacing(8)
                .align_y(Vertical::Center),
            );
        }
        content = content.push(card(providers_card));

        // Skills.
        let installed: Vec<&Skill> = self.skills.iter().filter(|skill| skill.installed).collect();
        let mut skills_card = column![section_header("gift", "Skill terpasang")].spacing(12);
        if installed.is_empty() {
            skills_card = skills_card.push(empty_state(
                "Belum ada skill terpasang.",
                "Skill dari hub akan muncul di sini setelah dipasang.",
            ));
        }
        for skill in installed {
            skills_card = skills_card.push(
                row![
                    theme::icon("gift", theme::RED, 20.0),
                    text(&skill.name)
                        .font(theme::font_semibold())
                        .size(14)
                        .color(theme::CHARCOAL)
                        .width(Length::Fill),
                    chip(format!("v{}", skill.version), theme::GREEN_TINT),
                ]
                .spacing(8)
                .align_y(Vertical::Center),
            );
        }
        content = content.push(card(skills_card));

        // Bahasa.
        let locales = if self.available_locales.is_empty() {
            vec!["id".to_string(), "en".to_string()]
        } else {
            self.available_locales.clone()
        };
        let mut lang_row = row![].spacing(8);
        for locale in locales {
            let active = locale == self.locale;
            lang_row = lang_row.push(
                button(text(locale.clone()).font(theme::font_semibold()).size(14))
                    .on_press(Message::LanguageChanged(locale))
                    .style(theme::tab_button(active))
                    .padding([8, 16]),
            );
        }
        content = content.push(card(
            column![
                section_header("chat", "Bahasa"),
                row![
                    text("Bahasa aktif")
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::CHARCOAL)
                        .width(Length::Fill),
                    lang_row,
                ]
                .spacing(8)
                .align_y(Vertical::Center),
            ]
            .spacing(12),
        ));

        // Telemetri.
        let (telemetry_label, telemetry_bg) = if self.telemetry_enabled {
            ("Aktif", theme::GREEN_TINT)
        } else {
            ("Tidak aktif", theme::PEACH_TINT)
        };
        content = content.push(card(
            column![
                section_header("info", "Telemetri"),
                row![
                    theme::icon("info", theme::RED, 20.0),
                    text("Telemetri")
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::CHARCOAL)
                        .width(Length::Fill),
                    chip(telemetry_label, telemetry_bg),
                    button(
                        text(if self.telemetry_enabled {
                            "Matikan"
                        } else {
                            "Nyalakan"
                        })
                        .font(theme::font_semibold())
                        .size(13),
                    )
                    .on_press(Message::TelemetryToggled)
                    .style(theme::outline_button)
                    .padding([6, 14]),
                ]
                .spacing(8)
                .align_y(Vertical::Center),
            ]
            .spacing(12),
        ));

        // Akun.
        content = content.push(card(
            column![
                section_header("profile", "Akun"),
                row![
                    theme::icon("profile", theme::RED, 20.0),
                    text("Keluar dari PuspaBot di komputer ini")
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::CHARCOAL)
                        .width(Length::Fill),
                    button(text("Keluar").font(theme::font_semibold()).size(13))
                        .on_press(Message::LogoutPressed)
                        .style(theme::outline_button)
                        .padding([6, 14]),
                ]
                .spacing(8)
                .align_y(Vertical::Center),
            ]
            .spacing(12),
        ));

        container(scrollable(content).width(Length::Fill).height(Length::Fill))
            .style(|_| theme::cream_style())
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }
}

/// A white card with the standard Puspa radius, border, and shadow.
fn card<'a>(content: impl Into<Element<'a, Message>>) -> Element<'a, Message> {
    container(content)
        .padding(16)
        .style(|_| theme::card_style())
        .width(Length::Fill)
        .into()
}

/// Section heading: one outline icon + semibold title, no subtitle.
fn section_header<'a>(icon: &'a str, title: impl Into<String>) -> Element<'a, Message> {
    row![
        theme::icon(icon, theme::RED, 20.0),
        text(title.into())
            .font(theme::font_semibold())
            .size(16)
            .color(theme::CHARCOAL),
    ]
    .spacing(8)
    .align_y(Vertical::Center)
    .into()
}

/// Title-only settings row: icon + label on the left, value on the right.
fn setting_row<'a>(label: &'a str, value: impl Into<String>) -> Element<'a, Message> {
    row![
        theme::icon("settings", theme::RED, 20.0),
        text(label)
            .font(theme::font_regular())
            .size(14)
            .color(theme::CHARCOAL)
            .width(Length::Fill),
        text(value.into())
            .font(theme::font_semibold())
            .size(14)
            .color(theme::CHARCOAL),
    ]
    .spacing(8)
    .align_y(Vertical::Center)
    .into()
}

/// Small tinted status chip.
fn chip<'a>(label: impl Into<String>, bg: iced::Color) -> Element<'a, Message> {
    container(
        text(label.into())
            .font(theme::font_medium())
            .size(12)
            .color(theme::CHARCOAL),
    )
    .padding([4, 10])
    .style(move |_| theme::tint_style(bg))
    .into()
}

/// Loading placeholder: mascot + a line telling the user to wait.
fn loading_card<'a>(label: &'a str) -> Element<'a, Message> {
    container(
        column![
            image(image::Handle::from_bytes(theme::MASCOT))
                .width(Length::Fixed(48.0))
                .height(Length::Fixed(48.0)),
            text(label)
                .font(theme::font_regular())
                .size(14)
                .color(theme::GRAY),
        ]
        .spacing(12)
        .align_x(Horizontal::Center),
    )
    .padding(24)
    .style(|_| theme::tint_style(theme::CREAM))
    .width(Length::Fill)
    .into()
}

/// Empty state: mascot + what is missing + the next step.
fn empty_state<'a>(title: &'a str, next: &'a str) -> Element<'a, Message> {
    container(
        column![
            image(image::Handle::from_bytes(theme::MASCOT))
                .width(Length::Fixed(56.0))
                .height(Length::Fixed(56.0)),
            text(title)
                .font(theme::font_semibold())
                .size(16)
                .color(theme::CHARCOAL),
            text(next)
                .font(theme::font_regular())
                .size(14)
                .color(theme::GRAY),
        ]
        .spacing(12)
        .align_x(Horizontal::Center),
    )
    .padding(24)
    .style(|_| theme::tint_style(theme::CREAM))
    .width(Length::Fill)
    .into()
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
