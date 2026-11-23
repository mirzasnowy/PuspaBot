//! Home tab: operator status, pending approvals, the autonomy
//! dial, and the recent activity log.
//!
//! Data comes from the local backend only: `GET
//! /api/operator/status`, `GET /api/autonomy-dial`, `GET
//! /api/live-view/{agent_id}/approvals`. Approvals need the logged
//! in user id (the agent id is `puspa-{user_id}`); without a
//! login the approvals section says so instead of guessing.
//! Approve/reject go through the matching POST endpoints.

use iced::widget::{button, column, row, scrollable, text};
use iced::{Color, Element, Length, Task};
use serde::Deserialize;

use super::section_title;
use crate::api::{ApiClient, ApiError};

const RED: Color = Color {
    r: 0xD7 as f32 / 255.0,
    g: 0x40 as f32 / 255.0,
    b: 0x4D as f32 / 255.0,
    a: 1.0,
};
const GRAY: Color = Color {
    r: 0x8F as f32 / 255.0,
    g: 0x8A as f32 / 255.0,
    b: 0x8F as f32 / 255.0,
    a: 1.0,
};

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
        let mut content = column![].spacing(12).padding(16).width(Length::Fill);

        content = content.push(
            row![
                section_title("Beranda"),
                button("Muat ulang").on_press(Message::ReloadPressed),
            ]
            .spacing(8),
        );

        if let Some(error) = &self.error {
            content = content.push(text(error).size(13).color(RED));
        }
        if !self.loaded {
            return content.push(text("Memuat beranda...").size(14)).into();
        }

        content = content.push(section_title("Status operator"));
        let mode = if self.mode.is_empty() {
            "tidak diketahui".to_string()
        } else {
            self.mode.clone()
        };
        content = content.push(text(format!("Mode: {mode}")).size(14));
        content = content.push(
            text(format!(
                "Panel operator: {}",
                if self.operator_panels {
                    "aktif"
                } else {
                    "tidak aktif"
                }
            ))
            .size(14),
        );
        content = content.push(
            text(format!(
                "Keputusan gateway: {} (dry run: {})",
                self.gateway_decisions,
                if self.gateway_dry_run { "ya" } else { "tidak" }
            ))
            .size(14),
        );

        content = content.push(section_title(format!(
            "Persetujuan menunggu ({})",
            self.pending.len()
        )));
        if self.user_id.is_none() {
            content = content.push(
                text("Masuk dulu untuk melihat persetujuan yang menunggu keputusan.").size(14),
            );
        } else if self.pending.is_empty() {
            content = content.push(
                text(format!(
                    "Tidak ada persetujuan menunggu. {} permintaan sudah diputuskan.",
                    self.decided_count
                ))
                .size(14),
            );
        } else {
            for approval in &self.pending {
                content = content.push(
                    row![
                        text(approval.label()).size(14).width(Length::Fill),
                        button("Setujui")
                            .on_press(Message::ApprovePressed(approval.request_id.clone())),
                        button("Tolak")
                            .on_press(Message::RejectPressed(approval.request_id.clone())),
                    ]
                    .spacing(8),
                );
            }
        }

        content = content.push(section_title("Dial otonomi"));
        if self.areas.is_empty() {
            content = content.push(text("Belum ada area otonomi.").size(14));
        }
        for area in &self.areas {
            let stage = if area.effective_stage.is_empty() {
                area.stage.clone()
            } else {
                area.effective_stage.clone()
            };
            let locked = if area.locked { " (terkunci)" } else { "" };
            content = content.push(
                column![
                    text(format!("{} — {}{}", area.label, stage, locked)).size(14),
                    text(&area.description).size(13).color(GRAY),
                ]
                .spacing(2),
            );
        }

        content = content.push(section_title("Aktivitas terakhir"));
        if self.log.is_empty() {
            content = content.push(text("Belum ada aktivitas.").size(14));
        }
        for entry in self.log.iter().rev().take(8) {
            content = content.push(text(&entry.summary).size(13));
        }

        scrollable(content).into()
    }
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
