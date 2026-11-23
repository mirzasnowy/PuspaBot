//! Native screens for the PuspaBot desktop app (v1: 5 core tabs).
//!
//! Every screen talks to the local Python backend through the
//! localhost API only ([`crate::api::ApiClient`]); no screen reads
//! the database directly. A screen only shows what the backend
//! really answers — no mock data, no dead buttons. Where the
//! backend has no endpoint for an action, the action does not
//! exist in the UI.

pub mod chat;
pub mod home;
pub mod memory;
pub mod profile;
pub mod tasks;

/// One short line of section heading, Puspa style (title only,
/// no subtitle — the Muse/Cue list rule). The lifetime is the
/// caller's: a borrowed title keeps the caller's lifetime, an
/// owned `String` becomes `Cow::Owned`, so both work.
pub fn section_title<'a>(
    text: impl iced::widget::text::IntoFragment<'a>,
) -> iced::widget::Text<'a, iced::Theme> {
    iced::widget::text(text).size(16)
}
