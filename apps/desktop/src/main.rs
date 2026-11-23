//! PuspaBot native desktop client — iced UI, no webview.
//!
//! The UI talks to the local Python backend over the localhost API
//! only; it never reads the database directly.

mod api;
mod backend;

use iced::theme::Palette;
use iced::widget::{button, column, container, row, text};
use iced::{Color, Element, Length, Task, Theme};

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
}

struct App {
    tab: Tab,
    backend: BackendStatus,
}

impl App {
    fn new() -> (Self, Task<Message>) {
        let app = Self {
            tab: Tab::Home,
            backend: BackendStatus::Checking,
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

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::TabSelected(tab) => self.tab = tab,
            Message::BackendProbed(status) => self.backend = status,
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
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

        let status_text = match &self.backend {
            BackendStatus::Checking => "Menyalakan backend lokal...".to_string(),
            BackendStatus::Ready { base_url } => format!("Backend jalan di {base_url}"),
            BackendStatus::Unavailable(reason) => format!("Backend tidak tersedia: {reason}"),
            BackendStatus::Failed(reason) => format!("Backend gagal: {reason}"),
        };

        // v1 shell: real screens land one by one (Chat first).
        // No mock data, no dead buttons: only the tab bar and the
        // honest backend status exist so far.
        let content = column![
            text(format!("{} — layar native, segera", self.tab.label())).size(24),
            text(status_text).size(14),
        ]
        .spacing(12)
        .padding(24);

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
