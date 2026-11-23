//! Chat tab: the live thread with the Puspa agent.
//!
//! Data comes from the local backend only: `GET /api/chat/messages`
//! is the live thread (added for this desktop client), `POST
//! /api/chat` sends a message. The thread is polled while the tab
//! is open, so an agent reply shows up when it lands. When no
//! model key is configured (`has_byok_key` is false) the agent
//! cannot answer at all — the screen says so honestly and points
//! at Saya → Pengaturan model instead of letting the user shout
//! into a void.

use std::collections::HashMap;
use std::time::Duration;

use iced::alignment::{Horizontal, Vertical};
use iced::widget::{button, column, container, image, row, scrollable, text, text_input};
use iced::{Element, Length, Subscription, Task};
use serde::Deserialize;

use crate::api::{ApiClient, ApiError};
use crate::theme;

/// One message in the thread (`puspa/pwa/app.py`).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct ChatMessage {
    pub sender: String,
    pub text: String,
    #[serde(default)]
    pub created_at: f64,
    #[serde(default)]
    pub attachments: Vec<serde_json::Value>,
}

impl ChatMessage {
    #[allow(dead_code)]
    fn sender_label(&self) -> &'static str {
        if self.sender == "users" {
            "Kamu"
        } else {
            "Puspa"
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct MessagesResponse {
    #[serde(default)]
    messages: Vec<ChatMessage>,
}

/// `POST /api/chat` answer.
#[derive(Debug, Clone, Deserialize)]
pub struct SendResponse {
    #[serde(default)]
    ok: bool,
    #[serde(default)]
    message: Option<ChatMessage>,
}

/// `GET /api/model-settings` — only `has_byok_key` matters here.
#[derive(Debug, Clone, Deserialize)]
pub struct ModelSettings {
    #[serde(default)]
    pub has_byok_key: bool,
    #[serde(default)]
    pub provider: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
}

#[derive(Debug, Clone)]
pub enum Message {
    DraftChanged(String),
    SendPressed,
    Sent(Result<SendResponse, ApiError>),
    Loaded(Result<MessagesResponse, ApiError>),
    KeyLoaded(Result<ModelSettings, ApiError>),
    Tick,
    GoToSettings,
    ReloadPressed,
}

pub struct State {
    pub messages: Vec<ChatMessage>,
    pub draft: String,
    pub sending: bool,
    pub error: Option<String>,
    pub has_key: Option<bool>,
    pub loaded: bool,
    loading: bool,
}

impl State {
    pub fn new() -> Self {
        Self {
            messages: Vec::new(),
            draft: String::new(),
            sending: false,
            error: None,
            has_key: None,
            loaded: false,
            loading: false,
        }
    }

    /// Load the thread + the model-key state (first load only).
    pub fn load(client: ApiClient) -> Task<Message> {
        let messages = client.clone();
        let key = client;
        Task::batch([
            Task::perform(
                async move {
                    messages
                        .get_json::<MessagesResponse>("/api/chat/messages")
                        .await
                },
                Message::Loaded,
            ),
            Task::perform(
                async move { key.get_json::<ModelSettings>("/api/model-settings").await },
                Message::KeyLoaded,
            ),
        ])
    }

    fn reload_messages(client: ApiClient) -> Task<Message> {
        Task::perform(
            async move {
                client
                    .get_json::<MessagesResponse>("/api/chat/messages")
                    .await
            },
            Message::Loaded,
        )
    }

    pub fn update(&mut self, message: Message, client: &ApiClient) -> Task<Message> {
        match message {
            Message::DraftChanged(draft) => {
                self.draft = draft;
                Task::none()
            }
            Message::SendPressed => {
                let text = self.draft.trim().to_string();
                if text.is_empty() || self.sending {
                    return Task::none();
                }
                self.sending = true;
                self.error = None;
                self.draft.clear();
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_form::<SendResponse, HashMap<String, String>>(
                                "/api/chat",
                                &HashMap::from([("text".to_string(), text)]),
                            )
                            .await
                    },
                    Message::Sent,
                )
            }
            Message::Sent(result) => {
                self.sending = false;
                match result {
                    Ok(reply) => {
                        if let Some(message) = reply.message {
                            if !self.messages.iter().any(|m| {
                                m.text == message.text
                                    && (m.created_at - message.created_at).abs() < 0.001
                            }) {
                                self.messages.push(message);
                            }
                        }
                        if !reply.ok {
                            self.error =
                                Some("Pesan terkirim tapi backend menjawab tidak ok.".into());
                        }
                        Self::reload_messages(client.clone())
                    }
                    Err(err) => {
                        self.error = Some(match err {
                            ApiError::NotLoggedIn => {
                                "Belum masuk — login dulu dengan Google di layar masuk.".into()
                            }
                            other => format!("Gagal mengirim pesan: {other}"),
                        });
                        Task::none()
                    }
                }
            }
            Message::Loaded(result) => {
                self.loading = false;
                match result {
                    Ok(response) => {
                        self.messages = response.messages;
                        self.loaded = true;
                        self.error = None;
                    }
                    Err(err) => {
                        self.error = Some(match err {
                            ApiError::NotLoggedIn => {
                                "Belum masuk — login dulu dengan Google di layar masuk.".into()
                            }
                            other => format!("Gagal memuat chat: {other}"),
                        });
                    }
                }
                Task::none()
            }
            Message::KeyLoaded(result) => {
                match result {
                    Ok(settings) => self.has_key = Some(settings.has_byok_key),
                    Err(_) => self.has_key = None,
                }
                Task::none()
            }
            Message::Tick => {
                if self.loaded && !self.sending && !self.loading {
                    self.loading = true;
                    Self::reload_messages(client.clone())
                } else {
                    Task::none()
                }
            }
            Message::GoToSettings => Task::none(),
            Message::ReloadPressed => {
                self.loading = true;
                Self::reload_messages(client.clone())
            }
        }
    }

    /// Poll the thread every 2 seconds while the tab is open.
    pub fn subscription(&self) -> Subscription<Message> {
        if self.loaded {
            iced::time::every(Duration::from_secs(2)).map(|_| Message::Tick)
        } else {
            Subscription::none()
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let header = row![
            mascot(40.0),
            column![
                text("Chat")
                    .font(theme::font_bold())
                    .size(22)
                    .color(theme::CHARCOAL),
                text("PuspaBot — asisten pribadimu")
                    .font(theme::font_regular())
                    .size(13)
                    .color(theme::GRAY),
            ]
            .spacing(2),
        ]
        .spacing(12)
        .align_y(Vertical::Center);

        let mut content = column![header].spacing(16).padding(24).width(Length::Fill);

        if self.has_key == Some(false) {
            content = content.push(
                container(
                    column![
                        row![
                            theme::icon("alert", theme::RED, 20.0),
                            text("Otak belum bisa menjawab")
                                .font(theme::font_semibold())
                                .size(14)
                                .color(theme::CHARCOAL),
                        ]
                        .spacing(8)
                        .align_y(Vertical::Center),
                        text("Belum ada kunci model (BYOK). Pesanmu tetap tersimpan, tapi Puspa tidak bisa membalas sampai ada kunci. Tambahkan di Saya → Pengaturan model.")
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::CHARCOAL),
                        button(
                            text("Buka pengaturan model")
                                .font(theme::font_semibold())
                                .size(14),
                        )
                        .on_press(Message::GoToSettings)
                        .style(theme::primary_button)
                        .padding([8, 16]),
                    ]
                    .spacing(8),
                )
                .padding(16)
                .style(|_| theme::tint_style(theme::RED_TINT))
                .width(Length::Fill),
            );
        }

        if let Some(error) = &self.error {
            content = content.push(
                container(
                    column![
                        text(error)
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::CHARCOAL),
                        button(text("Coba lagi").font(theme::font_semibold()).size(14))
                            .on_press(Message::ReloadPressed)
                            .style(theme::outline_button)
                            .padding([8, 16]),
                    ]
                    .spacing(8),
                )
                .padding(16)
                .style(|_| theme::tint_style(theme::PEACH_TINT))
                .width(Length::Fill),
            );
        }

        if !self.loaded && self.messages.is_empty() {
            content = content.push(
                container(
                    column![
                        mascot(56.0),
                        text("Memuat chat…")
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::GRAY),
                    ]
                    .spacing(12)
                    .align_x(Horizontal::Center),
                )
                .padding(24)
                .style(|_| theme::tint_style(theme::CREAM))
                .width(Length::Fill),
            );
        } else if self.messages.is_empty() {
            content = content.push(empty_state(
                "Belum ada pesan.",
                "Sapa Puspa dulu — tulis pesan di bawah dan tekan Kirim.",
            ));
        } else {
            let mut thread = column![].spacing(12).width(Length::Fill);
            let mut last_day: Option<String> = None;
            for m in &self.messages {
                let day = date_label(m.created_at);
                if last_day.as_deref() != Some(day.as_str()) {
                    thread = thread.push(date_pill(day.clone()));
                    last_day = Some(day);
                }
                if m.sender == "users" {
                    thread = thread.push(user_bubble(m));
                } else {
                    thread = thread.push(agent_bubble(m));
                }
            }
            content = content.push(scrollable(thread).width(Length::Fill).height(Length::Fill));
        }

        let mut send_button = button(
            text(if self.sending { "Mengirim…" } else { "Kirim" })
                .font(theme::font_semibold())
                .size(14),
        )
        .style(theme::primary_button)
        .padding([10, 20]);
        if !self.sending && !self.draft.trim().is_empty() {
            send_button = send_button.on_press(Message::SendPressed);
        }
        let composer = container(
            row![
                text_input("Tulis pesan…", &self.draft)
                    .on_input(Message::DraftChanged)
                    .on_submit(Message::SendPressed)
                    .font(theme::font_regular())
                    .size(14)
                    .style(theme::input_style)
                    .padding(12)
                    .width(Length::Fill),
                send_button,
            ]
            .spacing(8)
            .align_y(Vertical::Center),
        )
        .padding(12)
        .style(|_| theme::card_style())
        .width(Length::Fill);
        content = content.push(composer);

        container(content)
            .style(|_| theme::cream_style())
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }
}

/// The Puspa flower mascot at a fixed size.
fn mascot(size: f32) -> Element<'static, Message> {
    image(image::Handle::from_bytes(theme::MASCOT))
        .width(Length::Fixed(size))
        .height(Length::Fixed(size))
        .into()
}

/// Empty state: mascot + what is missing + the next step.
fn empty_state<'a>(title: &'a str, next: &'a str) -> Element<'a, Message> {
    container(
        column![
            mascot(64.0),
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

/// Centered date separator pill between message days.
fn date_pill(label: String) -> Element<'static, Message> {
    container(
        container(
            text(label)
                .font(theme::font_medium())
                .size(12)
                .color(theme::GRAY),
        )
        .padding([4, 12])
        .style(|_| theme::tint_style(theme::WHITE)),
    )
    .width(Length::Fill)
    .align_x(Horizontal::Center)
    .into()
}

/// Agent message: mascot avatar + "PuspaBot" name above a white card bubble.
fn agent_bubble(m: &ChatMessage) -> Element<'_, Message> {
    row![
        mascot(28.0),
        column![
            text("PuspaBot")
                .font(theme::font_semibold())
                .size(12)
                .color(theme::GRAY),
            container(
                text(&m.text)
                    .font(theme::font_regular())
                    .size(14)
                    .color(theme::CHARCOAL)
                    .width(Length::Fill),
            )
            .padding(12)
            .style(|_| theme::card_style())
            .width(Length::Fill),
        ]
        .spacing(4)
        .width(Length::Fill),
    ]
    .spacing(8)
    .into()
}

/// User message: tinted bubble, right-aligned, at most two thirds wide.
fn user_bubble(m: &ChatMessage) -> Element<'_, Message> {
    let bubble = container(
        column![
            text("Kamu")
                .font(theme::font_semibold())
                .size(12)
                .color(theme::GRAY),
            text(&m.text)
                .font(theme::font_regular())
                .size(14)
                .color(theme::CHARCOAL)
                .width(Length::Fill),
        ]
        .spacing(4),
    )
    .padding(12)
    .style(|_| theme::tint_style(theme::RED_TINT))
    .width(Length::FillPortion(2));
    row![container(text("")).width(Length::FillPortion(1)), bubble,]
        .spacing(8)
        .into()
}

/// Unix timestamp (seconds) as "23 Nov 2026" (civil-from-days, no extra dep).
fn date_label(ts: f64) -> String {
    let days = (ts as i64 / 86_400).max(0);
    let z = days + 719_468;
    let era = z / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    let months = [
        "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
    ];
    format!("{} {} {}", d, months[(m - 1) as usize], y)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn send_is_ignored_when_draft_is_empty() {
        let mut state = State::new();
        let client = ApiClient::new("http://127.0.0.1:1");
        state.draft = "   ".to_string();
        let _ = state.update(Message::SendPressed, &client);
        assert!(!state.sending);
    }

    #[test]
    fn sent_error_keeps_draft_cleared_and_reports() {
        let mut state = State::new();
        state.sending = true;
        let client = ApiClient::new("http://127.0.0.1:1");
        let _ = state.update(Message::Sent(Err(ApiError::NotLoggedIn)), &client);
        assert!(!state.sending);
        assert!(state.error.as_deref().unwrap().contains("Belum masuk"));
    }

    #[test]
    fn loaded_messages_replace_thread() {
        let mut state = State::new();
        let client = ApiClient::new("http://127.0.0.1:1");
        let _ = state.update(
            Message::Loaded(Ok(MessagesResponse {
                messages: vec![ChatMessage {
                    sender: "users".to_string(),
                    text: "halo".to_string(),
                    created_at: 1.0,
                    attachments: vec![],
                }],
            })),
            &client,
        );
        assert!(state.loaded);
        assert_eq!(state.messages.len(), 1);
        assert_eq!(state.messages[0].sender_label(), "Kamu");
    }
}
