//! Tugas tab: shared rooms + their boards, agent tasks, missions.
//!
//! Data comes from the local backend only: `GET /api/rooms` +
//! `GET /api/rooms/{id}/tasks` (boards), `POST /api/tugas`
//! (create an agent task) + `POST /api/tugas/{id}/hentikan`
//! (stop it), `GET /api/missions` + `POST /api/missions/{id}/run`.
//! There is no backend endpoint to check a room-board task off by
//! hand — a board task's status comes from its delegation — so the
//! screen shows statuses honestly and offers no fake check-off.

use std::collections::HashMap;

use iced::widget::{button, column, container, row, scrollable, text, text_input};
use iced::{Element, Length, Task};
use serde::Deserialize;

use crate::api::{ApiClient, ApiError};

#[derive(Debug, Clone, Deserialize)]
// Wire format: mirrors the backend payload; not every field is shown in the v1 UI.
#[allow(dead_code)]
pub struct RoomSummary {
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub role: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RoomsResponse {
    #[serde(default)]
    rooms: Vec<RoomSummary>,
}

#[derive(Debug, Clone, Deserialize)]
// Wire format: mirrors the backend payload; not every field is shown in the v1 UI.
#[allow(dead_code)]
pub struct RoomBoardTask {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub pending_steps: u32,
    #[serde(default)]
    pub delegation: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Deserialize)]
// Wire format: mirrors the backend payload; not every field is shown in the v1 UI.
#[allow(dead_code)]
pub struct RoomBoard {
    #[serde(default)]
    room_id: String,
    #[serde(default)]
    room_name: String,
    #[serde(default)]
    tasks: Vec<RoomBoardTask>,
}

#[derive(Debug, Clone, Deserialize)]
// Wire format: mirrors the backend payload; not every field is shown in the v1 UI.
#[allow(dead_code)]
pub struct MissionStep {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub question: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
// Wire format: mirrors the backend payload; not every field is shown in the v1 UI.
#[allow(dead_code)]
pub struct Mission {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub goal: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub steps: Vec<MissionStep>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct MissionsResponse {
    #[serde(default)]
    missions: Vec<Mission>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CreatedTask {
    #[serde(default)]
    task_id: String,
    #[serde(default)]
    status: String,
    #[serde(default)]
    stage: String,
    #[serde(default)]
    question: String,
}

#[derive(Debug, Clone)]
pub enum Message {
    RoomsLoaded(Result<RoomsResponse, ApiError>),
    MissionsLoaded(Result<MissionsResponse, ApiError>),
    BoardLoaded(Result<RoomBoard, ApiError>),
    RoomSelected(String),
    NewRoomChanged(String),
    CreateRoomPressed,
    RoomCreated(Result<serde_json::Value, ApiError>),
    NewTaskChanged(String),
    CreateTaskPressed,
    TaskCreated(Result<CreatedTask, ApiError>),
    StopTaskPressed(String),
    TaskStopped(Result<serde_json::Value, ApiError>),
    RunMissionPressed(String),
    MissionRan(Result<serde_json::Value, ApiError>),
    ReloadPressed,
}

pub struct State {
    pub rooms: Vec<RoomSummary>,
    pub selected_room: Option<String>,
    pub board: Option<RoomBoard>,
    pub missions: Vec<Mission>,
    pub new_room_name: String,
    pub new_task_purpose: String,
    pub last_task: Option<CreatedTask>,
    pub busy: bool,
    pub error: Option<String>,
    pub loaded: bool,
}

impl State {
    pub fn new() -> Self {
        Self {
            rooms: Vec::new(),
            selected_room: None,
            board: None,
            missions: Vec::new(),
            new_room_name: String::new(),
            new_task_purpose: String::new(),
            last_task: None,
            busy: false,
            error: None,
            loaded: false,
        }
    }

    pub fn load(client: ApiClient) -> Task<Message> {
        let rooms = client.clone();
        let missions = client;
        Task::batch([
            Task::perform(
                async move { rooms.get_json::<RoomsResponse>("/api/rooms").await },
                Message::RoomsLoaded,
            ),
            Task::perform(
                async move { missions.get_json::<MissionsResponse>("/api/missions").await },
                Message::MissionsLoaded,
            ),
        ])
    }

    fn load_board(client: ApiClient, room_id: String) -> Task<Message> {
        Task::perform(
            async move {
                client
                    .get_json::<RoomBoard>(&format!("/api/rooms/{room_id}/tasks"))
                    .await
            },
            Message::BoardLoaded,
        )
    }

    fn err_text(err: ApiError) -> String {
        match err {
            ApiError::NotLoggedIn => {
                "Belum masuk — login dulu dengan Google di layar masuk.".into()
            }
            other => format!("Gagal memuat tugas: {other}"),
        }
    }

    pub fn update(&mut self, message: Message, client: &ApiClient) -> Task<Message> {
        match message {
            Message::RoomsLoaded(result) => {
                match result {
                    Ok(response) => {
                        self.rooms = response.rooms;
                        self.loaded = true;
                        self.error = None;
                        if self.selected_room.is_none() {
                            if let Some(first) = self.rooms.first() {
                                let id = first.id.clone();
                                self.selected_room = Some(id.clone());
                                return Self::load_board(client.clone(), id);
                            }
                        }
                    }
                    Err(err) => self.error = Some(Self::err_text(err)),
                }
                Task::none()
            }
            Message::MissionsLoaded(result) => {
                match result {
                    Ok(response) => self.missions = response.missions,
                    Err(err) => self.error = Some(Self::err_text(err)),
                }
                Task::none()
            }
            Message::BoardLoaded(result) => {
                match result {
                    Ok(board) => self.board = Some(board),
                    Err(err) => self.error = Some(Self::err_text(err)),
                }
                Task::none()
            }
            Message::RoomSelected(id) => {
                self.selected_room = Some(id.clone());
                self.board = None;
                Self::load_board(client.clone(), id)
            }
            Message::NewRoomChanged(name) => {
                self.new_room_name = name;
                Task::none()
            }
            Message::CreateRoomPressed => {
                let name = self.new_room_name.trim().to_string();
                if name.is_empty() || self.busy {
                    return Task::none();
                }
                self.busy = true;
                self.error = None;
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, HashMap<String, String>>(
                                "/api/rooms",
                                &HashMap::from([("name".to_string(), name)]),
                            )
                            .await
                    },
                    Message::RoomCreated,
                )
            }
            Message::RoomCreated(result) => {
                self.busy = false;
                match result {
                    Ok(_) => {
                        self.new_room_name.clear();
                        Self::load(client.clone())
                    }
                    Err(err) => {
                        self.error = Some(format!("Gagal membuat ruang: {err}"));
                        Task::none()
                    }
                }
            }
            Message::NewTaskChanged(purpose) => {
                self.new_task_purpose = purpose;
                Task::none()
            }
            Message::CreateTaskPressed => {
                let purpose = self.new_task_purpose.trim().to_string();
                if purpose.is_empty() || self.busy {
                    return Task::none();
                }
                self.busy = true;
                self.error = None;
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_form::<CreatedTask, HashMap<String, String>>(
                                "/api/tugas",
                                &HashMap::from([("purpose".to_string(), purpose)]),
                            )
                            .await
                    },
                    Message::TaskCreated,
                )
            }
            Message::TaskCreated(result) => {
                self.busy = false;
                match result {
                    Ok(task) => {
                        self.last_task = Some(task);
                        self.new_task_purpose.clear();
                    }
                    Err(err) => self.error = Some(format!("Gagal membuat tugas: {err}")),
                }
                Task::none()
            }
            Message::StopTaskPressed(task_id) => {
                if self.busy {
                    return Task::none();
                }
                self.busy = true;
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, HashMap<String, String>>(
                                &format!("/api/tugas/{task_id}/hentikan"),
                                &HashMap::new(),
                            )
                            .await
                    },
                    Message::TaskStopped,
                )
            }
            Message::TaskStopped(result) => {
                self.busy = false;
                match result {
                    Ok(_) => {
                        self.last_task = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal menghentikan tugas: {err}")),
                }
                Task::none()
            }
            Message::RunMissionPressed(mission_id) => {
                if self.busy {
                    return Task::none();
                }
                self.busy = true;
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, HashMap<String, String>>(
                                &format!("/api/missions/{mission_id}/run"),
                                &HashMap::new(),
                            )
                            .await
                    },
                    Message::MissionRan,
                )
            }
            Message::MissionRan(result) => {
                self.busy = false;
                match result {
                    Ok(_) => Self::load(client.clone()),
                    Err(err) => {
                        self.error = Some(format!("Gagal menjalankan misi: {err}"));
                        Task::none()
                    }
                }
            }
            Message::ReloadPressed => Self::load(client.clone()),
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let mut content = column![text("Tugas").size(24)].spacing(12);

        if let Some(error) = &self.error {
            content = content.push(
                column![
                    text(error).size(14),
                    button(text("Coba lagi")).on_press(Message::ReloadPressed),
                ]
                .spacing(6),
            );
        }

        // Create an agent task (POST /api/tugas runs it now).
        let mut create_button = button(text(if self.busy {
            "Memproses…"
        } else {
            "Buat tugas"
        }));
        if !self.busy && !self.new_task_purpose.trim().is_empty() {
            create_button = create_button.on_press(Message::CreateTaskPressed);
        }
        content = content.push(
            column![
                text("Tugas agen baru").size(16),
                row![
                    text_input("Apa yang harus dikerjakan agen…", &self.new_task_purpose)
                        .on_input(Message::NewTaskChanged)
                        .on_submit(Message::CreateTaskPressed)
                        .width(Length::Fill),
                    create_button,
                ]
                .spacing(8),
            ]
            .spacing(6),
        );

        if let Some(task) = &self.last_task {
            let mut task_row = column![text(format!(
                "Tugas {} — status {} — tahap {}",
                task.task_id, task.status, task.stage
            ))
            .size(14)]
            .spacing(6);
            if !task.question.is_empty() {
                task_row = task_row.push(text(format!("Tanya: {}", task.question)).size(14));
            }
            if task.status != "completed" && task.status != "failed" {
                task_row = task_row.push(
                    button(text("Hentikan"))
                        .on_press(Message::StopTaskPressed(task.task_id.clone())),
                );
            }
            content = content.push(task_row);
        }

        // Missions.
        if !self.missions.is_empty() {
            let mut missions = column![text("Misi").size(16)].spacing(6);
            for mission in &self.missions {
                let mut mission_row =
                    row![text(format!("{} — {}", mission.title, mission.status)).size(14)]
                        .spacing(8);
                if mission.status != "done" && mission.status != "failed" {
                    mission_row = mission_row.push(
                        button(text("Jalankan"))
                            .on_press(Message::RunMissionPressed(mission.id.clone())),
                    );
                }
                missions = missions.push(mission_row);
            }
            content = content.push(missions);
        }

        // Rooms + board.
        if !self.loaded {
            content = content.push(text("Memuat tugas…").size(14));
        } else if self.rooms.is_empty() {
            content = content.push(text("Belum ada ruang. Buat ruang dulu di bawah.").size(14));
        } else {
            let room_row = row(self
                .rooms
                .iter()
                .map(|room| {
                    let selected = self.selected_room.as_deref() == Some(room.id.as_str());
                    let label = if selected {
                        format!("▶ {}", room.name)
                    } else {
                        room.name.clone()
                    };
                    button(text(label))
                        .on_press(Message::RoomSelected(room.id.clone()))
                        .into()
                })
                .collect::<Vec<_>>())
            .spacing(8);
            content = content.push(column![text("Ruang").size(16), room_row].spacing(6));

            match &self.board {
                Some(board) => {
                    let mut board_view =
                        column![text(format!("Papan: {}", board.room_name)).size(16)].spacing(6);
                    if board.tasks.is_empty() {
                        board_view =
                            board_view.push(text("Belum ada tugas di papan ini.").size(14));
                    } else {
                        for task in &board.tasks {
                            let delegation = task
                                .delegation
                                .as_ref()
                                .and_then(|d| d.get("status"))
                                .and_then(|s| s.as_str())
                                .unwrap_or("");
                            let detail = if delegation.is_empty() {
                                format!("{} — {}", task.title, task.status)
                            } else {
                                format!("{} — {} ({})", task.title, task.status, delegation)
                            };
                            board_view = board_view.push(text(detail).size(14));
                        }
                    }
                    content = content.push(board_view);
                }
                None => {
                    content = content.push(text("Memuat papan…").size(14));
                }
            }
        }

        // Create a room.
        let mut room_button = button(text("Buat ruang"));
        if !self.busy && !self.new_room_name.trim().is_empty() {
            room_button = room_button.on_press(Message::CreateRoomPressed);
        }
        content = content.push(
            row![
                text_input("Nama ruang baru…", &self.new_room_name)
                    .on_input(Message::NewRoomChanged)
                    .on_submit(Message::CreateRoomPressed)
                    .width(Length::Fill),
                room_button,
            ]
            .spacing(8),
        );

        container(scrollable(content).width(Length::Fill).height(Length::Fill))
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
    fn create_task_ignored_when_purpose_empty() {
        let mut state = State::new();
        let client = ApiClient::new("http://127.0.0.1:1");
        state.new_task_purpose = "   ".to_string();
        let _ = state.update(Message::CreateTaskPressed, &client);
        assert!(!state.busy);
    }

    #[test]
    fn room_selection_loads_board_state() {
        let mut state = State::new();
        state.rooms = vec![RoomSummary {
            id: "r1".to_string(),
            name: "Umum".to_string(),
            role: "owner".to_string(),
        }];
        let client = ApiClient::new("http://127.0.0.1:1");
        let _ = state.update(Message::RoomSelected("r1".to_string()), &client);
        assert_eq!(state.selected_room.as_deref(), Some("r1"));
        assert!(state.board.is_none());
    }
}
