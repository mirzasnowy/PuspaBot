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

use iced::widget::{button, column, container, row, scrollable, text, text_input};
use iced::{Element, Length, Subscription, Task};
use serde::Deserialize;

use crate::api::{ApiClient, ApiError};

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
        let mut content = column![text("Chat").size(24)].spacing(12);

        if self.has_key == Some(false) {
            content = content.push(
                column![
                    text("Otak belum bisa menjawab: belum ada kunci model (BYOK).").size(14),
                    text("Pesanmu tetap tersimpan, tapi Puspa tidak bisa membalas sampai ada kunci. Tambahkan di Saya → Pengaturan model.").size(14),
                    button(text("Buka pengaturan model")).on_press(Message::GoToSettings),
                ]
                .spacing(6),
            );
        }

        if let Some(error) = &self.error {
            content = content.push(
                column![
                    text(error).size(14),
                    button(text("Coba lagi")).on_press(Message::ReloadPressed),
                ]
                .spacing(6),
            );
        }

        if !self.loaded && self.messages.is_empty() {
            content = content.push(text("Memuat chat…").size(14));
        } else if self.messages.is_empty() {
            content = content.push(text("Belum ada pesan. Sapa Puspa dulu.").size(14));
        } else {
            let thread = column(
                self.messages
                    .iter()
                    .map(|m| {
                        text(format!("{}: {}", m.sender_label(), m.text))
                            .size(14)
                            .width(Length::Fill)
                            .into()
                    })
                    .collect::<Vec<_>>(),
            )
            .spacing(8);
            content = content.push(scrollable(thread).width(Length::Fill).height(Length::Fill));
        }

        let mut send_button = button(text(if self.sending { "Mengirim…" } else { "Kirim" }));
        if !self.sending && !self.draft.trim().is_empty() {
            send_button = send_button.on_press(Message::SendPressed);
        }
        let composer = row![
            text_input("Tulis pesan…", &self.draft)
                .on_input(Message::DraftChanged)
                .on_submit(Message::SendPressed)
                .width(Length::Fill),
            send_button,
        ]
        .spacing(8);
        content = content.push(composer);

        container(content)
            .padding(24)
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }
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
