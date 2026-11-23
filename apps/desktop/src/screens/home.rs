//! Home tab: operator status, pending approvals, the autonomy
//! dial, and the recent activity log.
//!
//! Data comes from the local backend only: `GET
//! /api/operator/status`, `GET /api/autonomy-dial`, `GET
//! /api/live-view/{agent_id}/approvals`. Approvals need the logged
//! in user id (the agent id is `puspa-{user_id}`); without a
//! login the approvals section says so instead of guessing.
//! Approve/reject go through the matching POST endpoints.

use iced::alignment::{Horizontal, Vertical};
use iced::widget::{button, column, container, image, row, scrollable, text};
use iced::{Element, Length, Task};
use serde::Deserialize;

use crate::api::{ApiClient, ApiError};
use crate::theme;

/// `GET /api/operator/status` answer (only the fields shown).
#[derive(Debug, Clone, Deserialize)]
pub struct OperatorStatus {
    #[serde(default)]
    mode: String,
    #[serde(default)]
    operator_panels: bool,
    #[serde(default)]
    gateway: Gateway,
    #[serde(default)]
    log: Vec<LogEntry>,
}

#[derive(Debug, Clone, Deserialize, Default)]
struct Gateway {
    #[serde(default)]
    decisions: u64,
    #[serde(default)]
    last_decision: Option<serde_json::Value>,
    #[serde(default)]
    dry_run: bool,
}

#[derive(Debug, Clone, Deserialize)]
// Wire format: mirrors the backend payload; not every field is shown in the v1 UI.
#[allow(dead_code)]
pub struct LogEntry {
    #[serde(rename = "type", default)]
    kind: String,
    #[serde(default)]
    created_at: f64,
    #[serde(default)]
    summary: String,
}

/// `GET /api/autonomy-dial` answer.
#[derive(Debug, Clone, Deserialize)]
pub struct AutonomyDial {
    #[serde(default)]
    areas: Vec<AutonomyArea>,
}

#[derive(Debug, Clone, Deserialize)]
// Wire format: mirrors the backend payload; not every field is shown in the v1 UI.
#[allow(dead_code)]
pub struct AutonomyArea {
    #[serde(default)]
    area: String,
    #[serde(default)]
    label: String,
    #[serde(default)]
    locked: bool,
    #[serde(default)]
    description: String,
    #[serde(default)]
    stage: String,
    #[serde(default)]
    effective_stage: String,
    #[serde(default)]
    ceiling: String,
}

/// One pending approval (`puspa/pwa/approval_overlay_api.py`).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Approval {
    #[serde(default)]
    pub request_id: String,
    #[serde(default)]
    pub task_id: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub tool_name: String,
    #[serde(default)]
    pub button_label: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub created_at: f64,
}

impl Approval {
    fn label(&self) -> String {
        if !self.button_label.is_empty() {
            self.button_label.clone()
        } else if !self.tool_name.is_empty() {
            self.tool_name.clone()
        } else {
            self.kind.clone()
        }
    }
}

/// `GET /api/live-view/{agent_id}/approvals` answer.
#[derive(Debug, Clone, Deserialize)]
// Wire format: mirrors the backend payload; not every field is shown in the v1 UI.
#[allow(dead_code)]
pub struct ApprovalsResponse {
    #[serde(default)]
    agent_id: String,
    #[serde(default)]
    pending: Vec<Approval>,
    #[serde(default)]
    decided: Vec<Approval>,
}

#[derive(Debug, Clone)]
pub enum Message {
    OperatorLoaded(Result<OperatorStatus, ApiError>),
    DialLoaded(Result<AutonomyDial, ApiError>),
    ApprovalsLoaded(Result<ApprovalsResponse, ApiError>),
    Saved(Result<serde_json::Value, ApiError>),
    ApprovePressed(String),
    RejectPressed(String),
    ReloadPressed,
}

pub struct State {
    pub mode: String,
    pub operator_panels: bool,
    pub gateway_decisions: u64,
    pub gateway_dry_run: bool,
    pub last_decision: Option<serde_json::Value>,
    pub log: Vec<LogEntry>,
    pub areas: Vec<AutonomyArea>,
    pub pending: Vec<Approval>,
    pub decided_count: usize,
    pub user_id: Option<String>,
    pub error: Option<String>,
    pub loaded: bool,
}

impl State {
    pub fn new() -> Self {
        Self {
            mode: String::new(),
            operator_panels: false,
            gateway_decisions: 0,
            gateway_dry_run: false,
            last_decision: None,
            log: Vec::new(),
            areas: Vec::new(),
            pending: Vec::new(),
            decided_count: 0,
            user_id: None,
            error: None,
            loaded: false,
        }
    }

    fn agent_id(user_id: &str) -> String {
        format!("puspa-{user_id}")
    }

    /// Load operator status + autonomy dial, and approvals when a
    /// user id is known.
    pub fn load(client: ApiClient, user_id: Option<String>) -> Task<Message> {
        let operator = client.clone();
        let dial = client.clone();
        let approvals = client;
        let mut tasks = vec![
            Task::perform(
                async move {
                    operator
                        .get_json::<OperatorStatus>("/api/operator/status")
                        .await
                },
                Message::OperatorLoaded,
            ),
            Task::perform(
                async move { dial.get_json::<AutonomyDial>("/api/autonomy-dial").await },
                Message::DialLoaded,
            ),
        ];
        if let Some(user_id) = user_id {
            let agent_id = Self::agent_id(&user_id);
            tasks.push(Task::perform(
                async move {
                    approvals
                        .get_json::<ApprovalsResponse>(&format!(
                            "/api/live-view/{agent_id}/approvals"
                        ))
                        .await
                },
                Message::ApprovalsLoaded,
            ));
        }
        Task::batch(tasks)
    }

    fn reload(&self, client: &ApiClient) -> Task<Message> {
        Self::load(client.clone(), self.user_id.clone())
    }

    pub fn update(&mut self, message: Message, client: &ApiClient) -> Task<Message> {
        match message {
            Message::OperatorLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.mode = data.mode;
                        self.operator_panels = data.operator_panels;
                        self.gateway_decisions = data.gateway.decisions;
                        self.gateway_dry_run = data.gateway.dry_run;
                        self.last_decision = data.gateway.last_decision;
                        self.log = data.log;
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat status: {err}")),
                }
                Task::none()
            }
            Message::DialLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.areas = data.areas;
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat dial otonomi: {err}")),
                }
                Task::none()
            }
            Message::ApprovalsLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.pending = data.pending;
                        self.decided_count = data.decided.len();
                        self.error = None;
                    }
                    Err(err) => {
                        self.error = Some(format!("Gagal memuat persetujuan: {err}"));
                    }
                }
                Task::none()
            }
            Message::Saved(result) => match result {
                Ok(_) => {
                    self.error = None;
                    self.reload(client)
                }
                Err(err) => {
                    self.error = Some(format!("Gagal menyimpan keputusan: {err}"));
                    Task::none()
                }
            },
            Message::ApprovePressed(request_id) => {
                let Some(user_id) = self.user_id.clone() else {
                    return Task::none();
                };
                let agent_id = Self::agent_id(&user_id);
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                &format!(
                                    "/api/live-view/{agent_id}/approvals/{request_id}/approve"
                                ),
                                &serde_json::json!({}),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::RejectPressed(request_id) => {
                let Some(user_id) = self.user_id.clone() else {
                    return Task::none();
                };
                let agent_id = Self::agent_id(&user_id);
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                &format!("/api/live-view/{agent_id}/approvals/{request_id}/reject"),
                                &serde_json::json!({}),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::ReloadPressed => self.reload(client),
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let mut content = column![].spacing(16).padding(24).width(Length::Fill);

        content = content.push(
            row![
                column![
                    text("Beranda")
                        .font(theme::font_bold())
                        .size(22)
                        .color(theme::CHARCOAL),
                    text("Ringkasan agen, persetujuan, dan aktivitas terakhir")
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
            content = content.push(loading_card("Memuat beranda…"));
            return container(scrollable(content).width(Length::Fill).height(Length::Fill))
                .style(|_| theme::cream_style())
                .width(Length::Fill)
                .height(Length::Fill)
                .into();
        }

        // Status operator.
        let mode = if self.mode.is_empty() {
            "tidak diketahui".to_string()
        } else {
            self.mode.clone()
        };
        content = content.push(card(
            column![
                section_header("chart", "Status operator"),
                info_row("Mode", &mode,),
                info_row(
                    "Panel operator",
                    if self.operator_panels {
                        "aktif"
                    } else {
                        "tidak aktif"
                    },
                ),
                info_row("Keputusan gateway", self.gateway_decisions.to_string()),
                info_row("Dry run", if self.gateway_dry_run { "ya" } else { "tidak" },),
            ]
            .spacing(12),
        ));

        // Persetujuan menunggu.
        let mut approvals = column![section_header(
            "bell",
            format!("Persetujuan menunggu ({})", self.pending.len()),
        )]
        .spacing(12);
        if self.user_id.is_none() {
            approvals = approvals.push(
                text("Masuk dulu untuk melihat persetujuan yang menunggu keputusan.")
                    .font(theme::font_regular())
                    .size(14)
                    .color(theme::GRAY),
            );
        } else if self.pending.is_empty() {
            approvals = approvals.push(
                container(
                    column![
                        text("Tidak ada persetujuan menunggu.")
                            .font(theme::font_semibold())
                            .size(14)
                            .color(theme::CHARCOAL),
                        text(format!(
                            "{} permintaan sudah diputuskan.",
                            self.decided_count
                        ))
                        .font(theme::font_regular())
                        .size(13)
                        .color(theme::GRAY),
                    ]
                    .spacing(4),
                )
                .padding(12)
                .style(|_| theme::tint_style(theme::GREEN_TINT))
                .width(Length::Fill),
            );
        } else {
            for approval in &self.pending {
                approvals = approvals.push(
                    row![
                        theme::icon("alert", theme::RED, 20.0),
                        text(approval.label())
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::CHARCOAL)
                            .width(Length::Fill),
                        button(text("Setujui").font(theme::font_semibold()).size(13))
                            .on_press(Message::ApprovePressed(approval.request_id.clone()))
                            .style(theme::primary_button)
                            .padding([6, 14]),
                        button(text("Tolak").font(theme::font_semibold()).size(13))
                            .on_press(Message::RejectPressed(approval.request_id.clone()))
                            .style(theme::outline_button)
                            .padding([6, 14]),
                    ]
                    .spacing(8)
                    .align_y(Vertical::Center),
                );
            }
        }
        content = content.push(card(approvals));

        // Dial otonomi (5 area).
        let mut dial = column![section_header("lightning", "Dial otonomi")].spacing(12);
        if self.areas.is_empty() {
            dial = dial.push(
                text("Belum ada area otonomi.")
                    .font(theme::font_regular())
                    .size(14)
                    .color(theme::GRAY),
            );
        }
        for area in &self.areas {
            let stage = if area.effective_stage.is_empty() {
                area.stage.clone()
            } else {
                area.effective_stage.clone()
            };
            let stage_label = if area.locked {
                format!("{stage} · terkunci")
            } else {
                stage
            };
            let stage_bg = if area.locked {
                theme::PEACH_TINT
            } else {
                theme::GREEN_TINT
            };
            dial = dial.push(
                row![
                    theme::icon("lightning", theme::RED, 20.0),
                    column![
                        text(&area.label)
                            .font(theme::font_semibold())
                            .size(14)
                            .color(theme::CHARCOAL),
                        text(&area.description)
                            .font(theme::font_regular())
                            .size(13)
                            .color(theme::GRAY),
                    ]
                    .spacing(2)
                    .width(Length::Fill),
                    chip(&stage_label, stage_bg),
                ]
                .spacing(12)
                .align_y(Vertical::Center),
            );
        }
        content = content.push(card(dial));

        // Aktivitas terakhir.
        let mut activity = column![section_header("calendar", "Aktivitas terakhir")].spacing(12);
        if self.log.is_empty() {
            activity = activity.push(empty_state(
                "Belum ada aktivitas.",
                "Aktivitas agen akan muncul di sini begitu ada yang dikerjakan.",
            ));
        }
        for entry in self.log.iter().rev().take(8) {
            activity = activity.push(
                row![
                    theme::icon("info", theme::GRAY, 18.0),
                    text(&entry.summary)
                        .font(theme::font_regular())
                        .size(13)
                        .color(theme::CHARCOAL)
                        .width(Length::Fill),
                ]
                .spacing(8)
                .align_y(Vertical::Center),
            );
        }
        content = content.push(card(activity));

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

/// Label (gray) on the left, value (semibold) on the right.
fn info_row<'a>(label: &'a str, value: impl Into<String>) -> Element<'a, Message> {
    row![
        text(label)
            .font(theme::font_regular())
            .size(14)
            .color(theme::GRAY)
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
                .width(Length::Fixed(48.0))
                .height(Length::Fixed(48.0)),
            text(title)
                .font(theme::font_semibold())
                .size(14)
                .color(theme::CHARCOAL),
            text(next)
                .font(theme::font_regular())
                .size(13)
                .color(theme::GRAY),
        ]
        .spacing(8)
        .align_x(Horizontal::Center),
    )
    .padding(16)
    .style(|_| theme::tint_style(theme::CREAM))
    .width(Length::Fill)
    .into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn agent_id_prefixes_puspa() {
        assert_eq!(State::agent_id("user-1"), "puspa-user-1");
    }

    #[test]
    fn approvals_response_parses_live_shape() {
        let data: ApprovalsResponse = serde_json::from_str(
            r#"{"agent_id":"puspa-user-1","pending":[{"request_id":"r1","task_id":"t1","kind":"tool","tool_name":"buka","button_label":"Buka halaman","status":"pending","created_at":1795409301.0}],"decided":[]}"#,
        )
        .unwrap();
        assert_eq!(data.pending.len(), 1);
        assert_eq!(data.pending[0].label(), "Buka halaman");
    }

    #[test]
    fn approval_label_falls_back_to_tool_then_kind() {
        let mut approval = Approval {
            request_id: "r".to_string(),
            task_id: String::new(),
            kind: "tool".to_string(),
            tool_name: "klik".to_string(),
            button_label: String::new(),
            status: String::new(),
            created_at: 0.0,
        };
        assert_eq!(approval.label(), "klik");
        approval.tool_name.clear();
        assert_eq!(approval.label(), "tool");
    }

    #[test]
    fn operator_status_parses_live_shape() {
        let data: OperatorStatus = serde_json::from_str(
            r#"{"mode":"local","operator_panels":true,"gateway":{"policy":{"loaded":true},"dry_run":false,"decisions":3,"last_decision":null},"log":[{"type":"user_message","created_at":1795409301.0,"summary":"user_message: Halo"}],"embedding":{"healthy":true}}"#,
        )
        .unwrap();
        assert_eq!(data.mode, "local");
        assert_eq!(data.gateway.decisions, 3);
        assert_eq!(data.log[0].summary, "user_message: Halo");
    }

    #[test]
    fn autonomy_dial_parses_live_shape() {
        let data: AutonomyDial = serde_json::from_str(
            r#"{"areas":[{"area":"money","label":"Money","locked":true,"description":"Payments.","score":0.5,"stage":"propose","effective_stage":"propose","ceiling":"ask_first"}]}"#,
        )
        .unwrap();
        assert_eq!(data.areas.len(), 1);
        assert!(data.areas[0].locked);
    }
}
