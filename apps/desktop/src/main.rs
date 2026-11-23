//! PuspaBot native desktop client — iced UI, no webview.
//!
//! The UI talks to the local Python backend over the localhost API
//! only; it never reads the database directly.
//!
//! Startup: probe the backend (first run downloads it, see
//! [`backend::probe_and_start`]), then restore a saved session
//! when one exists, else show the Google login screen. Once
//! logged in, the five native tabs (Beranda, Chat, Tugas, Memori,
//! Saya) load against the local backend. Only features the
//! backend really answers exist here — no mock data.

mod api;
mod auth;
mod backend;
mod screens;

use std::time::Duration;

use iced::theme::Palette;
use iced::widget::{button, column, container, row, text};
use iced::{Color, Element, Length, Subscription, Task, Theme};

use api::ApiClient;
use auth::{DesktopSession, LoginStatus};
use backend::BackendStatus;

/// Puspa design tokens (`puspabot-assets/brand/tokens.json`).
fn puspa_theme() -> Theme {
    Theme::custom(
        "Puspa".to_string(),
        Palette {
            background: Color::from_rgb8(0xFF, 0xF5, 0xF2), // cream
            text: Color::from_rgb8(0x1C, 0x1C, 0x1C),       // charcoal
            primary: Color::from_rgb8(0xD7, 0x40, 0x4D),    // Puspa red
            success: Color::from_rgb8(0x4C, 0xAF, 0x50),    // leaf green
            danger: Color::from_rgb8(0xD7, 0x40, 0x4D),
        },
    )
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Tab {
    Home,
    Chat,
    Tasks,
    Memory,
    Profile,
}

impl Tab {
    const ALL: [Tab; 5] = [Tab::Home, Tab::Chat, Tab::Tasks, Tab::Memory, Tab::Profile];

    fn label(self) -> &'static str {
        match self {
            Tab::Home => "Beranda",
            Tab::Chat => "Chat",
            Tab::Tasks => "Tugas",
            Tab::Memory => "Memori",
            Tab::Profile => "Saya",
        }
    }
}

#[derive(Debug, Clone)]
enum Message {
    TabSelected(Tab),
    BackendProbed(BackendStatus),
    BackendTick,
    RetryBackend,
    LoginPressed,
    LoginFinished(Result<DesktopSession, String>),
    SessionRestored(LoginStatus, Option<String>),
    Chat(screens::chat::Message),
    Tasks(screens::tasks::Message),
    Home(screens::home::Message),
    Memory(screens::memory::Message),
    Profile(screens::profile::Message),
}

/// Validate a saved session against the backend on a copy of the
/// client; returns the login status plus the token, so the caller
/// can put the token on its own client.
async fn restore_session_task(client: ApiClient) -> (LoginStatus, Option<String>) {
    let mut client = client;
    let status = auth::restore_session(&mut client).await;
    (status, client.session_token().map(str::to_string))
}

struct App {
    tab: Tab,
    backend: BackendStatus,
    client: Option<ApiClient>,
    base_url: Option<String>,
    logged_in: bool,
    user_id: Option<String>,
    restoring: bool,
    login_busy: bool,
    login_error: Option<String>,
    chat: screens::chat::State,
    tasks: screens::tasks::State,
    home: screens::home::State,
    memory: screens::memory::State,
    profile: screens::profile::State,
}

impl App {
    fn new() -> (Self, Task<Message>) {
        let app = Self {
            tab: Tab::Home,
            backend: BackendStatus::Checking,
            client: None,
            base_url: None,
            logged_in: false,
            user_id: None,
            restoring: false,
            login_busy: false,
            login_error: None,
            chat: screens::chat::State::new(),
            tasks: screens::tasks::State::new(),
            home: screens::home::State::new(),
            memory: screens::memory::State::new(),
            profile: screens::profile::State::new(),
        };
        // Probe the backend on a worker thread; the UI stays responsive.
        let probe = Task::perform(
            async {
                tokio::task::spawn_blocking(backend::probe_and_start)
                    .await
                    .unwrap_or_else(|_| BackendStatus::Failed("backend probe panicked".to_string()))
            },
            Message::BackendProbed,
        );
        (app, probe)
    }

    fn probe_backend() -> Task<Message> {
        Task::perform(
            async {
                tokio::task::spawn_blocking(backend::probe_and_start)
                    .await
                    .unwrap_or_else(|_| BackendStatus::Failed("backend probe panicked".to_string()))
            },
            Message::BackendProbed,
        )
    }

    /// Reload one screen's data when its tab is selected. Only
    /// Beranda needs it: it is a dashboard of activity that
    /// happened while another tab was open (the operator log must
    /// show a chat message or task created on the Chat/Tugas tab).
    /// The other tabs reload through their own actions, and
    /// reloading them on every tab switch would wipe a half-typed
    /// draft in their inputs.
    fn load_tab(&mut self, tab: Tab) -> Task<Message> {
        if tab != Tab::Home {
            return Task::none();
        }
        let Some(client) = self.client.clone() else {
            return Task::none();
        };
        self.home.user_id = self.user_id.clone();
        let user_id = self.user_id.clone();
        screens::home::State::load(client, user_id).map(Message::Home)
    }

    /// Dispatch the first load of all five screens (once per login).
    fn load_screens(&mut self) -> Task<Message> {
        let Some(client) = &self.client else {
            return Task::none();
        };
        // The Home tab needs the user id for its approvals section
        // (the agent id is `puspa-{user_id}`); the load alone does
        // not store it on the state.
        self.home.user_id = self.user_id.clone();
        let user_id = self.user_id.clone();
        Task::batch([
            screens::chat::State::load(client.clone()).map(Message::Chat),
            screens::tasks::State::load(client.clone()).map(Message::Tasks),
            screens::home::State::load(client.clone(), user_id).map(Message::Home),
            screens::memory::State::load(client.clone()).map(Message::Memory),
            screens::profile::State::load(client.clone()).map(Message::Profile),
        ])
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::TabSelected(tab) => {
                self.tab = tab;
                self.load_tab(tab)
            }
            Message::BackendProbed(status) => {
                if let BackendStatus::Ready { base_url } = status {
                    let client = ApiClient::new(base_url.clone());
                    self.base_url = Some(base_url.clone());
                    self.client = Some(client.clone());
                    self.backend = BackendStatus::Ready { base_url };
                    self.restoring = true;
                    Task::perform(restore_session_task(client), |(status, token)| {
                        Message::SessionRestored(status, token)
                    })
                } else {
                    self.backend = status;
                    Task::none()
                }
            }
            Message::BackendTick => {
                if let Some(status) = backend::current_download_status() {
                    self.backend = status;
                }
                Task::none()
            }
            Message::RetryBackend => {
                self.backend = BackendStatus::Checking;
                self.client = None;
                self.base_url = None;
                Self::probe_backend()
            }
            Message::LoginPressed => {
                if self.login_busy || self.restoring {
                    return Task::none();
                }
                let Some(client) = self.client.clone() else {
                    return Task::none();
                };
                let base_url = self.base_url.clone().unwrap_or_default();
                self.login_busy = true;
                self.login_error = None;
                Task::perform(
                    async move {
                        let mut client = client;
                        auth::login_via_browser(&mut client, &base_url)
                            .await
                            .map_err(|err| err.to_string())
                    },
                    Message::LoginFinished,
                )
            }
            Message::LoginFinished(result) => {
                self.login_busy = false;
                match result {
                    Ok(session) => {
                        if let Some(client) = &mut self.client {
                            client.set_session_token(Some(session.session_token.clone()));
                        }
                        self.user_id = Some(session.user_id);
                        self.logged_in = true;
                        self.login_error = None;
                        self.load_screens()
                    }
                    Err(err) => {
                        self.login_error = Some(format!("Login gagal: {err}"));
                        Task::none()
                    }
                }
            }
            Message::SessionRestored(status, token) => {
                self.restoring = false;
                match status {
                    LoginStatus::LoggedIn { user_id } => {
                        if let (Some(client), Some(token)) = (&mut self.client, token) {
                            client.set_session_token(Some(token));
                        }
                        self.user_id = Some(user_id);
                        self.logged_in = true;
                        self.load_screens()
                    }
                    LoginStatus::NotLoggedIn => Task::none(),
                    LoginStatus::Failed(reason) => {
                        self.login_error =
                            Some(format!("Sesi tersimpan tidak bisa dipakai: {reason}"));
                        Task::none()
                    }
                }
            }
            Message::Chat(message) => {
                if matches!(message, screens::chat::Message::GoToSettings) {
                    self.tab = Tab::Profile;
                    return Task::none();
                }
                let Some(client) = self.client.clone() else {
                    return Task::none();
                };
                self.chat.update(message, &client).map(Message::Chat)
            }
            Message::Tasks(message) => {
                let Some(client) = self.client.clone() else {
                    return Task::none();
                };
                self.tasks.update(message, &client).map(Message::Tasks)
            }
            Message::Home(message) => {
                let Some(client) = self.client.clone() else {
                    return Task::none();
                };
                self.home.update(message, &client).map(Message::Home)
            }
            Message::Memory(message) => {
                let Some(client) = self.client.clone() else {
                    return Task::none();
                };
                self.memory.update(message, &client).map(Message::Memory)
            }
            Message::Profile(message) => {
                if matches!(message, screens::profile::Message::LogoutPressed) {
                    auth::clear_saved_session();
                    if let Some(client) = &mut self.client {
                        client.set_session_token(None);
                    }
                    self.logged_in = false;
                    self.user_id = None;
                    self.tab = Tab::Home;
                    self.chat = screens::chat::State::new();
                    self.tasks = screens::tasks::State::new();
                    self.home = screens::home::State::new();
                    self.memory = screens::memory::State::new();
                    self.profile = screens::profile::State::new();
                    return Task::none();
                }
                let Some(client) = self.client.clone() else {
                    return Task::none();
                };
                self.profile.update(message, &client).map(Message::Profile)
            }
        }
    }

    fn subscription(&self) -> Subscription<Message> {
        let mut subscriptions = vec![self.chat.subscription().map(Message::Chat)];
        if !matches!(self.backend, BackendStatus::Ready { .. }) {
            subscriptions
                .push(iced::time::every(Duration::from_millis(500)).map(|_| Message::BackendTick));
        }
        Subscription::batch(subscriptions)
    }

    fn view(&self) -> Element<'_, Message> {
        match &self.backend {
            BackendStatus::Ready { base_url } if !self.logged_in => self.login_view(base_url),
            BackendStatus::Ready { .. } => self.tabs_view(),
            _ => self.backend_view(),
        }
    }

    fn backend_view(&self) -> Element<'_, Message> {
        let status_text = match &self.backend {
            BackendStatus::Checking => "Menyalakan backend lokal...".to_string(),
            BackendStatus::Downloading { step } => format!("Mengunduh backend: {step}"),
            BackendStatus::Ready { base_url } => format!("Backend jalan di {base_url}"),
            BackendStatus::Unavailable(reason) => format!("Backend tidak tersedia: {reason}"),
            BackendStatus::Failed(reason) => format!("Backend gagal: {reason}"),
        };
        let mut content = column![text("PuspaBot").size(24), text(status_text).size(14),]
            .spacing(12)
            .padding(24);
        if matches!(self.backend, BackendStatus::Downloading { .. }) {
            let log = backend::install_log();
            for line in log.iter().rev().take(5).rev() {
                content = content.push(text(line.clone()).size(12));
            }
        }
        if matches!(
            self.backend,
            BackendStatus::Unavailable(_) | BackendStatus::Failed(_)
        ) {
            content = content.push(button("Coba lagi").on_press(Message::RetryBackend));
        }
        container(content)
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn login_view(&self, base_url: &str) -> Element<'_, Message> {
        let mut content = column![
            text("PuspaBot").size(24),
            text(format!("Backend lokal jalan di {base_url}")).size(14),
            text("Masuk dengan akun Google untuk memakai PuspaBot di komputer ini.").size(14),
        ]
        .spacing(12)
        .padding(24);
        if self.restoring {
            content = content.push(text("Memeriksa sesi tersimpan...").size(14));
        } else if self.login_busy {
            content = content.push(
                text("Login dibuka di browser. Selesaikan login Google di sana, lalu kembali ke sini.")
                    .size(14),
            );
        } else {
            content = content.push(button("Masuk dengan Google").on_press(Message::LoginPressed));
        }
        if let Some(error) = &self.login_error {
            content = content.push(
                text(error)
                    .size(13)
                    .color(Color::from_rgb8(0xD7, 0x40, 0x4D)),
            );
        }
        container(content)
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn tabs_view(&self) -> Element<'_, Message> {
        let sidebar = column(
            Tab::ALL
                .iter()
                .map(|tab| -> Element<'_, Message> {
                    button(text(tab.label()))
                        .on_press(Message::TabSelected(*tab))
                        .width(Length::Fill)
                        .into()
                })
                .collect::<Vec<_>>(),
        )
        .spacing(8)
        .padding(16)
        .width(Length::Fixed(200.0));

        let content: Element<'_, Message> = match self.tab {
            Tab::Home => self.home.view().map(Message::Home),
            Tab::Chat => self.chat.view().map(Message::Chat),
            Tab::Tasks => self.tasks.view().map(Message::Tasks),
            Tab::Memory => self.memory.view().map(Message::Memory),
            Tab::Profile => self.profile.view().map(Message::Profile),
        };

        container(row![sidebar, content])
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }
}

impl Drop for App {
    fn drop(&mut self) {
        backend::shutdown();
    }
}

fn main() -> iced::Result {
    iced::application(|_: &App| "PuspaBot".to_string(), App::update, App::view)
        .theme(|_: &App| puspa_theme())
        .subscription(App::subscription)
        .window(iced::window::Settings {
            size: iced::Size::new(1280.0, 800.0),
            // App icon from the official brand asset
            // (puspabot-assets/brand/puspa_app_icon_reference.png).
            icon: iced::window::icon::from_file_data(
                include_bytes!("../assets/puspa-icon.png"),
                None,
            )
            .ok(),
            ..iced::window::Settings::default()
        })
        .run_with(App::new)
}
