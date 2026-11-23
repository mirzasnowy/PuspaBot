//! Memory tab: facts the agent remembers, the knowledge graph,
//! and personas.
//!
//! Data comes from the local backend only: `GET /api/memory`
//! (facts + proposals), `GET /api/graph` (nodes + edges), `GET
//! /api/personas`. Edits go through PATCH/DELETE/POST on the same
//! resources. There is no endpoint to create a fact by hand — the
//! PWA adds graph nodes instead — so this screen does not offer
//! one either. The graph is drawn on a real iced canvas (circular
//! layout, click a node to select it); it is a view of the live
//! graph, not a picture.

use std::f32::consts::PI;

use iced::alignment::{Horizontal, Vertical};
use iced::widget::canvas::{self, Frame, Geometry, Path, Program, Stroke, Text};
use iced::widget::{button, column, container, image, row, scrollable, text, text_input, Canvas};
use iced::{Color, Element, Length, Pixels, Point, Rectangle, Task};
use serde::Deserialize;

use crate::api::{ApiClient, ApiError};
use crate::theme;

/// One remembered fact (`puspa/pwa/memory_api.py`).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Fact {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub key: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub source_label: String,
    #[serde(default)]
    pub confidence: f64,
}

/// `GET /api/memory` answer.
#[derive(Debug, Clone, Deserialize)]
pub struct MemoryResponse {
    #[serde(default)]
    facts: Vec<Fact>,
    #[serde(default)]
    proposals: Vec<Fact>,
    #[serde(default)]
    consent: bool,
    #[serde(default)]
    guest_mode: bool,
    #[serde(default)]
    total_facts: usize,
}

/// One knowledge-graph node (`puspa/pwa/graph_api.py`).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct GraphNode {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub description: String,
}

/// One knowledge-graph edge.
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct GraphEdge {
    #[serde(default)]
    pub subject: String,
    #[serde(default)]
    pub relation: String,
    #[serde(default)]
    pub object: String,
    #[serde(default)]
    pub sources: Vec<String>,
}

/// `GET /api/graph` answer.
#[derive(Debug, Clone, Deserialize)]
pub struct GraphResponse {
    #[serde(default)]
    nodes: Vec<GraphNode>,
    #[serde(default)]
    edges: Vec<GraphEdge>,
}

/// One persona (`puspa/pwa/personas_api.py`).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Persona {
    #[serde(default)]
    pub slug: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
}

/// `GET /api/personas` answer.
#[derive(Debug, Clone, Deserialize)]
pub struct PersonasResponse {
    #[serde(default)]
    personas: Vec<Persona>,
    #[serde(default)]
    active: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum View {
    Facts,
    Graph,
    Personas,
}

#[derive(Debug, Clone)]
pub enum Message {
    FactsLoaded(Result<MemoryResponse, ApiError>),
    GraphLoaded(Result<GraphResponse, ApiError>),
    PersonasLoaded(Result<PersonasResponse, ApiError>),
    /// Any write (save / delete / confirm / add / activate) landed.
    Saved(Result<serde_json::Value, ApiError>),
    ViewChanged(View),
    EditStarted(String),
    EditChanged(String),
    EditSaved(String),
    EditCancelled,
    FactDeleted(String),
    ProposalConfirmed(String),
    ProposalRejected(String),
    FactForgotten(String),
    NodeNameChanged(String),
    NodeKindChanged(String),
    NodeDescChanged(String),
    NodeAdded,
    NodeSelected(Option<usize>),
    NodeDeleted(String),
    EdgeSubjectChanged(String),
    EdgeRelationChanged(String),
    EdgeObjectChanged(String),
    EdgeAdded,
    EdgeDeleted(usize),
    PersonaActivated(String),
    ReloadPressed,
}

pub struct State {
    pub facts: Vec<Fact>,
    pub proposals: Vec<Fact>,
    pub consent: bool,
    pub guest_mode: bool,
    pub total_facts: usize,
    pub nodes: Vec<GraphNode>,
    pub edges: Vec<GraphEdge>,
    pub personas: Vec<Persona>,
    pub active_persona: Option<String>,
    pub view: View,
    pub editing: Option<(String, String)>,
    pub new_node_name: String,
    pub new_node_kind: String,
    pub new_node_desc: String,
    pub new_edge_subject: String,
    pub new_edge_relation: String,
    pub new_edge_object: String,
    pub selected_node: Option<usize>,
    pub error: Option<String>,
    pub loaded: bool,
}

impl State {
    pub fn new() -> Self {
        Self {
            facts: Vec::new(),
            proposals: Vec::new(),
            consent: false,
            guest_mode: false,
            total_facts: 0,
            nodes: Vec::new(),
            edges: Vec::new(),
            personas: Vec::new(),
            active_persona: None,
            view: View::Facts,
            editing: None,
            new_node_name: String::new(),
            new_node_kind: String::new(),
            new_node_desc: String::new(),
            new_edge_subject: String::new(),
            new_edge_relation: String::new(),
            new_edge_object: String::new(),
            selected_node: None,
            error: None,
            loaded: false,
        }
    }

    /// Load facts + graph + personas (first load only).
    pub fn load(client: ApiClient) -> Task<Message> {
        let facts = client.clone();
        let graph = client.clone();
        let personas = client;
        Task::batch([
            Task::perform(
                async move { facts.get_json::<MemoryResponse>("/api/memory").await },
                Message::FactsLoaded,
            ),
            Task::perform(
                async move { graph.get_json::<GraphResponse>("/api/graph").await },
                Message::GraphLoaded,
            ),
            Task::perform(
                async move { personas.get_json::<PersonasResponse>("/api/personas").await },
                Message::PersonasLoaded,
            ),
        ])
    }

    fn reload(client: &ApiClient) -> Task<Message> {
        Self::load(client.clone())
    }

    pub fn update(&mut self, message: Message, client: &ApiClient) -> Task<Message> {
        match message {
            Message::FactsLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.facts = data.facts;
                        self.proposals = data.proposals;
                        self.consent = data.consent;
                        self.guest_mode = data.guest_mode;
                        self.total_facts = data.total_facts;
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat fakta: {err}")),
                }
                Task::none()
            }
            Message::GraphLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.nodes = data.nodes;
                        self.edges = data.edges;
                        self.selected_node = None;
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat graf: {err}")),
                }
                Task::none()
            }
            Message::PersonasLoaded(result) => {
                self.loaded = true;
                match result {
                    Ok(data) => {
                        self.personas = data.personas;
                        self.active_persona = data.active;
                        self.error = None;
                    }
                    Err(err) => self.error = Some(format!("Gagal memuat persona: {err}")),
                }
                Task::none()
            }
            Message::Saved(result) => match result {
                Ok(_) => {
                    self.editing = None;
                    self.new_node_name.clear();
                    self.new_node_kind.clear();
                    self.new_node_desc.clear();
                    self.new_edge_subject.clear();
                    self.new_edge_relation.clear();
                    self.new_edge_object.clear();
                    self.error = None;
                    Self::reload(client)
                }
                Err(err) => {
                    self.error = Some(format!("Gagal menyimpan: {err}"));
                    Task::none()
                }
            },
            Message::ViewChanged(view) => {
                self.view = view;
                Task::none()
            }
            Message::EditStarted(id) => {
                let draft = self
                    .facts
                    .iter()
                    .find(|fact| fact.id == id)
                    .map(|fact| fact.content.clone())
                    .unwrap_or_default();
                self.editing = Some((id, draft));
                Task::none()
            }
            Message::EditChanged(draft) => {
                if let Some(editing) = &mut self.editing {
                    editing.1 = draft;
                }
                Task::none()
            }
            Message::EditSaved(id) => {
                let draft = self
                    .editing
                    .as_ref()
                    .map(|(_, draft)| draft.clone())
                    .unwrap_or_default();
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .patch_json::<serde_json::Value, _>(
                                &format!("/api/memory/facts/{id}"),
                                &serde_json::json!({ "content": draft }),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::EditCancelled => {
                self.editing = None;
                Task::none()
            }
            Message::FactDeleted(id) => {
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .delete::<serde_json::Value>(&format!("/api/memory/facts/{id}"))
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::ProposalConfirmed(id) => {
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                &format!("/api/memory/facts/{id}/confirm"),
                                &serde_json::json!({}),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::ProposalRejected(id) => {
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                &format!("/api/memory/facts/{id}/reject"),
                                &serde_json::json!({}),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::FactForgotten(id) => {
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                &format!("/api/memory/facts/{id}/forget"),
                                &serde_json::json!({}),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::NodeNameChanged(name) => {
                self.new_node_name = name;
                Task::none()
            }
            Message::NodeKindChanged(kind) => {
                self.new_node_kind = kind;
                Task::none()
            }
            Message::NodeDescChanged(desc) => {
                self.new_node_desc = desc;
                Task::none()
            }
            Message::NodeAdded => {
                let name = self.new_node_name.trim().to_string();
                if name.is_empty() {
                    return Task::none();
                }
                let kind = self.new_node_kind.trim().to_string();
                let desc = self.new_node_desc.trim().to_string();
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                "/api/graph/nodes",
                                &serde_json::json!({
                                    "name": name,
                                    "kind": kind,
                                    "description": desc,
                                }),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::NodeSelected(index) => {
                self.selected_node = index;
                Task::none()
            }
            Message::NodeDeleted(name) => {
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .delete::<serde_json::Value>(&format!(
                                "/api/graph/nodes/{}",
                                encode_segment(&name)
                            ))
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::EdgeSubjectChanged(value) => {
                self.new_edge_subject = value;
                Task::none()
            }
            Message::EdgeRelationChanged(value) => {
                self.new_edge_relation = value;
                Task::none()
            }
            Message::EdgeObjectChanged(value) => {
                self.new_edge_object = value;
                Task::none()
            }
            Message::EdgeAdded => {
                let subject = self.new_edge_subject.trim().to_string();
                let relation = self.new_edge_relation.trim().to_string();
                let object = self.new_edge_object.trim().to_string();
                if subject.is_empty() || relation.is_empty() || object.is_empty() {
                    return Task::none();
                }
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                "/api/graph/edges",
                                &serde_json::json!({
                                    "subject": subject,
                                    "relation": relation,
                                    "object": object,
                                }),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::EdgeDeleted(index) => {
                let Some(edge) = self.edges.get(index) else {
                    return Task::none();
                };
                let edge = edge.clone();
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .delete::<serde_json::Value>(&format!(
                                "/api/graph/edges?subject={}&relation={}&object={}",
                                encode_segment(&edge.subject),
                                encode_segment(&edge.relation),
                                encode_segment(&edge.object),
                            ))
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::PersonaActivated(slug) => {
                let client = client.clone();
                Task::perform(
                    async move {
                        client
                            .post_json::<serde_json::Value, _>(
                                &format!("/api/personas/{slug}/activate"),
                                &serde_json::json!({}),
                            )
                            .await
                    },
                    Message::Saved,
                )
            }
            Message::ReloadPressed => Self::reload(client),
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let mut content = column![].spacing(16).padding(24).width(Length::Fill);

        content = content.push(
            row![
                column![
                    text("Memori")
                        .font(theme::font_bold())
                        .size(22)
                        .color(theme::CHARCOAL),
                    text("Fakta, graf pengetahuan, dan persona")
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

        content = content.push(
            row![
                tab_button("Fakta", self.view == View::Facts, View::Facts),
                tab_button("Graf", self.view == View::Graph, View::Graph),
                tab_button("Persona", self.view == View::Personas, View::Personas),
            ]
            .spacing(8),
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
            content = content.push(loading_card("Memuat memori…"));
            return container(scrollable(content).width(Length::Fill).height(Length::Fill))
                .style(|_| theme::cream_style())
                .width(Length::Fill)
                .height(Length::Fill)
                .into();
        }

        content = match self.view {
            View::Facts => content.push(self.facts_view()),
            View::Graph => content.push(self.graph_view()),
            View::Personas => content.push(self.personas_view()),
        };

        container(scrollable(content).width(Length::Fill).height(Length::Fill))
            .style(|_| theme::cream_style())
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn facts_view(&self) -> Element<'_, Message> {
        let mut content = column![].spacing(16).width(Length::Fill);

        if !self.proposals.is_empty() {
            let mut proposals = column![section_header("bell", "Usulan baru")].spacing(12);
            for proposal in &self.proposals {
                let label = if proposal.content.is_empty() {
                    proposal.key.clone()
                } else {
                    format!("{}: {}", proposal.key, proposal.content)
                };
                proposals = proposals.push(
                    row![
                        theme::icon("info", theme::RED, 20.0),
                        text(label)
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::CHARCOAL)
                            .width(Length::Fill),
                        button(text("Terima").font(theme::font_semibold()).size(13))
                            .on_press(Message::ProposalConfirmed(proposal.id.clone()))
                            .style(theme::primary_button)
                            .padding([6, 14]),
                        button(text("Tolak").font(theme::font_semibold()).size(13))
                            .on_press(Message::ProposalRejected(proposal.id.clone()))
                            .style(theme::outline_button)
                            .padding([6, 14]),
                    ]
                    .spacing(8)
                    .align_y(Vertical::Center),
                );
            }
            content = content.push(card(proposals));
        }

        let mut facts = column![section_header(
            "memory",
            format!("Fakta tersimpan ({})", self.total_facts),
        )]
        .spacing(12);
        if self.facts.is_empty() {
            facts = facts.push(empty_state(
                "Belum ada fakta tersimpan.",
                "Agen akan mengingat hal penting dari percakapan — mulai mengobrol di Chat.",
            ));
        }
        for fact in &self.facts {
            let editing = self.editing.as_ref().filter(|(id, _)| *id == fact.id);
            let mut fact_card = column![].spacing(8).width(Length::Fill);
            fact_card = fact_card.push(
                row![
                    theme::icon("memory", theme::RED, 20.0),
                    text(&fact.key)
                        .font(theme::font_semibold())
                        .size(14)
                        .color(theme::CHARCOAL)
                        .width(Length::Fill),
                    chip(&fact.status, theme::GREEN_TINT),
                ]
                .spacing(8)
                .align_y(Vertical::Center),
            );
            if let Some((_, draft)) = editing {
                fact_card = fact_card.push(
                    text_input("Isi fakta", draft)
                        .on_input(Message::EditChanged)
                        .font(theme::font_regular())
                        .size(14)
                        .style(theme::input_style)
                        .padding(12)
                        .width(Length::Fill),
                );
                fact_card = fact_card.push(
                    row![
                        button(text("Simpan").font(theme::font_semibold()).size(13))
                            .on_press(Message::EditSaved(fact.id.clone()))
                            .style(theme::primary_button)
                            .padding([6, 14]),
                        button(text("Batal").font(theme::font_semibold()).size(13))
                            .on_press(Message::EditCancelled)
                            .style(theme::outline_button)
                            .padding([6, 14]),
                    ]
                    .spacing(8),
                );
            } else {
                fact_card = fact_card.push(
                    text(&fact.content)
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::CHARCOAL),
                );
                let mut actions = row![
                    button(text("Ubah").font(theme::font_semibold()).size(13))
                        .on_press(Message::EditStarted(fact.id.clone()))
                        .style(theme::outline_button)
                        .padding([6, 14]),
                    button(text("Lupakan").font(theme::font_semibold()).size(13))
                        .on_press(Message::FactForgotten(fact.id.clone()))
                        .style(theme::text_button)
                        .padding([6, 8]),
                    button(text("Hapus").font(theme::font_semibold()).size(13))
                        .on_press(Message::FactDeleted(fact.id.clone()))
                        .style(theme::text_button)
                        .padding([6, 8]),
                ]
                .spacing(8);
                if !fact.source_label.is_empty() {
                    actions = actions.push(
                        text(&fact.source_label)
                            .font(theme::font_regular())
                            .size(12)
                            .color(theme::GRAY),
                    );
                }
                fact_card = fact_card.push(actions);
            }
            facts = facts.push(card(fact_card));
        }
        content = content.push(facts);

        let consent = if self.consent { "ya" } else { "tidak" };
        let guest = if self.guest_mode { " (mode tamu)" } else { "" };
        content = content.push(
            text(format!("Izin memori: {consent}{guest}"))
                .font(theme::font_regular())
                .size(12)
                .color(theme::GRAY),
        );
        content.into()
    }

    fn graph_view(&self) -> Element<'_, Message> {
        let mut content = column![].spacing(16).width(Length::Fill);

        let mut graph = column![section_header(
            "memory",
            format!(
                "Graf pengetahuan ({} simpul, {} hubungan)",
                self.nodes.len(),
                self.edges.len()
            ),
        )]
        .spacing(12);
        if self.nodes.is_empty() {
            graph = graph.push(empty_state(
                "Graf masih kosong.",
                "Tambah simpul di bawah untuk mulai — simpul pertama bisa berupa namamu.",
            ));
        } else {
            let program = GraphProgram {
                nodes: self.nodes.clone(),
                edges: self.edges.clone(),
                selected: self.selected_node,
            };
            graph = graph.push(
                container(
                    Canvas::new(program)
                        .width(Length::Fill)
                        .height(Length::Fixed(320.0)),
                )
                .padding(8)
                .style(|_| theme::tint_style(theme::CREAM))
                .width(Length::Fill),
            );
        }
        content = content.push(card(graph));

        if let Some(index) = self.selected_node {
            if let Some(node) = self.nodes.get(index) {
                content = content.push(card(
                    column![
                        row![
                            theme::icon("memory", theme::RED, 20.0),
                            text(&node.name)
                                .font(theme::font_semibold())
                                .size(15)
                                .color(theme::CHARCOAL)
                                .width(Length::Fill),
                            chip(&node.kind, theme::PEACH_TINT),
                        ]
                        .spacing(8)
                        .align_y(Vertical::Center),
                        text(&node.description)
                            .font(theme::font_regular())
                            .size(14)
                            .color(theme::CHARCOAL),
                        button(text("Hapus simpul").font(theme::font_semibold()).size(13))
                            .on_press(Message::NodeDeleted(node.name.clone()))
                            .style(theme::outline_button)
                            .padding([6, 14]),
                    ]
                    .spacing(8),
                ));
            }
        }

        content = content.push(card(
            column![
                section_header("plus", "Tambah simpul"),
                text_input("Nama simpul", &self.new_node_name)
                    .on_input(Message::NodeNameChanged)
                    .font(theme::font_regular())
                    .size(14)
                    .style(theme::input_style)
                    .padding(12)
                    .width(Length::Fill),
                text_input("Jenis (mis. orang, tempat, topik)", &self.new_node_kind)
                    .on_input(Message::NodeKindChanged)
                    .font(theme::font_regular())
                    .size(14)
                    .style(theme::input_style)
                    .padding(12)
                    .width(Length::Fill),
                text_input("Deskripsi", &self.new_node_desc)
                    .on_input(Message::NodeDescChanged)
                    .font(theme::font_regular())
                    .size(14)
                    .style(theme::input_style)
                    .padding(12)
                    .width(Length::Fill),
                button(text("Tambah simpul").font(theme::font_semibold()).size(14))
                    .on_press(Message::NodeAdded)
                    .style(theme::primary_button)
                    .padding([10, 20]),
            ]
            .spacing(8),
        ));

        content = content.push(card(
            column![
                section_header("link", "Tambah hubungan"),
                text_input("Dari", &self.new_edge_subject)
                    .on_input(Message::EdgeSubjectChanged)
                    .font(theme::font_regular())
                    .size(14)
                    .style(theme::input_style)
                    .padding(12)
                    .width(Length::Fill),
                text_input("Hubungan (mis. suka, bekerja di)", &self.new_edge_relation)
                    .on_input(Message::EdgeRelationChanged)
                    .font(theme::font_regular())
                    .size(14)
                    .style(theme::input_style)
                    .padding(12)
                    .width(Length::Fill),
                text_input("Ke", &self.new_edge_object)
                    .on_input(Message::EdgeObjectChanged)
                    .font(theme::font_regular())
                    .size(14)
                    .style(theme::input_style)
                    .padding(12)
                    .width(Length::Fill),
                button(
                    text("Tambah hubungan")
                        .font(theme::font_semibold())
                        .size(14)
                )
                .on_press(Message::EdgeAdded)
                .style(theme::primary_button)
                .padding([10, 20]),
            ]
            .spacing(8),
        ));

        if !self.edges.is_empty() {
            let mut edges = column![section_header("link", "Hubungan")].spacing(12);
            for (index, edge) in self.edges.iter().enumerate() {
                edges = edges.push(
                    row![
                        text(format!(
                            "{} — {} → {}",
                            edge.subject, edge.relation, edge.object
                        ))
                        .font(theme::font_regular())
                        .size(14)
                        .color(theme::CHARCOAL)
                        .width(Length::Fill),
                        button(text("Hapus").font(theme::font_semibold()).size(13))
                            .on_press(Message::EdgeDeleted(index))
                            .style(theme::text_button)
                            .padding([6, 8]),
                    ]
                    .spacing(8)
                    .align_y(Vertical::Center),
                );
            }
            content = content.push(card(edges));
        }
        content.into()
    }

    fn personas_view(&self) -> Element<'_, Message> {
        let mut content = column![section_header("profile", "Persona")]
            .spacing(16)
            .width(Length::Fill);
        if self.personas.is_empty() {
            content = content.push(empty_state(
                "Belum ada persona.",
                "Persona akan muncul di sini setelah dibuat — sementara Puspa memakai persona bawaan.",
            ));
        }
        for persona in &self.personas {
            let name = if persona.name.is_empty() {
                persona.slug.clone()
            } else {
                persona.name.clone()
            };
            let mut persona_card = column![row![
                theme::icon("profile", theme::RED, 20.0),
                text(name)
                    .font(theme::font_semibold())
                    .size(14)
                    .color(theme::CHARCOAL)
                    .width(Length::Fill),
                if Some(&persona.slug) == self.active_persona.as_ref() {
                    chip("Aktif", theme::GREEN_TINT)
                } else {
                    button(text("Aktifkan").font(theme::font_semibold()).size(13))
                        .on_press(Message::PersonaActivated(persona.slug.clone()))
                        .style(theme::outline_button)
                        .padding([6, 14])
                        .into()
                },
            ]
            .spacing(8)
            .align_y(Vertical::Center),]
            .spacing(8);
            if !persona.description.is_empty() {
                persona_card = persona_card.push(
                    text(&persona.description)
                        .font(theme::font_regular())
                        .size(13)
                        .color(theme::GRAY),
                );
            }
            content = content.push(card(persona_card));
        }
        content.into()
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

/// Pill tab: white card when active, plain text button when not.
fn tab_button<'a>(label: impl Into<String>, active: bool, view: View) -> Element<'a, Message> {
    button(text(label.into()).font(theme::font_semibold()).size(14))
        .on_press(Message::ViewChanged(view))
        .style(theme::tab_button(active))
        .padding([8, 16])
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
                .width(Length::Fixed(56.0))
                .height(Length::Fixed(56.0)),
            text(title)
                .font(theme::font_semibold())
                .size(16)
                .color(theme::CHARCOAL),
            text(next)
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

/// Percent-encode one URL path segment / query value (keeps the
/// unreserved set, encodes the rest — enough for names with
/// spaces and other punctuation).
fn encode_segment(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char);
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// Circular layout: nodes on a ring, centered in the canvas.
fn node_positions(count: usize, width: f32, height: f32) -> Vec<Point> {
    if count == 0 {
        return Vec::new();
    }
    let center_x = width / 2.0;
    let center_y = height / 2.0;
    let radius = (width.min(height) / 2.0 - 36.0).max(24.0);
    (0..count)
        .map(|index| {
            let angle = 2.0 * PI * index as f32 / count as f32 - PI / 2.0;
            Point::new(
                center_x + radius * angle.cos(),
                center_y + radius * angle.sin(),
            )
        })
        .collect()
}

fn node_color(kind: &str) -> Color {
    let kind = kind.to_lowercase();
    if kind.contains("person") || kind.contains("orang") {
        theme::RED
    } else if kind.contains("place") || kind.contains("tempat") {
        theme::GREEN
    } else if kind.contains("topic") || kind.contains("topik") {
        theme::PINK
    } else {
        theme::GRAY
    }
}

/// The live knowledge graph as an iced canvas program: edges as
/// lines, nodes as colored dots with labels, click a node to
/// select it (the selected node gets a red ring and its detail
/// shows below the canvas).
struct GraphProgram {
    nodes: Vec<GraphNode>,
    edges: Vec<GraphEdge>,
    selected: Option<usize>,
}

impl Program<Message> for GraphProgram {
    type State = ();

    fn update(
        &self,
        _state: &mut Self::State,
        event: canvas::Event,
        bounds: Rectangle,
        cursor: iced::mouse::Cursor,
    ) -> (iced::event::Status, Option<Message>) {
        if let canvas::Event::Mouse(iced::mouse::Event::ButtonPressed(iced::mouse::Button::Left)) =
            event
        {
            if let Some(position) = cursor.position_in(bounds) {
                let positions = node_positions(self.nodes.len(), bounds.width, bounds.height);
                for (index, point) in positions.iter().enumerate() {
                    if point.distance(position) < 20.0 {
                        return (
                            iced::event::Status::Captured,
                            Some(Message::NodeSelected(Some(index))),
                        );
                    }
                }
                return (
                    iced::event::Status::Captured,
                    Some(Message::NodeSelected(None)),
                );
            }
        }
        (iced::event::Status::Ignored, None)
    }

    fn draw(
        &self,
        _state: &Self::State,
        renderer: &iced::Renderer,
        _theme: &iced::Theme,
        bounds: Rectangle,
        _cursor: iced::mouse::Cursor,
    ) -> Vec<Geometry> {
        let mut frame = Frame::new(renderer, bounds.size());
        let positions = node_positions(self.nodes.len(), frame.width(), frame.height());
        for edge in &self.edges {
            let from = self.nodes.iter().position(|node| node.name == edge.subject);
            let to = self.nodes.iter().position(|node| node.name == edge.object);
            if let (Some(a), Some(b)) = (from, to) {
                frame.stroke(
                    &Path::line(positions[a], positions[b]),
                    Stroke::default().with_color(theme::GRAY).with_width(1.0),
                );
            }
        }
        for (index, node) in self.nodes.iter().enumerate() {
            let point = positions[index];
            frame.fill(&Path::circle(point, 14.0), node_color(&node.kind));
            if Some(index) == self.selected {
                frame.stroke(
                    &Path::circle(point, 19.0),
                    Stroke::default().with_color(theme::RED).with_width(2.0),
                );
            }
            frame.fill_text(Text {
                content: node.name.clone(),
                position: Point::new(point.x, point.y + 22.0),
                color: theme::CHARCOAL,
                size: Pixels(12.0),
                horizontal_alignment: Horizontal::Center,
                vertical_alignment: Vertical::Top,
                ..Text::default()
            });
        }
        vec![frame.into_geometry()]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encode_segment_keeps_plain_names() {
        assert_eq!(encode_segment("Jakarta"), "Jakarta");
        assert_eq!(encode_segment("Kota Bandung"), "Kota%20Bandung");
        assert_eq!(encode_segment("a/b?c"), "a%2Fb%3Fc");
    }

    #[test]
    fn node_positions_stay_inside_canvas() {
        let positions = node_positions(5, 400.0, 300.0);
        assert_eq!(positions.len(), 5);
        for point in positions {
            assert!(point.x >= 0.0 && point.x <= 400.0);
            assert!(point.y >= 0.0 && point.y <= 300.0);
        }
    }

    #[test]
    fn node_positions_empty_for_zero_nodes() {
        assert!(node_positions(0, 400.0, 300.0).is_empty());
    }

    #[test]
    fn node_color_by_kind() {
        assert_eq!(node_color("person"), theme::RED);
        assert_eq!(node_color("tempat"), theme::GREEN);
        assert_eq!(node_color("topik"), theme::PINK);
        assert_eq!(node_color("other"), theme::GRAY);
    }

    #[test]
    fn memory_response_parses_live_shape() {
        let data: MemoryResponse = serde_json::from_str(
            r#"{"facts":[{"id":"f1","key":"kota","content":"Jakarta","status":"confirmed","source":"chat","source_label":"Percakapan","confidence":0.9}],"proposals":[],"consent":true,"guest_mode":false,"total_facts":1}"#,
        )
        .unwrap();
        assert_eq!(data.facts.len(), 1);
        assert_eq!(data.facts[0].content, "Jakarta");
        assert_eq!(data.total_facts, 1);
        assert!(data.consent);
    }

    #[test]
    fn graph_response_parses_live_shape() {
        let data: GraphResponse = serde_json::from_str(
            r#"{"nodes":[{"name":"Nero","kind":"person","description":"Pengguna"}],"edges":[{"subject":"Nero","relation":"tinggal di","object":"Jakarta","sources":[]}]}"#,
        )
        .unwrap();
        assert_eq!(data.nodes.len(), 1);
        assert_eq!(data.edges[0].relation, "tinggal di");
    }

    #[test]
    fn personas_response_parses_live_shape() {
        let data: PersonasResponse = serde_json::from_str(
            r#"{"personas":[{"slug":"default","name":"Puspa","description":""}],"active":"default"}"#,
        )
        .unwrap();
        assert_eq!(data.personas.len(), 1);
        assert_eq!(data.active.as_deref(), Some("default"));
    }
}
