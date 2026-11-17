# Inventaris nanobot (chassis Puspa) — Fase 0

**Tanggal inventaris:** 17 November 2026
**Sumber:** clone lokal `~/workspace/goals/hard-ai-engineering-portfolio-project/build/fase-0/nanobot`
**Repo asal:** https://github.com/HKUDS/nanobot

## 1. Verifikasi clone

| Item | Hasil |
|---|---|
| Clone | Berhasil, branch `main`, remote `origin` = https://github.com/HKUDS/nanobot.git |
| HEAD | `e76717445058f4670ac9299cc89f91504da2f5f2` — "fix(webui): open ready apps by default (#6685)" |
| Tanggal HEAD | 2026-11-17 11:21:26 +0800 (commit terbaru saat clone, 17 Nov 2026) |
| Lisensi | **MIT terverifikasi** — file `LICENSE`, "Copyright (c) 2025-present Xubin Ren and the nanobot contributors". `pyproject.toml` juga menyatakan `license = MIT`. |
| Bahasa utama | Python (1.071 file `.py`; ~193.000 baris di paket `nanobot/`, ~144.000 baris di luar folder `tests/`) |
| Ukuran repo | 124 MB di disk (termasuk `.git`) |
| Versi paket | `nanobot-ai` 0.4.1 |
| Status kerja | Repo hasil clone **tidak diubah** (git status bersih). Tidak ada push ke GitHub. Tidak ada dependensi diinstal, nanobot tidak dijalankan. |

**Koreksi terhadap survei:** survei repo menyebut core nanobot "sekitar 12 ribu baris yang bisa dibaca habis". Angka itu tidak cocok dengan hasil clone: paket `nanobot/` saja ~144 ribu baris Python di luar tes (468 file), dan ~193 ribu baris termasuk tes kanal. Chassis ini jauh lebih besar dari perkiraan survei. Tetap Python dan tetap satu paket yang rapi, tapi rencana baca habis kode perlu disesuaikan (baca per modul, bukan sekaligus).

## 2. Struktur direktori top-level

| Path | Isi |
|---|---|
| `nanobot/` | Kode aplikasi Python (satu paket, lihat bagian 3) |
| `packages/client-events/` | Paket pendamping (event client) |
| `webui/` | WebUI: aplikasi React + Vite + Tailwind (TypeScript). Build-nya (`nanobot/web/dist/`) dihasilkan lewat `bun run build` dan tidak ikut ter-commit |
| `tui/` | TUI desktop native (TypeScript/Bun) |
| `tests/` | Tes pytest tingkat repo (gate coverage 75%) |
| `docs/` | Dokumentasi lengkap: `architecture.md`, `memory.md`, `automations.md`, `mcp-presets.md`, `cli-reference.md`, `configuration.md`, `deployment.md`, dsb. |
| `scripts/` | Utilitas rilis/CI: `install.sh`, `ci_gate.py`, `verify_release_approval.py`, `install_channel_dependencies.py`, dsb. |
| `Dockerfile`, `docker-compose.yml`, `docker-compose.bwrap.yml` | Image + compose gateway (port 18790 WebUI, 8765); varian bwrap untuk sandbox bubblewrap |
| `entrypoint.sh` | Entrypoint container: turunkan privilege root ke user `nanobot` (fail-closed kalau gagal), siapkan `~/.nanobot/config.json` |
| `pyproject.toml` | Metadata paket, dependensi, entry point console script, konfigurasi ruff/pyright/pytest |
| `render.yaml`, `render-config.json` | Deploy satu klik ke Render |
| `README.md` (91 KB) | Dokumen utama, sangat panjang |
| `SECURITY.md`, `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`, `AGENTS.md` | Kebijakan keamanan, atribusi pihak ketiga, kontribusi |
| `conftest.py`, `hatch_build.py` | Konfigurasi pytest + hook build hatch |

### Struktur paket `nanobot/` (modul yang penting)

| Modul | Peran |
|---|---|
| `nanobot/cli/` | Entry point CLI (`entry.py`, `commands.py`, `gateway.py`, `webui.py`, `agent.py`, `onboard.py`, `update.py`, dsb.) |
| `nanobot/agent/` | Inti agen: `loop.py` (orkestrasi turn), `runner.py` (loop model/tool), `context.py`, `memory.py`, `skills.py`, `subagent.py`, `execution_policy.py`, `goal_permission.py`, `hooks/`, `tools/` |
| `nanobot/agent/tools/` | Semua tool bawaan: `filesystem.py`, `shell.py`, `web.py`, `search.py`, `cron.py`, `mcp.py`, `sessions.py`, `subagent.py`, `message.py`, `apply_patch.py`, `rg.py`, `image_generation.py`, `self.py`, `cli_apps.py`, `registry.py`, `loader.py`, `base.py`, `schema.py` |
| `nanobot/bus/` | MessageBus: `events.py`, `queue.py`, `delivery.py`, `outbound_events.py` — semua pesan masuk/keluar lewat sini |
| `nanobot/session/` | Penyimpanan sesi: `manager.py`, `sqlite_store.py` (SQLite), `history.py`, `summary.py`, `history_graph.py`, `recovery.py`, `state.py` |
| `nanobot/channels/` | 19 kanal (lihat bagian 7). Tiap kanal: `manifest.py`, `runtime.py`, `validation.py`, `webui/` |
| `nanobot/cron/` | Penjadwal: `types.py`, `service.py`, `binding.py`, `bound_runner.py`, `session_delivery.py` |
| `nanobot/triggers/` | Trigger lokal (`local_store.py`, `local_runner.py`) |
| `nanobot/config/` | Konfigurasi: `schema.py` (pydantic), `loader.py`, `paths.py`, `home.py`, `network.py`, `watcher.py` |
| `nanobot/gateway/` | Gateway daemon: `runtime.py`, `service.py` |
| `nanobot/providers/` | Provider LLM: OpenAI-compatible, Anthropic, Azure, Bedrock, GitHub Copilot, Codex, xAI, fallback, routing |
| `nanobot/security/` | `workspace_policy.py`, `workspace_access.py`, `sandbox_launcher.py`, `network.py` (anti-SSRF, pinned DNS) |
| `nanobot/execution/` | Eksekusi shell/sandbox: `sandbox.py` (backend bwrap + seatbelt), `processes.py`, `http.py` |
| `nanobot/skills/` | Skill bawaan (lihat bagian 4) |
| `nanobot/templates/` | Template prompt + memori: `agent/` (`dream.md`, `subagent_system.md`, `tool_contract.md`, `skills_section.md`, dsb.), `memory/MEMORY.md`, `prompts/` |
| `nanobot/webui/` | Preset MCP (`mcp_presets.json`) + aset WebUI yang ikut paket |
| `nanobot/sdk/` | Python SDK untuk integrasi dari kode |
| `nanobot/llm_usage/`, `nanobot/goal/`, `nanobot/pairing/` | Pelacakan pemakaian LLM, goal, pairing/approval pengirim DM |
| `nanobot/apps/cli/` | Aplikasi CLI (manajer `CliAppManager`, alias skill) |
| `nanobot/command/` | Perintah slash (`/dream`, `/compact`, `/sessions`, `/diff`, dsb.) |

## 3. Entry point — bagaimana nanobot dijalankan

Ada tiga mode jalan, semuanya dari satu console script:

- **`nanobot` → `nanobot.cli.entry:main`** (terdaftar di `[project.scripts]` di `pyproject.toml`). `entry.py` meneruskan ke subcommand.
- **`nanobot webui`** — mode utama untuk pengguna: menjalankan/menggabungkan gateway lokal bersama lalu membuka WebUI di browser (localhost, login lewat link berisi token setelah `#`). WebUI dilayani dari bundle `nanobot/web/dist/`.
- **`nanobot agent`** — TUI terminal (atau `--classic` / `--no-tui` untuk mode klasik, `-m/--message` untuk sekali jalan). TUI memakai direktori launch sebagai workspace proyek dan berbagi sesi + gateway dengan WebUI. Perintah slash `/`, mention `@`, tempel gambar, `$` untuk referensi skill.
- **`nanobot gateway`** — daemon gateway (dipakai Docker: `command: ["gateway"]`, `restart: unless-stopped`). Gateway adalah proses terkelola (`gateway/runtime.py`, `gateway/service.py`) dengan identitas health (instance_id, run_id, version) dan file lock. Docker compose memetakan `127.0.0.1:18790:18790` (WebUI) dan `8765:8765`.
- **`nanobot-desktop-tui` → `nanobot.cli.desktop_tui:main`** — entry point kedua untuk TUI desktop.
- **Deploy cloud:** Render satu klik (`render.yaml`) atau Docker (`Dockerfile` + compose). Data hidup di `~/.nanobot` (config `config.json`, workspace default `~/.nanobot/workspace`).

Alur runtime (dari `docs/architecture.md`): **Channel → MessageBus (InboundMessage) → AgentLoop (session, workspace, context) → AgentRunner (provider/tool loop) → Provider → Tools → kembali ke AgentLoop → MessageBus (OutboundMessage) → Channel.** `AgentLoop` milik turn yang menghadap kanal; `AgentRunner` milik loop model/tool. MCP dikelola di luar loop (composition root membuat `MCPProvider`, loop hanya memakai `ToolRegistry` yang dibagikan).

Konfigurasi: satu file `~/.nanobot/config.json`, dimuat sebagai model pydantic (`nanobot/config/schema.py`, `Config`), dengan `pydantic-settings`. Ada watcher config (`config/watcher.py`).

## 4. Sistem skill/tool

### Format skill
Skill adalah folder berisi `SKILL.md` dengan frontmatter YAML, format standar agentskills (kompatibel OpenClaw/ClawHub):

```yaml
---
name: weather
description: Get current weather and forecasts.
homepage: https://wttr.in/:help
metadata: {"nanobot":{"emoji":"🌤️","requires":{"bins":["curl"]}}}
---
```

Isi SKILL.md adalah instruksi Markdown untuk agen (kapan dipakai, langkah, contoh perintah). Progressive disclosure: deskripsi di frontmatter masuk konteks, isi penuh dibaca saat skill dipakai.

### Skill bawaan (`nanobot/skills/`)
`weather`, `cron`, `memory`, `github`, `image-generation`, `summarize`, `tmux`, `skill-creator`, `nanobot-document`, `clawhub` (cari + instal skill dari registry publik ClawHub), `my` (introspeksi runtime: cek kapasitas konteks, ganti model, catat scratchpad).

### Cara skill didaftarkan
- **Loader:** `nanobot/agent/skills.py` (`SkillsLoader`) — memindai `nanobot/skills/` (bawaan) dan `<workspace>/skills/` (milik user). Alias dari `CliAppManager` (aplikasi CLI terinstal) juga dihitung sebagai skill.
- **Metadata:** `nanobot/agent/skill_metadata.py` — parse + validasi frontmatter (`parse_skill_metadata`, `valid_skill_metadata`).
- **Tidak ada tanda tangan / verifikasi.** Skill dari workspace user dan dari ClawHub dimuat apa adanya; tidak ada pemeriksaan signature, tidak ada pemindai isi. Ini titik yang harus dikeraskan Puspa (definisi 2.6: skill bertanda tangan).
- Skill juga bisa dibuat otomatis oleh Dream: `templates/agent/dream.md` memerintahkan pembuatan `skills/<nama>/SKILL.md` kalau sebuah workflow sudah muncul minimal 2 kali.

### Cara tool dipanggil
- **Tool = kelas Python** di `nanobot/agent/tools/`, memakai decorator `@tool_parameters` dengan skema JSON (`nanobot/agent/tools/schema.py`: `StringSchema`, `NumberSchema`, `BooleanSchema`, `tool_parameters_schema`). Contoh: `SubagentTool`, tool `cron`, `message`, `my`.
- **Registry:** `nanobot/agent/tools/registry.py` (`ToolRegistry`) — `register()`, `unregister()`, `get()`, definisi tool di-cache untuk dikirim ke provider sebagai function-calling schema.
- **Loader:** `nanobot/agent/tools/loader.py` (`ToolLoader`) — merakit registry dari config.
- **Eksekusi:** `AgentRunner` (`agent/runner.py`) menjalankan loop: kirim pesan ke provider → terima tool call → eksekusi tool → kembalikan hasil ke model → ulangi sampai jawaban akhir atau batas iterasi. Konteks per-request dibawa lewat `ContextVar` (`agent/tools/context.py`: `RequestContext`, `ToolContext`).
- **Tool bawaan:** filesystem (baca/tulis/patch), shell (dengan sesi exec), web fetch + search (`ddgs`), cron, message (kirim ke kanal), sessions (kelola sesi), subagent (spawn sub-agen), MCP (lihat bagian 8), image generation, `my` (self), `cli_apps`, `rg` (ripgrep), `apply_patch`.
- **Pembatasan tool:** `ToolsConfig` (`config/schema.py`) punya `enabled_tools` per MCP server, `max_session_messages_per_minute` (default 6), `restrict_to_workspace` (**default `False`** — niat kebijakan "jaga akses tool di dalam workspace", tapi mati secara default), `ssrf_whitelist`, `webui_allow_local_service_access` (default True), `webui_allow_remote_package_install` (default False). Tidak ada allowlist tool per skill dan tidak ada approval sebelum eksekusi.

## 5. Sistem memori

Yang ada adalah **memori file Markdown + log JSONL**, tanpa database vektor dan tanpa fakta terstruktur:

- **File kanonik** (`nanobot/agent/memory.py`, kelas `MemoryStore` — "Pure file I/O for memory files"):
  - `memory/MEMORY.md` — memori jangka panjang (template di `nanobot/templates/memory/MEMORY.md`: User Information, Preferences, Project Context, Important Notes)
  - `memory/history.jsonl` — log percakapan append-only (tiap entri: `cursor`, `timestamp`, `content`; maks 1.000 entri default), plus `HISTORY.md` legacy
  - `SOUL.md` — perilaku/guardrail agen; `USER.md` — atribut/preferensi user (keduanya di root workspace)
  - `memory/.cursor`, `memory/.dream_cursor` — kursor konsumsi Dream
- **Dream (konsolidasi):** `templates/agent/dream.md` — agen konsolidator yang merangkum riwayat ke file kanonik (routing per file: SOUL/USER/MEMORY/skill), dengan tag retensi `[skip]`, `[correction]`, `[permanent]`, `[durable]`, `[ephemeral]`. Jalan sesuai jadwal kalau diaktifkan, atau manual lewat perintah `/dream`; `/dream-log` dan `/dream-restore <sha>` melihat/mengembalikan versi lama. **Memori di-backup git** (`nanobot/utils/gitstore.py`, `GitStore`) sehingga tiap perubahan Dream tercatat sebagai commit yang bisa di-restore.
- **Skill `memory`:** hanya menginstruksikan agen mencari di `history.jsonl` dengan tool text-search. Tidak ada pencarian semantik.
- **Kompaksi konteks:** `agent/autocompact.py` + `session/summary.py` — ringkasan sesi saat konteks membesar, atau manual `/compact`.
- **Sesi:** `session/sqlite_store.py` (SQLite) + `session/manager.py` — sesi, metadata, riwayat, checkpoint, recovery; `session/history_graph.py` untuk ukuran/struktur riwayat.
- **Yang TIDAK ada:** memori terstruktur (fakta + asal + validitas + konflik), pencarian vektor, graf pengetahuan, pelingkupan memori per agen/sub-agen (semua agen berbagi file workspace yang sama), Usulan/konfirmasi user atas fakta yang disimpulkan. Semua itu harus dibangun Puspa (mem0 + Graphiti sesuai definisi 2.7).

## 6. Cron / penjadwal

Sudah ada dan cukup lengkap untuk kebutuhan dasar:

- **Model:** `nanobot/cron/types.py` — `CronSchedule` (kind `at` / `every` / `cron`, dengan `at_ms`, `every_ms`, `expr` cron + `tz`), `CronPayload` (kind `agent_turn` atau `system_event`, `message`, `deliver`, `channel`, `to`, `session_key`, `origin_channel`, `origin_chat_id`), `CronRunRecord` (status `ok`/`error`/`skipped`, durasi, error).
- **Service:** `nanobot/cron/service.py` — memuat/menyimpan job dari **`jobs.json`** (dengan backup `.corrupt-<ts>` kalau file rusak), menjalankan job, mencatat riwayat eksekusi. `binding.py`, `bound_runner.py`, `session_delivery.py`, `session_turns.py` mengikat job ke sesi dan mengantarkan hasil ke kanal asal.
- **Tool + skill:** `nanobot/agent/tools/cron.py` (tool `cron`: `add`/`list`/`remove`/pause/resume) dan `nanobot/skills/cron/SKILL.md` — agen membuat jadwal dari bahasa natural ("every day at 8am, remind me…", `cron_expr="0 9 * * 1-5"`, `at="<ISO datetime>"`, `every_seconds=1200`). Jenis tugas: Reminder, Task (agen mengeksekusi tiap jalan), One-time (hapus otomatis setelah jalan).
- **HEARTBEAT.md:** pola cek latar berkala dengan timing fleksibel — hanya memberi notif kalau ada perubahan yang bisa ditindaklanjuti; eksplisit BUKAN pengganti cron untuk jadwal yang harus tepat waktu.
- **Catatan untuk Puspa:** cron nanobot adalah job di `jobs.json` lokal per instance, dieksekusi oleh gateway yang jalan. Definisi Puspa (2.12) minta tugas terjadwal sebagai baris Postgres + scheduler APScheduler di VPS + audit ke Buku Kerja — jadi lapisan cron ini bisa dipakai polanya, tapi penyimpanan dan eksekusinya perlu dipindah/dibungkus ke Postgres + Buku Kerja.

## 7. Kanal (WA / Telegram / dll.)

19 paket kanal di `nanobot/channels/` (plus folder `tests/`):

| Kanal | Paket | Cara kerja (dari runtime + manifest) |
|---|---|---|
| WhatsApp | `whatsapp/` | **neonize** (pustaka WhatsApp Web, dependensi `neonize>=0.5.2`, `python-magic`, `segno` untuk QR). Bukan Cloud API resmi — ini protokol WA Web tidak resmi, ada risiko blokir nomor. Config: `allow_from` (allowlist pengirim), `group_policy` open/mention, `database_path`, `lid_mappings`. Login via QR/pairing |
| WhatsApp (agen) | `whatsapp_agent/` | Varian kanal WA kedua (agen) |
| Telegram | `telegram/` | `python-telegram-bot` (Bot API resmi, long polling). Handler pesan, callback query, reaksi |
| Discord | `discord/` | Bot Discord |
| Slack | `slack/` | Bot Slack |
| Signal | `signal/` | Signal |
| Matrix | `matrix/` | Matrix |
| MS Teams | `msteams/` | Microsoft Teams |
| Feishu | `feishu/` | Feishu/Lark (websocket + `lark-oapi`) |
| WeChat | `weixin/` | WeChat |
| DingTalk | `dingtalk/` | DingTalk |
| WeCom | `wecom/` | WeCom |
| QQ | `qq/` | QQ |
| NapCat | `napcat/` | NapCat (jembatan QQ) |
| MoChat | `mochat/` | MoChat |
| Email | `email/` | Kanal email (baca + balas email sebagai percakapan) |
| Linear | `linear/` | Linear (dengan pengecekan keanggotaan tim) |
| Mattermost | `mattermost/` | Mattermost |
| WebSocket | `websocket/` | Kanal WebSocket generik (dipakai WebUI + integrasi luar) |

Pola umum tiap kanal: `manifest.py` (definisi `ChannelPlugin`: nama, runtime, connector, setup spec, dependensi, webui), `runtime.py` (kelas channel turunan `BaseChannel`, menerima pesan → `MessageBus` → mengirim balasan), `validation.py`, folder `webui/` (UI pengaturan kanal). Kanal diaktifkan dari config; dependensi kanal diinstal terpisah (`scripts/install_channel_dependencies.py`).

**Catatan untuk Puspa:** definisi MVP (2.8) menyatakan **tidak ada WhatsApp dan Telegram di MVP (PWA saja)**. Jadi kanal-kanal ini adalah cadangan untuk fase sesudah MVP, bukan yang dipakai dulu. Kalau WA dipakai nanti, perlu diputuskan: tetap neonize (tidak resmi, risiko blokir) atau pindah ke Cloud API resmi.

## 8. MCP

Sudah ada sebagai **MCP client** (nanobot terhubung ke MCP server luar), bukan MCP server:

- **Klien:** `nanobot/agent/tools/mcp.py` (`MCPProvider`) — koneksi stdio/SSE/streamable HTTP, retry untuk failure stream sementara, OAuth remote (`mcp_oauth.py`), timeout tool 30 detik.
- **Config:** `MCPServerConfig` (`config/schema.py`): `type` (stdio/sse/streamableHttp, auto-detect), `command`/`args`/`env`/`cwd` (stdio), `url`/`headers` (HTTP), `auth: oauth` (token disimpan di luar config), `tool_timeout`, dan **`enabled_tools` (default `["*"]` = semua capability: tools, resources, prompts; kalau dibatasi, hanya tool yang disebut, tanpa resources/prompts)**. Tool MCP dibungkus dengan nama `mcp_<server>_<tool>`.
- **Preset:** `nanobot/webui/mcp_presets.json` + `docs/mcp-presets.md` — preset server MCP siap pakai dari WebUI.
- **Yang TIDAK ada:** nanobot tidak mengekspos dirinya sebagai MCP server (tidak ada `mcp server` mode di repo ini); tidak ada pemindai keamanan atas MCP server/tool (tool poisoning, rug-pull). Puspa perlu menambahkan kurasi + tanda tangan untuk MCP server yang diizinkan.

## 9. Keamanan yang sudah ada (dan yang belum)

Yang sudah ada:
- **Batas path workspace:** `security/workspace_policy.py` + `workspace_access.py` — helper `resolve_path`, `is_path_within`, `require_path_within`, `WorkspaceScope` via `ContextVar` (`bind_workspace_scope`). Dokumennya sendiri menegaskan ini **bukan pengganti sandbox OS**.
- **Sandbox shell:** `execution/sandbox.py` — backend **bwrap** (bubblewrap, Linux) dan **seatbelt** (macOS), dengan bind read-only/read-write; `security/sandbox_launcher.py`; `docker-compose.bwrap.yml` (butuh `SYS_ADMIN`, AppArmor/seccomp unconfined). Sandbox dipakai untuk eksekusi shell, bukan sebagai container per agen.
- **Jaringan:** `security/network.py` — anti-SSRF (validasi target URL, pinned DNS transport), `NetworkConfig` + `ssrf_whitelist` (CIDR yang dikecualikan, mis. Tailscale). `config/network.py` untuk proxy.
- **Docker:** entrypoint menurunkan privilege root → user `nanobot` (fail-closed), `cap_drop: ALL`, `no-new-privileges`.
- **Rilis:** `release_trust.py` + `sigstore` (dependensi `sigstore==4.5.0`) — verifikasi approval rilis paket. Ini untuk update nanobot sendiri, bukan untuk skill.
- **Pairing DM:** `pairing/store.py` — approval pengirim DM (allowlist siapa boleh DM agen). Ini soal siapa boleh bicara, bukan soal aksi agen.
- **Goal permission:** `agent/goal_permission.py` — izin mutasi goal yang diikat per run lewat `ContextVar` (pola yang mirip token izin, tapi cakupannya hanya goal, bukan tool).

Yang **TIDAK ada** (harus dibangun Puspa):
- **Tidak ada approval per aksi.** Grep `approval` di seluruh `nanobot/` hanya menemukan: approval pengirim DM (pairing), approval rilis paket (sigstore), dan prompt OAuth device. Tidak ada antrean persetujuan, tidak ada persetujuan terikat hash isi, tidak ada batas belanja sebagai kode.
- **`tools.restrictToWorkspace` default mati** (`False` di `ToolsConfig`) — sesuai catatan survei.
- **Tidak ada tanda tangan skill** (skill workspace + ClawHub dimuat tanpa verifikasi).
- **Tidak ada token izin** per agen/sub-agen (tidak ada daftar tool/domain/konektor/anggaran/waktu kedaluwarsa yang diperiksa kode).
- **Tidak ada gerbang tunggal aksi keluar** (tool kirim/pesan/eksekusi dipanggil langsung dari loop runner).
- **Tidak ada sandbox per agen/bot** (satu proses gateway + workspace bersama; sandbox hanya membungkus perintah shell).
- **Tidak ada pemisahan data antar agen** (satu workspace, satu MEMORY.md, satu history.jsonl).
- **Tidak ada konektor Google** (Gmail/Drive/Calendar) — kanal email ada, tapi bukan konektor OAuth Google dengan scope minimal. Dependensi `oauth-cli-kit` sudah ada dan pola OAuth provider (GitHub Copilot, Codex, xAI) bisa dicontoh.
- **Tidak ada PWA** — WebUI adalah aplikasi React desktop-browser; tidak ada manifest/service worker PWA.

## 10. Titik kait untuk lapisan Puspa (analisis)

Tempat paling natural menyambungkan tiap lapisan Puspa, berdasarkan struktur yang dibaca:

| Lapisan Puspa | Titik kait di nanobot | Alasan |
|---|---|---|
| **Gerbang tunggal aksi keluar** | `nanobot/agent/runner.py` (loop tool-call) + `nanobot/agent/tools/registry.py` (`ToolRegistry.get`/eksekusi) + `nanobot/bus/delivery.py` (pengiriman outbound) | Semua tool call dan semua pesan keluar melewati titik-titik ini. Satu pembungkus di sini menutup semua jalur keluar tanpa mengubah tiap tool |
| **Token izin (menyempit)** | `nanobot/agent/tools/context.py` (`RequestContext`/`ToolContext` via `ContextVar`) + `nanobot/security/workspace_access.py` (`WorkspaceScope`, `bind_workspace_scope`) + `nanobot/agent/goal_permission.py` (pola izin per-run yang sudah ada) | Mekanisme konteks per-request sudah ada; token bisa digantung di `RequestContext` dan diperiksa di `ToolRegistry` sebelum eksekusi. `WorkspaceScope` adalah contoh pola scope yang sudah bekerja |
| **Buku Kerja (log append-only)** | `nanobot/bus/events.py` + `nanobot/bus/queue.py` (semua event pesan) + `nanobot/session/sqlite_store.py` (penyimpanan SQLite yang sudah ada) + `nanobot/agent/memory.py` (`history.jsonl` append-only) | Bus sudah menjadi satu tempat semua kejadian lewat; Buku Kerja bisa dibangun sebagai sink event di bus + tabel SQLite/Postgres baru, dengan pola penyimpanan yang sudah terbukti di `sqlite_store.py` |
| **Approval per aksi** | `nanobot/agent/runner.py` (tahan tool call sebelum eksekusi) + WebUI (`webui/`, antrean persetujuan baru) + `nanobot/bus/` (pengiriman hasil persetujuan kembali ke loop) | Runner adalah satu-satunya tempat tool dieksekusi; antrean persetujuan tinggal menahan di sana. Pola "tahan lalu lanjutkan" sudah ada di `session/turn_continuation.py` dan `agent/pending_inputs.py` |
| **Memori terstruktur (mem0 + Graphiti)** | `nanobot/agent/memory.py` (`MemoryStore`) + `nanobot/templates/agent/dream.md` | `MemoryStore` adalah satu-satunya pintu baca/tulis memori; ganti isinya dengan mem0/Graphiti tanpa mengubah pemanggil. Dream tetap sebagai konsolidator, tapi menulis fakta ber-skema + asal, bukan Markdown bebas |
| **Sandbox per bot** | `nanobot/execution/sandbox.py` (backend bwrap/seatbelt) + `nanobot/agent/tools/exec_session.py` + `docker-compose.bwrap.yml` | Backend sandbox sudah ada; yang belum adalah orkestrasi satu sandbox per agen/sub-agen dan pemaksaan pemakaiannya (saat ini opsional). Fail-closed sudah dicontohkan di `entrypoint.sh` |
| **Model sub-agen** | `nanobot/agent/subagent.py` (`SubagentManager`) + `nanobot/agent/tools/subagent.py` (tool `subagent`: create/send/cancel/check) + `nanobot/templates/agent/subagent_system.md` | Spawn sub-agen per tugas **sudah ada dan bekerja** (create → task → wait/check/cancel, sesi privat per tugas). Yang belum: kelas izin per spawn (Pembaca/Pelaksana/Pemeriksa), token yang menyempit dari induk, batas 1 level, batas paralel + anggaran, kontrak handoff ber-skema. Ini lapisan di atas `SubagentManager`, bukan bangun ulang |
| **Skill bertanda tangan** | `nanobot/agent/skills.py` (`SkillsLoader`) + `nanobot/agent/skill_metadata.py` + `nanobot/skills/clawhub/` | Semua skill masuk lewat `SkillsLoader`; satu pemeriksaan signature di sini menutup skill bawaan, workspace, dan ClawHub sekaligus. Pola verifikasi rilis (`release_trust.py` + sigstore) bisa dicontoh |
| **Konektor Google** | Pola OAuth di `nanobot/providers/` (`github_copilot_oauth.py`, `openai_codex_oauth.py`, `xai_oauth.py`) + dependensi `oauth-cli-kit` + kanal `email/` | Pola OAuth + penyimpanan token di luar config sudah ada; konektor Gmail/Drive/Calendar baru mengikuti pola ini |
| **Penjadwal → Buku Kerja** | `nanobot/cron/service.py` (`jobs.json`) + `nanobot/triggers/local_store.py` | Job cron sudah berstruktur (`CronSchedule`/`CronPayload`); yang berubah adalah penyimpanan (jobs.json → Postgres) dan pencatatan eksekusi ke Buku Kerja |
| **PWA** | `webui/` (React + Vite) | WebUI sudah ada sebagai frontend; PWA berarti menambah manifest/service worker/push + 7 layar Puspa, bukan menulis UI dari nol |

## 11. Dependensi Python utama

Python yang dibutuhkan: **>= 3.11** (dikembangkan/diuji di 3.11 dan 3.12; target ruff/pyright `py311`).

Dependensi inti dari `pyproject.toml` (paket `nanobot-ai` 0.4.1):

| Dependensi | Versi | Untuk |
|---|---|---|
| `typer` | >=0.20, <1.0 | CLI |
| `anthropic`, `openai` | anthropic >=0.100, <1.0; openai >=2.8 | Provider LLM |
| `pydantic`, `pydantic-settings` | >=2.12, <3.0 | Config + skema |
| `mcp` | >=1.26, <2.0 | Klien MCP |
| `httpx[socks]`, `aiohttp`, `websockets` | httpx >=0.28; aiohttp >=3.14; websockets 15–16 | HTTP/WebSocket (Feishu minta websockets <16) |
| `croniter` | >=6.0, <7.0 | Cron |
| `loguru`, `rich`, `prompt-toolkit`, `questionary` | — | Logging + TUI/CLI |
| `cryptography`, `sigstore==4.5.0` | — | Keamanan + verifikasi rilis |
| `ddgs` | >=9.16, <10 | Pencarian web |
| `readability-lxml`, `lxml-html-clean` | — | Ekstrak konten web |
| `qrcode[pil]`, `pillow` | — | QR pairing WA |
| `tiktoken`, `jinja2`, `pyyaml`, `jsonschema`, `json-repair`, `regex`, `rapidfuzz`, `dulwich` | — | Token, template, skema, git |
| `pypdf`, `python-docx`, `openpyxl`, `python-pptx`, `defusedxml` | — | Baca dokumen (PDF/Word/Excel/PowerPoint) |
| `oauth-cli-kit` | >=0.1.6, <1.0 | Alur OAuth CLI |
| `filelock`, `watchfiles`, `tzdata`, `tzlocal` | — | Lock, watch file, zona waktu |
| `setproctitle` | — | Nama proses (non-Windows) |

Optional extras: `azure` (azure-identity), `bedrock` (boto3/botocore), `langfuse`, `olostep`, `dev` (pytest, ruff, basedpyright, coverage, dsb.). Dependensi kanal (mis. `neonize` untuk WA, `python-telegram-bot` untuk Telegram) **tidak** ada di `pyproject.toml` utama — diinstal terpisah per kanal lewat `scripts/install_channel_dependencies.py`.

Build: `hatchling` + `hatch_build.py` (hook custom). Yang ikut paket: `nanobot/**/*.py`, template Markdown, skill bawaan, preset MCP, `nanobot/web/dist/**/*` (hasil build WebUI).

## 12. Catatan lain

- **Dokumentasi internal sangat baik.** `docs/architecture.md` memetakan tiap perilaku ke file sumber; `docs/memory.md` menjelaskan Dream + `/dream-log` + `/dream-restore`. Ini mempercepat kerja Fase 0.
- **Tes:** `tests/` + tes di tiap kanal; `pytest` dengan `asyncio_mode = auto`; gate coverage 75%.
- **Update mandiri:** nanobot punya sistem update sendiri (`nanobot/update*.py`, `release_feed.py`, `release_trust.py`) dengan verifikasi approval rilis — relevan saat Puspa nanti mendistribusikan update fork.
- **Python SDK:** `nanobot/sdk/` + `docs/python-sdk.md` — integrasi dari kode Python dimungkinkan tanpa CLI.
- **Tidak ada yang perlu diinstal/dijalankan di fase ini.** Semua temuan di atas dari membaca kode hasil clone, sesuai tugas.
