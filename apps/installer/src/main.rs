//! PuspaBot native installer — replaces the NSIS installer.
//!
//! Double-click the .exe: a two-panel Puspa welcome (the same
//! design as the app's first-run screen) with a real progress
//! bar. It installs the embedded `puspabot.exe` payload to
//! Program Files, copies itself as the uninstaller, registers
//! the app in the Windows uninstall list, creates Start Menu +
//! Desktop shortcuts, and launches PuspaBot when done — no
//! hunting for the launcher afterwards.
//!
//! Headless modes (CI + power users):
//!   --install-quiet [--install-dir <path>]  install, no window
//!   --uninstall [--install-dir <path>]       remove everything
//!   --self-test                              install to a temp
//!     dir, verify files (+ registry/shortcuts on Windows),
//!     uninstall, verify removal; exit 0 on success.

mod install;

/// The design system is shared with the desktop app; the
/// installer uses only a subset of it, so dead-code warnings
/// for the rest are silenced here (the desktop crate still
/// warns about genuinely unused helpers).
#[allow(dead_code)]
#[path = "../../desktop/src/theme.rs"]
mod theme;

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use iced::widget::{button, column, container, image, progress_bar, row, text};
use iced::{Element, Length, Subscription, Task};

/// The embedded app payload (see build.rs). Empty when the
/// installer was built without one.
const PAYLOAD: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/payload.exe"));
const PAYLOAD_PRESENT: &str = env!("PAYLOAD_PRESENT");

/// Install progress shared with the worker thread: (fraction,
/// step text). The UI polls it on a tick.
static INSTALL_PROGRESS: Mutex<Option<(f32, String)>> = Mutex::new(None);

fn set_progress(fraction: f32, step: &str) {
    *INSTALL_PROGRESS.lock().expect("install progress lock") = Some((fraction, step.to_string()));
}

#[derive(Debug, Clone)]
enum Message {
    InstallPressed,
    InstallFinished(Result<PathBuf, String>),
    Tick,
    ClosePressed,
}

#[derive(Debug, Clone, PartialEq)]
enum State {
    Welcome,
    Installing,
    Done,
    Failed(String),
}

struct Installer {
    state: State,
    progress: f32,
    step: String,
    install_dir: PathBuf,
    done_at: Option<Instant>,
    /// Set when the install succeeded but the app could not be
    /// launched automatically: the Done screen says so honestly
    /// instead of claiming the app is opening.
    launch_failed: Option<String>,
}

impl Installer {
    fn new() -> (Self, Task<Message>) {
        (
            Self {
                state: State::Welcome,
                progress: 0.0,
                step: String::new(),
                install_dir: install::default_install_dir(),
                done_at: None,
                launch_failed: None,
            },
            Task::none(),
        )
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::InstallPressed => {
                if !matches!(self.state, State::Welcome | State::Failed(_)) {
                    return Task::none();
                }
                self.state = State::Installing;
                self.progress = 0.0;
                self.step = "Memulai pemasangan...".to_string();
                let dir = self.install_dir.clone();
                Task::perform(
                    async move {
                        tokio::task::spawn_blocking(move || {
                            let opts = install::InstallOptions::new(dir);
                            install::install(PAYLOAD, &opts, &mut |fraction, step| {
                                set_progress(fraction, step);
                            })
                        })
                        .await
                        .unwrap_or_else(|_| Err("pemasangan berhenti mendadak".to_string()))
                    },
                    Message::InstallFinished,
                )
            }
            Message::InstallFinished(result) => {
                match result {
                    Ok(_) => {
                        self.state = State::Done;
                        self.progress = 1.0;
                        self.step = "Selesai.".to_string();
                        self.done_at = Some(Instant::now());
                        // Launch is separate from install: a launch
                        // failure never turns a good install into a
                        // failed one, it is reported on the Done
                        // screen instead.
                        if let Err(reason) = install::launch(&self.install_dir) {
                            self.launch_failed = Some(reason);
                        }
                    }
                    Err(reason) => {
                        self.state = State::Failed(reason);
                    }
                }
                Task::none()
            }
            Message::Tick => {
                if let Some((fraction, step)) = INSTALL_PROGRESS
                    .lock()
                    .expect("install progress lock")
                    .clone()
                {
                    self.progress = fraction;
                    self.step = step;
                }
                // Close the installer shortly after a successful
                // install: the app is already launching.
                if self.state == State::Done {
                    if let Some(done_at) = self.done_at {
                        if done_at.elapsed() >= Duration::from_secs(2) {
                            std::process::exit(0);
                        }
                    }
                }
                Task::none()
            }
            Message::ClosePressed => {
                std::process::exit(0);
            }
        }
    }

    fn subscription(&self) -> Subscription<Message> {
        if matches!(self.state, State::Installing | State::Done) {
            iced::time::every(Duration::from_millis(100)).map(|_| Message::Tick)
        } else {
            Subscription::none()
        }
    }

    fn view(&self) -> Element<'_, Message> {
        let mascot =
            image::Image::new(image::Handle::from_bytes(theme::MASCOT)).width(Length::Fixed(240.0));
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
        .width(Length::Fixed(360.0))
        .height(Length::Fill);

        let right = container(self.right_view())
            .width(Length::Fill)
            .height(Length::Fill)
            .padding(40)
            .style(|_| container::Style {
                background: Some(iced::Background::Color(theme::WHITE)),
                ..Default::default()
            });

        container(row![left, right])
            .width(Length::Fill)
            .height(Length::Fill)
            .style(|_| theme::cream_style())
            .into()
    }

    fn right_view(&self) -> Element<'_, Message> {
        let mut content = column![
            text("Selamat datang di PuspaBot")
                .font(theme::font_bold())
                .size(28)
                .color(theme::CHARCOAL),
            text("Pemasangan PuspaBot di komputer kamu.")
                .font(theme::font_regular())
                .size(15)
                .color(theme::GRAY),
            text(format!("Akan dipasang di {}", self.install_dir.display()))
                .font(theme::font_regular())
                .size(13)
                .color(theme::GRAY),
        ]
        .spacing(10);
        match &self.state {
            State::Welcome => {
                if PAYLOAD_PRESENT != "1" {
                    content = content.push(
                        text("Installer ini dibuat tanpa payload: build aplikasi desktop dulu, baru installer.")
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::RED),
                    );
                } else {
                    content = content.push(
                        button(
                            text("Pasang PuspaBot")
                                .font(theme::font_semibold())
                                .size(16),
                        )
                        .on_press(Message::InstallPressed)
                        .style(theme::primary_button)
                        .padding([14, 32]),
                    );
                }
            }
            State::Installing => {
                content = content
                    .push(text(self.step.clone()).font(theme::font_regular()).size(14))
                    .push(progress_bar(0.0..=1.0, self.progress).height(8))
                    .push(
                        text(format!("{:.0}%", self.progress * 100.0))
                            .font(theme::font_medium())
                            .size(13)
                            .color(theme::GRAY),
                    );
            }
            State::Done => {
                content = content
                    .push(
                        row![
                            theme::icon("check", theme::GREEN, 24.0),
                            text("PuspaBot berhasil dipasang.")
                                .font(theme::font_semibold())
                                .size(16)
                                .color(theme::CHARCOAL),
                        ]
                        .spacing(10)
                        .align_y(iced::Alignment::Center),
                    )
                    .push(
                        match &self.launch_failed {
                            Some(reason) => text(format!(
                                "Terpasang, tapi tidak bisa dibuka otomatis: {reason}. Buka PuspaBot dari Start Menu."
                            )),
                            None => text("Aplikasi sedang dibuka. Jendela ini akan menutup sendiri."),
                        }
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::GRAY),
                    )
                    .push(
                        button(text("Tutup").font(theme::font_semibold()).size(15))
                            .on_press(Message::ClosePressed)
                            .style(theme::outline_button)
                            .padding([12, 28]),
                    );
            }
            State::Failed(reason) => {
                content = content
                    .push(
                        text(format!("Pemasangan gagal: {reason}"))
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::RED),
                    )
                    .push(
                        button(text("Coba lagi").font(theme::font_semibold()).size(15))
                            .on_press(Message::InstallPressed)
                            .style(theme::primary_button)
                            .padding([12, 28]),
                    );
            }
        }
        content.spacing(16).into()
    }
}

fn install_dir_arg(args: &[String]) -> PathBuf {
    let mut dir = install::default_install_dir();
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--install-dir" {
            if let Some(value) = args.get(i + 1) {
                dir = PathBuf::from(value);
            }
            i += 1;
        }
        i += 1;
    }
    dir
}

/// Headless install (CI / power users). Returns the process exit
/// code: 0 when the install succeeded. A launch failure is a
/// warning, not an install failure: the app is installed, it just
/// did not open by itself.
fn headless_install(dir: PathBuf) -> i32 {
    let opts = install::InstallOptions::new(dir);
    match install::install(PAYLOAD, &opts, &mut |fraction, step| {
        println!("{:.0}% {step}", fraction * 100.0);
    }) {
        Ok(path) => {
            println!("installed: {}", path.display());
            if opts.launch_after {
                if let Err(reason) = install::launch(&opts.dir) {
                    eprintln!("warning: installed, but could not launch automatically: {reason}");
                }
            }
            0
        }
        Err(reason) => {
            eprintln!("install failed: {reason}");
            1
        }
    }
}

/// Headless uninstall. Returns the process exit code.
fn headless_uninstall(dir: PathBuf) -> i32 {
    match install::uninstall(&dir) {
        Ok(()) => {
            println!("uninstalled: {}", dir.display());
            0
        }
        Err(reason) => {
            eprintln!("uninstall failed: {reason}");
            1
        }
    }
}

/// Headless self-test (CI on windows-latest): install a fake
/// payload to a temp dir, verify files (+ registry/shortcuts on
/// Windows), uninstall, verify removal. Exit 0 on success.
fn self_test() -> i32 {
    let dir = std::env::temp_dir().join(format!("puspabot-selftest-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let payload = b"puspabot-self-test-payload";
    let opts = install::InstallOptions {
        dir: dir.clone(),
        launch_after: false,
    };
    let fail = |reason: &str| {
        eprintln!("self-test failed: {reason}");
        let _ = std::fs::remove_dir_all(&dir);
        1
    };
    if let Err(reason) = install::install(payload, &opts, &mut |_, _| {}) {
        return fail(&format!("install: {reason}"));
    }
    if !install::is_installed(&dir) {
        return fail("app binary missing after install");
    }
    let written = match std::fs::read(dir.join(install::APP_EXE)) {
        Ok(bytes) => bytes,
        Err(err) => return fail(&format!("cannot read installed app: {err}")),
    };
    if written.as_slice() != payload {
        return fail("installed app bytes differ from the payload");
    }
    if !dir.join(install::UNINSTALL_EXE).is_file() {
        return fail("uninstaller copy missing after install");
    }
    #[cfg(windows)]
    {
        if let Err(reason) = verify_windows_install(&dir) {
            return fail(&reason);
        }
    }
    if let Err(reason) = install::uninstall(&dir) {
        return fail(&format!("uninstall: {reason}"));
    }
    if install::is_installed(&dir) || dir.exists() {
        return fail("install dir still exists after uninstall");
    }
    println!("self-test passed: {}", dir.display());
    0
}

/// Windows-only self-test checks: uninstall registry key +
/// shortcuts exist.
#[cfg(windows)]
fn verify_windows_install(dir: &std::path::Path) -> Result<(), String> {
    use winreg::enums::HKEY_LOCAL_MACHINE;
    use winreg::RegKey;
    let key = RegKey::predef(HKEY_LOCAL_MACHINE)
        .open_subkey(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PuspaBot")
        .map_err(|e| format!("uninstall registry key missing: {e}"))?;
    let name: String = key
        .get_value("DisplayName")
        .map_err(|e| format!("DisplayName missing: {e}"))?;
    if name != "PuspaBot" {
        return Err(format!("DisplayName is {name}, expected PuspaBot"));
    }
    let _ = dir;
    let start_menu = std::env::var_os("APPDATA").map(|base| {
        std::path::PathBuf::from(base)
            .join("Microsoft")
            .join("Windows")
            .join("Start Menu")
            .join("Programs")
            .join("PuspaBot")
            .join("PuspaBot.lnk")
    });
    if let Some(link) = start_menu {
        if !link.is_file() {
            return Err(format!("Start Menu shortcut missing: {}", link.display()));
        }
    }
    let desktop = std::env::var_os("USERPROFILE").map(|base| {
        std::path::PathBuf::from(base)
            .join("Desktop")
            .join("PuspaBot.lnk")
    });
    if let Some(link) = desktop {
        if !link.is_file() {
            return Err(format!("Desktop shortcut missing: {}", link.display()));
        }
    }
    Ok(())
}

fn main() -> iced::Result {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|arg| arg == "--self-test") {
        std::process::exit(self_test());
    }
    if args.iter().any(|arg| arg == "--uninstall") {
        std::process::exit(headless_uninstall(install_dir_arg(&args)));
    }
    if args.iter().any(|arg| arg == "--install-quiet") {
        std::process::exit(headless_install(install_dir_arg(&args)));
    }
    iced::application(
        |_: &Installer| "PuspaBot Setup".to_string(),
        Installer::update,
        Installer::view,
    )
    .theme(|_: &Installer| {
        iced::Theme::custom(
            "Puspa".to_string(),
            iced::theme::Palette {
                background: theme::CREAM,
                text: theme::CHARCOAL,
                primary: theme::RED,
                success: theme::GREEN,
                danger: theme::RED,
            },
        )
    })
    .font(theme::INTER_FONT)
    .subscription(Installer::subscription)
    .window(iced::window::Settings {
        size: iced::Size::new(1000.0, 640.0),
        icon: iced::window::icon::from_file_data(theme::MASCOT, None).ok(),
        ..iced::window::Settings::default()
    })
    .run_with(Installer::new)
}
