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
mod config;
mod screens;
mod theme;

use std::time::Duration;

use iced::theme::Palette;
use iced::widget::{
    button, column, container, image, progress_bar, row, svg, text, text_input, Space,
};
use iced::{Background, Color, Element, Length, Subscription, Task, Theme};

use api::ApiClient;
use auth::{DesktopSession, LoginStatus};
use backend::BackendStatus;
use config::OauthConfig;

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
    ConfigChecked(Option<bool>),
    SettingsPressed,
    SettingsClosed,
    SettingsClientIdChanged(String),
    SettingsClientSecretChanged(String),
    SettingsSavePressed,
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
    /// Google login readiness from `GET /api/desktop/config`:
    /// `None` = still checking, `Some(true)` = login can start,
    /// `Some(false)` = no OAuth client yet (show guidance, never
    /// open the browser to a raw error).
    google_configured: Option<bool>,
    /// The settings panel (Pengaturan Lainnya) over the welcome
    /// screen, where the Google OAuth client ID + secret are
    /// entered and saved to the user-owned config file.
    settings_open: bool,
    settings_client_id: String,
    settings_client_secret: String,
    settings_saved: Option<String>,
    settings_error: Option<String>,
    /// Indeterminate progress animation (0.0..=1.0), advanced on
    /// every backend tick; used only while no byte total is known.
    download_anim: f32,
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
            google_configured: None,
            settings_open: false,
            settings_client_id: config::load_oauth_config()
                .map(|c| c.client_id)
                .unwrap_or_default(),
            settings_client_secret: config::load_oauth_config()
                .map(|c| c.client_secret)
                .unwrap_or_default(),
            settings_saved: None,
            settings_error: None,
            download_anim: 0.0,
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
                    // Check whether Google login can start before
                    // the login button is shown: when no OAuth
                    // client exists the app shows guidance instead
                    // of opening the browser to a raw error.
                    let config_client = client.clone();
                    let check_config = Task::perform(
                        async move {
                            let value: Result<serde_json::Value, _> =
                                config_client.get_json("/api/desktop/config").await;
                            value
                                .ok()
                                .and_then(|v| v.get("google_configured")?.as_bool())
                        },
                        Message::ConfigChecked,
                    );
                    Task::batch([
                        Task::perform(restore_session_task(client), |(status, token)| {
                            Message::SessionRestored(status, token)
                        }),
                        check_config,
                    ])
                } else {
                    self.backend = status;
                    Task::none()
                }
            }
            Message::BackendTick => {
                // Advance the indeterminate animation; the bar
                // shows real bytes when the download reports them.
                self.download_anim = (self.download_anim + 0.12).rem_euclid(1.0);
                if let Some(status) = backend::current_download_status() {
                    self.backend = status;
                }
                Task::none()
            }
            Message::ConfigChecked(configured) => {
                self.google_configured = configured;
                Task::none()
            }
            Message::SettingsPressed => {
                self.settings_open = true;
                self.settings_saved = None;
                self.settings_error = None;
                Task::none()
            }
            Message::SettingsClosed => {
                self.settings_open = false;
                Task::none()
            }
            Message::SettingsClientIdChanged(value) => {
                self.settings_client_id = value;
                self.settings_saved = None;
                Task::none()
            }
            Message::SettingsClientSecretChanged(value) => {
                self.settings_client_secret = value;
                self.settings_saved = None;
                Task::none()
            }
            Message::SettingsSavePressed => {
                let config = OauthConfig {
                    client_id: self.settings_client_id.trim().to_string(),
                    client_secret: self.settings_client_secret.trim().to_string(),
                };
                match config::save_oauth_config(&config) {
                    Ok(()) => {
                        // The backend reads the file on every
                        // request, so no restart is needed: the
                        // next login attempt sees the new client.
                        self.google_configured = Some(config.is_complete());
                        self.settings_saved = Some(if config.is_complete() {
                            "Tersimpan. Login Google sudah bisa dipakai.".to_string()
                        } else {
                            "Tersimpan, tapi Client ID dan Client Secret masih kosong.".to_string()
                        });
                        self.settings_error = None;
                    }
                    Err(err) => {
                        self.settings_error =
                            Some(format!("Tidak bisa menyimpan pengaturan: {err}"));
                        self.settings_saved = None;
                    }
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
        if self.logged_in {
            return self.tabs_view();
        }
        self.welcome_view()
    }

    /// The welcome screen: the same two-panel Puspa design for
    /// every pre-login state (backend starting, downloading,
    /// ready, failed) — left: cream panel with the mascot; right:
    /// white panel with the heading, backend card, progress bar,
    /// and the Google sign-in. No plain white screen anywhere.
    fn welcome_view(&self) -> Element<'_, Message> {
        let mascot =
            image::Image::new(image::Handle::from_bytes(theme::MASCOT)).width(Length::Fixed(280.0));
        let left = container(
            column![
                row![
                    image::Image::new(image::Handle::from_bytes(theme::MASCOT))
                        .width(Length::Fixed(40.0)),
                    text("PuspaBot")
                        .font(theme::font_bold())
                        .size(22)
                        .color(theme::CHARCOAL),
                ]
                .spacing(10)
                .align_y(iced::Alignment::Center),
                text("Satu asisten, sejuta kemungkinan")
                    .font(theme::font_regular())
                    .size(14)
                    .color(theme::GRAY),
                mascot,
            ]
            .spacing(16)
            .padding(32)
            .align_x(iced::Alignment::Center),
        )
        .style(|_| theme::cream_style())
        .width(Length::Fixed(420.0))
        .height(Length::Fill);

        let right = container(self.welcome_right())
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(48)
            .style(|_| container::Style {
                background: Some(Background::Color(theme::WHITE)),
                ..Default::default()
            });

        container(row![left, right])
            .width(Length::Fixed(1000.0))
            .height(Length::Fill)
            .center_x(Length::Fill)
            .style(|_| theme::cream_style())
            .into()
    }

    /// The right panel of the welcome screen: heading + subtitle,
    /// then the content for the current state (download progress,
    /// backend card + login, settings, or an honest failure).
    fn welcome_right(&self) -> Element<'_, Message> {
        if self.settings_open {
            return self.settings_view();
        }
        let mut content = column![
            text("Selamat datang di PuspaBot")
                .font(theme::font_bold())
                .size(30)
                .color(theme::CHARCOAL),
            text("Asisten pribadi yang selalu siap membantu, di komputer kamu.")
                .font(theme::font_regular())
                .size(15)
                .color(theme::GRAY),
        ]
        .spacing(10);
        match &self.backend {
            BackendStatus::Checking => {
                content = content
                    .push(
                        text("Menyalakan backend lokal...")
                            .font(theme::font_regular())
                            .size(14),
                    )
                    .push(progress_bar(0.0..=1.0, self.download_anim).height(8));
            }
            BackendStatus::Downloading { step, progress } => {
                let value = progress.unwrap_or(self.download_anim);
                content = content
                    .push(text(step.clone()).font(theme::font_regular()).size(14))
                    .push(progress_bar(0.0..=1.0, value).height(8));
                if let Some(fraction) = progress {
                    content = content.push(
                        text(format!("{:.0}%", fraction * 100.0))
                            .font(theme::font_medium())
                            .size(13)
                            .color(theme::GRAY),
                    );
                }
                for line in backend::install_log().iter().rev().take(3).rev() {
                    content = content.push(
                        text(line.clone())
                            .font(theme::font_regular())
                            .size(12)
                            .color(theme::GRAY),
                    );
                }
            }
            BackendStatus::Ready { base_url } => {
                content = content.push(self.backend_card(base_url));
                content = content.push(self.login_block());
            }
            BackendStatus::Unavailable(reason) | BackendStatus::Failed(reason) => {
                content = content
                    .push(
                        text(reason.clone())
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::RED),
                    )
                    .push(
                        button(text("Coba lagi").font(theme::font_semibold()).size(15))
                            .on_press(Message::RetryBackend)
                            .style(theme::outline_button)
                            .padding([12, 28]),
                    );
            }
        }
        if let Some(error) = &self.login_error {
            content = content.push(
                text(error.clone())
                    .font(theme::font_regular())
                    .size(13)
                    .color(theme::RED),
            );
        }
        content.spacing(16).into()
    }

    /// The backend address card (reference design): tinted panel
    /// with "Backend lokal jalan di" + the address in bold.
    fn backend_card(&self, base_url: &str) -> Element<'_, Message> {
        container(
            column![
                text("Backend lokal jalan di")
                    .font(theme::font_regular())
                    .size(13)
                    .color(theme::GRAY),
                text(base_url.to_string())
                    .font(theme::font_bold())
                    .size(17)
                    .color(theme::CHARCOAL),
            ]
            .spacing(4)
            .padding(16),
        )
        .style(|_| theme::tint_style(theme::CREAM))
        .width(Length::Fill)
        .into()
    }

    /// The login block on the welcome screen. The Google button
    /// only appears when the backend says a login can start;
    /// without an OAuth client the app shows guidance + a button
    /// to the settings — it never opens the browser to a raw
    /// JSON error.
    fn login_block(&self) -> Element<'_, Message> {
        match self.google_configured {
            Some(true) => {
                let google_button = button(
                    row![
                        svg::Svg::new(svg::Handle::from_memory(theme::GOOGLE_G))
                            .width(Length::Fixed(22.0))
                            .height(Length::Fixed(22.0)),
                        text("Masuk dengan Google")
                            .font(theme::font_semibold())
                            .size(16)
                            .color(theme::WHITE),
                    ]
                    .spacing(12)
                    .align_y(iced::Alignment::Center),
                )
                .on_press(Message::LoginPressed)
                .style(theme::primary_button)
                .padding([14, 32]);
                let busy = if self.restoring {
                    text("Memeriksa sesi tersimpan...")
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::GRAY)
                } else if self.login_busy {
                    text("Login dibuka di browser. Selesaikan login Google di sana, lalu kembali ke sini.")
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::GRAY)
                } else {
                    text("Masuk dengan akun Google untuk memulai PuspaBot di komputer ini.")
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::GRAY)
                };
                column![
                    busy,
                    google_button,
                    row![
                        Space::new(Length::Fill, Length::Shrink),
                        text("atau")
                            .font(theme::font_regular())
                            .size(13)
                            .color(theme::GRAY),
                        Space::new(Length::Fill, Length::Shrink),
                    ]
                    .spacing(12)
                    .align_y(iced::Alignment::Center),
                    button(text("Pengaturan Lainnya").font(theme::font_semibold()).size(15))
                        .on_press(Message::SettingsPressed)
                        .style(theme::outline_button)
                        .padding([12, 28]),
                ]
                .spacing(16)
                .align_x(iced::Alignment::Center)
                .into()
            }
            Some(false) => column![
                text("Login Google belum disiapkan di komputer ini.")
                    .font(theme::font_semibold())
                    .size(15)
                    .color(theme::CHARCOAL),
                text("PuspaBot belum punya kredensial login Google. Buat kredensialnya di Google Cloud Console (tipe Desktop app), lalu masukkan Client ID dan Client Secret di Pengaturan Lainnya.")
                    .font(theme::font_regular())
                    .size(14)
                    .color(theme::GRAY),
                button(text("Buka Pengaturan Lainnya").font(theme::font_semibold()).size(15))
                    .on_press(Message::SettingsPressed)
                    .style(theme::primary_button)
                    .padding([12, 28]),
            ]
            .spacing(12)
            .into(),
            None => text("Memeriksa pengaturan login...")
                .font(theme::font_regular())
                .size(14)
                .color(theme::GRAY)
                .into(),
        }
    }

    /// "Pengaturan Lainnya": backend address, Google OAuth client
    /// ID + secret (saved to the user-owned config file), and the
    /// link to the setup guide. Replaces the welcome right panel.
    fn settings_view(&self) -> Element<'_, Message> {
        let mut content = column![
            text("Pengaturan Lainnya")
                .font(theme::font_bold())
                .size(26)
                .color(theme::CHARCOAL),
            text("Backend dan login Google di komputer ini.")
                .font(theme::font_regular())
                .size(14)
                .color(theme::GRAY),
        ]
        .spacing(8);
        if let BackendStatus::Ready { base_url } = &self.backend {
            content = content.push(self.backend_card(base_url));
        }
        content = content.push(
            column![
                text("Login Google")
                    .font(theme::font_semibold())
                    .size(16)
                    .color(theme::CHARCOAL),
                text("Buat kredensial di Google Cloud Console (OAuth client, tipe Desktop app), lalu tempel Client ID dan Client Secret di sini. Panduan lengkap: docs/desktop-google-oauth-setup.md. Kredensial tersimpan di komputer ini saja, tidak pernah dikirim ke mana pun.")
                    .font(theme::font_regular())
                    .size(13)
                    .color(theme::GRAY),
                text("Client ID")
                    .font(theme::font_semibold())
                    .size(13)
                    .color(theme::CHARCOAL),
                text_input("Contoh: 1234567890-abc.apps.googleusercontent.com", &self.settings_client_id)
                    .on_input(Message::SettingsClientIdChanged)
                    .style(theme::input_style)
                    .padding(12),
                text("Client Secret")
                    .font(theme::font_semibold())
                    .size(13)
                    .color(theme::CHARCOAL),
                text_input("Client Secret Google", &self.settings_client_secret)
                    .on_input(Message::SettingsClientSecretChanged)
                    .secure(true)
                    .style(theme::input_style)
                    .padding(12),
                button(text("Simpan").font(theme::font_semibold()).size(15))
                    .on_press(Message::SettingsSavePressed)
                    .style(theme::primary_button)
                    .padding([12, 28]),
            ]
            .spacing(10),
        );
        if let Some(saved) = &self.settings_saved {
            content = content.push(
                text(saved.clone())
                    .font(theme::font_regular())
                    .size(13)
                    .color(theme::GREEN),
            );
        }
        if let Some(error) = &self.settings_error {
            content = content.push(
                text(error.clone())
                    .font(theme::font_regular())
                    .size(13)
                    .color(theme::RED),
            );
        }
        content = content.push(
            button(text("Kembali").font(theme::font_semibold()).size(15))
                .on_press(Message::SettingsClosed)
                .style(theme::outline_button)
                .padding([12, 28]),
        );
        content.spacing(16).into()
    }

    fn tabs_view(&self) -> Element<'_, Message> {
        let tab_icon = |tab: Tab| match tab {
            Tab::Home => "home",
            Tab::Chat => "chat",
            Tab::Tasks => "tasks",
            Tab::Memory => "memory",
            Tab::Profile => "profile",
        };
        let sidebar = column![
            row![
                image::Image::new(image::Handle::from_bytes(theme::MASCOT))
                    .width(Length::Fixed(36.0)),
                text("PuspaBot")
                    .font(theme::font_bold())
                    .size(19)
                    .color(theme::CHARCOAL),
            ]
            .spacing(10)
            .padding([4, 8])
            .align_y(iced::Alignment::Center),
            column(
                Tab::ALL
                    .iter()
                    .map(|tab| -> Element<'_, Message> {
                        let active = *tab == self.tab;
                        let icon_color = if active { theme::RED } else { theme::CHARCOAL };
                        button(
                            row![
                                theme::icon(tab_icon(*tab), icon_color, 20.0),
                                text(tab.label())
                                    .font(if active {
                                        theme::font_semibold()
                                    } else {
                                        theme::font_regular()
                                    })
                                    .size(15),
                            ]
                            .spacing(12)
                            .align_y(iced::Alignment::Center)
                            .padding([4, 8]),
                        )
                        .on_press(Message::TabSelected(*tab))
                        .style(theme::tab_button(active))
                        .width(Length::Fill)
                        .into()
                    })
                    .collect::<Vec<_>>(),
            )
            .spacing(6),
            Space::new(Length::Shrink, Length::Fill),
            text("PuspaBot 0.1.0")
                .font(theme::font_regular())
                .size(12)
                .color(theme::GRAY),
        ]
        .spacing(16)
        .padding(16)
        .width(Length::Fixed(220.0));

        let content: Element<'_, Message> = match self.tab {
            Tab::Home => self.home.view().map(Message::Home),
            Tab::Chat => self.chat.view().map(Message::Chat),
            Tab::Tasks => self.tasks.view().map(Message::Tasks),
            Tab::Memory => self.memory.view().map(Message::Memory),
            Tab::Profile => self.profile.view().map(Message::Profile),
        };

        container(
            row![
                container(sidebar)
                    .style(|_| theme::cream_style())
                    .height(Length::Fill),
                container(content).padding(24).width(Length::Fill),
            ]
            .spacing(0),
        )
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
        .font(theme::INTER_FONT)
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
