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
