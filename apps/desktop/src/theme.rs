//! Puspa design system — the one source of truth for how the
//! desktop app looks (tokens from
//! `puspabot-assets/brand/tokens.json`, never invented here).
//!
//! Every screen builds its view from these helpers so the app
//! looks like one product: the same cards, the same pill buttons,
//! the same outline icons, the same font. No screen invents its
//! own colors, radii, or button shapes. The flat default-iced
//! look the first version shipped with (red rectangles, system
//! font, empty cream) is gone: anything that does not use this
//! module is a bug.
//!
//! Tokens: Puspa red #D7404D, petal pink #F08BA0, peach #F6C89F,
//! cream #FFF5F2, leaf green #4CAF50, charcoal #1C1C1C, gray
//! #8F8A8F, border #F0DDE0; radius 10 / 16 / 24 / pill; font
//! Inter (bundled, OFL) — Geist is web-only, Inter is the
//! desktop stand-in from the same family of grotesques.

use iced::font::{Family, Font, Weight};
use iced::widget::{button, container, svg, text_input};
use iced::{Background, Border, Color, Length, Shadow, Theme, Vector};

// ---------------------------------------------------------------------
// Tokens
// ---------------------------------------------------------------------

/// Const color constructor: `Color::from_rgb8` is not a const fn,
/// so constants go through here.
const fn rgb8(r: u8, g: u8, b: u8) -> Color {
    Color {
        r: r as f32 / 255.0,
        g: g as f32 / 255.0,
        b: b as f32 / 255.0,
        a: 1.0,
    }
}

/// Puspa red — primary actions, active states, brand.
pub const RED: Color = rgb8(0xD7, 0x40, 0x4D);
/// Petal pink — soft accents, hover tints.
pub const PINK: Color = rgb8(0xF0, 0x8B, 0xA0);
/// Peach — warm highlights, secondary accents. Part of the
/// official token set; no v1 screen uses the raw peach yet (the
/// screens use the softer [`PEACH_TINT`]), so the dead-code lint
/// is silenced here rather than deleting a token.
#[allow(dead_code)]
pub const PEACH: Color = rgb8(0xF6, 0xC8, 0x9F);
/// Cream — app background, sidebar, welcome panel.
pub const CREAM: Color = rgb8(0xFF, 0xF5, 0xF2);
/// Leaf green — success, healthy states.
pub const GREEN: Color = rgb8(0x4C, 0xAF, 0x50);
/// Charcoal — primary text.
pub const CHARCOAL: Color = rgb8(0x1C, 0x1C, 0x1C);
/// Gray — secondary text, muted labels.
pub const GRAY: Color = rgb8(0x8F, 0x8A, 0x8F);
/// Border — hairlines, card outlines.
pub const BORDER: Color = rgb8(0xF0, 0xDD, 0xE0);
/// White — cards, right panel, inputs.
pub const WHITE: Color = Color::WHITE;
/// Soft red tint — active tab, selected row, light danger wash.
pub const RED_TINT: Color = rgb8(0xFD, 0xEC, 0xEA);
/// Soft green tint — healthy / ok badges.
pub const GREEN_TINT: Color = rgb8(0xE8, 0xF5, 0xE9);
/// Soft peach tint — warning / waiting badges.
pub const PEACH_TINT: Color = rgb8(0xFD, 0xF3, 0xE7);

/// Card shadow from the token set: soft, warm, low.
fn card_shadow() -> Shadow {
    Shadow {
        color: Color::from_rgba8(80, 35, 45, 0.08),
        offset: Vector::new(0.0, 8.0),
        blur_radius: 24.0,
    }
}

// ---------------------------------------------------------------------
// Font — Inter, bundled (OFL, see assets/Inter-var.ttf)
// ---------------------------------------------------------------------

/// The bundled Inter variable font (all weights in one file).
pub const INTER_FONT: &[u8] = include_bytes!("../assets/Inter-var.ttf");

/// Inter at the given weight. The family name comes from the
/// font's own name table; when the bundled font cannot load, the
/// renderer falls back to the system sans.
pub fn font(weight: Weight) -> Font {
    Font {
        family: Family::Name("Inter"),
        weight,
        ..Font::default()
    }
}

pub fn font_regular() -> Font {
    font(Weight::Normal)
}

pub fn font_medium() -> Font {
    font(Weight::Medium)
}

pub fn font_semibold() -> Font {
    font(Weight::Semibold)
}

pub fn font_bold() -> Font {
    font(Weight::Bold)
}

// ---------------------------------------------------------------------
// Images + icons (official assets, embedded at compile time)
// ---------------------------------------------------------------------

/// The Puspa flower mascot (official app icon, 1024x1024).
pub const MASCOT: &[u8] = include_bytes!("../assets/puspa-icon.png");

/// The Google "G" mark for the sign-in button (official brand
/// asset; its four colors are part of the mark, never recolored).
pub const GOOGLE_G: &[u8] = include_bytes!("../assets/google-g.svg");

/// One outline icon, tinted with `color`. The SVGs in
/// `assets/icons/` use `stroke="currentColor"`, so the style color
/// is the stroke color. `size` is the icon's width in pixels
/// (height follows the 24x24 viewBox).
pub fn icon<'a>(name: &'a str, color: Color, size: f32) -> svg::Svg<'a> {
    let bytes: &'static [u8] = match name {
        "home" => include_bytes!("../assets/icons/navigation/home.svg"),
        "chat" => include_bytes!("../assets/icons/navigation/chat.svg"),
        "tasks" => include_bytes!("../assets/icons/navigation/tasks.svg"),
        "memory" => include_bytes!("../assets/icons/navigation/memory.svg"),
        "profile" => include_bytes!("../assets/icons/navigation/profile.svg"),
        "settings" => include_bytes!("../assets/icons/navigation/settings.svg"),
        "search" => include_bytes!("../assets/icons/navigation/search.svg"),
        "bell" => include_bytes!("../assets/icons/navigation/bell.svg"),
        "plus" => include_bytes!("../assets/icons/navigation/plus.svg"),
        "calendar" => include_bytes!("../assets/icons/navigation/calendar.svg"),
        "lightning" => include_bytes!("../assets/icons/features/lightning.svg"),
        "chart" => include_bytes!("../assets/icons/features/chart.svg"),
        "arrow-right" => include_bytes!("../assets/icons/features/arrow-right.svg"),
        "link" => include_bytes!("../assets/icons/features/link.svg"),
        "image" => include_bytes!("../assets/icons/features/image.svg"),
        "gift" => include_bytes!("../assets/icons/features/gift.svg"),
        "users" => include_bytes!("../assets/icons/features/users.svg"),
        "wallet" => include_bytes!("../assets/icons/features/wallet.svg"),
        "box" => include_bytes!("../assets/icons/features/box.svg"),
        "info" => include_bytes!("../assets/icons/status/info.svg"),
        "check" => include_bytes!("../assets/icons/status/check.svg"),
        "alert" => include_bytes!("../assets/icons/status/alert.svg"),
        "close" => include_bytes!("../assets/icons/status/close.svg"),
        _ => include_bytes!("../assets/icons/status/info.svg"),
    };
    svg::Svg::new(svg::Handle::from_memory(bytes))
        .width(Length::Fixed(size))
        .height(Length::Fixed(size))
        .style(move |_, _| svg::Style { color: Some(color) })
}

// ---------------------------------------------------------------------
// Container styles
// ---------------------------------------------------------------------

/// A white card: rounded 16, hairline border, soft shadow.
/// The standard surface for every content block.
pub fn card_style() -> container::Style {
    container::Style {
        text_color: Some(CHARCOAL),
        background: Some(Background::Color(WHITE)),
        border: Border {
            color: BORDER,
            width: 1.0,
            radius: 16.0.into(),
        },
        shadow: card_shadow(),
    }
}

/// A flat tinted panel (no shadow): section backgrounds, address
/// cards, empty states.
pub fn tint_style(bg: Color) -> container::Style {
    container::Style {
        text_color: Some(CHARCOAL),
        background: Some(Background::Color(bg)),
        border: Border {
            color: BORDER,
            width: 1.0,
            radius: 16.0.into(),
        },
        shadow: Shadow::default(),
    }
}

/// The cream app background (sidebar, welcome left panel).
pub fn cream_style() -> container::Style {
    container::Style {
        background: Some(Background::Color(CREAM)),
        ..Default::default()
    }
}

// ---------------------------------------------------------------------
// Button styles
// ---------------------------------------------------------------------

/// Primary pill button: Puspa red, white text, fully rounded.
/// The one main action per screen (Google sign-in, Send, Save).
pub fn primary_button(_theme: &Theme, _status: button::Status) -> button::Style {
    button::Style {
        background: Some(Background::Color(RED)),
        text_color: WHITE,
        border: Border {
            radius: 999.0.into(),
            ..Default::default()
        },
        shadow: Shadow {
            color: Color::from_rgba8(215, 64, 77, 0.30),
            offset: Vector::new(0.0, 4.0),
            blur_radius: 12.0,
        },
    }
}

/// Outline pill button: white fill, hairline border, charcoal
/// text. Secondary actions ("Pengaturan Lainnya", "Coba lagi").
pub fn outline_button(_theme: &Theme, _status: button::Status) -> button::Style {
    button::Style {
        background: Some(Background::Color(WHITE)),
        text_color: CHARCOAL,
        border: Border {
            color: BORDER,
            width: 1.0,
            radius: 999.0.into(),
        },
        shadow: Shadow::default(),
    }
}

/// Sidebar tab button. Active: white card with red text; inactive:
/// transparent with charcoal text. The active tab is a card, never
/// a flat red rectangle.
pub fn tab_button(active: bool) -> impl Fn(&Theme, button::Status) -> button::Style {
    move |_theme: &Theme, _status: button::Status| {
        if active {
            button::Style {
                background: Some(Background::Color(WHITE)),
                text_color: RED,
                border: Border {
                    color: BORDER,
                    width: 1.0,
                    radius: 12.0.into(),
                },
                shadow: card_shadow(),
            }
        } else {
            button::Style {
                background: None,
                text_color: CHARCOAL,
                border: Border {
                    radius: 12.0.into(),
                    ..Default::default()
                },
                shadow: Shadow::default(),
            }
        }
    }
}

/// A quiet text button: no fill, no border, red text (links,
/// "Lihat semua", dismiss actions).
pub fn text_button(_theme: &Theme, _status: button::Status) -> button::Style {
    button::Style {
        background: None,
        text_color: RED,
        border: Border::default(),
        shadow: Shadow::default(),
    }
}

// ---------------------------------------------------------------------
// Text input style
// ---------------------------------------------------------------------

/// Rounded input: white fill, hairline border, radius 10.
pub fn input_style(_theme: &Theme, _status: text_input::Status) -> text_input::Style {
    text_input::Style {
        background: Background::Color(WHITE),
        border: Border {
            color: BORDER,
            width: 1.0,
            radius: 10.0.into(),
        },
        icon: GRAY,
        placeholder: GRAY,
        value: CHARCOAL,
        selection: RED_TINT,
    }
}
