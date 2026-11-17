# Glossary — Revisi 7 terms (Indonesian) → English code names

Source of truth for the repo-wide English rename (ordered by Nero,
18 Nov 2026). Code identifiers are English; stored domain values
(event types, table/column names, statuses, enum values) are English;
prose (docstrings, comments, docs, messages) is English.

Proper nouns are kept as-is: Puspa, PLN, PDAM, Tokopedia, Shopee,
Blibli, GoPay, OVO, DANA, QRIS, BI-FAST.

## Concepts

| Revisi 7 (Indonesian) | English code name |
|---|---|
| Buku Kerja | ledger |
| Pemeriksa | verifier |
| Pembaca terkarantina | quarantined reader |
| Pembaca | reader |
| Pelaksana | executor |
| alur | workflow |
| modul | module |
| kejadian | event |
| tugas | task |
| agen | agent |
| sub-agen | subagent |
| token izin | permission token |
| gerbang | gate |
| batas belanja | spending limit |
| angka yatim | orphan number |
| vonis | verdict |
| kaset | cassette |
| pemicu | trigger |
| handoff | handoff |
| detak | heartbeat |
| Kantor | office |
| Rekor Otonomi | autonomy record |
| Buku Bukti | evidence book |
| Skor Keandalan | reliability score |
| Graf Alur Layanan | service flow graph |

## Event types (31) — `type` in `events`

| Indonesian | English |
|---|---|
| tugas_dibuat | task_created |
| rencana_dibuat | plan_created |
| subagen_dibuat | subagent_created |
| handoff_diajukan | handoff_proposed |
| handoff_diterima | handoff_accepted |
| handoff_diserahkan | handoff_handed_over |
| handoff_ditolak | handoff_rejected |
| handoff_kedaluwarsa | handoff_expired |
| detak | heartbeat |
| tool_dipanggil | tool_called |
| tool_selesai | tool_completed |
| bukti_dicatat | evidence_recorded |
| pemeriksaan_selesai | verification_completed |
| persetujuan_diminta | approval_requested |
| persetujuan_diberikan | approval_granted |
| persetujuan_ditolak | approval_rejected |
| pesan_user | user_message |
| pesan_agen | agent_message |
| anggaran_habis | budget_exhausted |
| tugas_selesai | task_completed |
| tugas_gagal | task_failed |
| tugas_dihentikan | task_stopped |
| alur_diajukan | workflow_proposed |
| alur_diaktifkan | workflow_activated |
| alur_dijeda | workflow_paused |
| pemicu_terpicu | trigger_fired |
| pemicu_terlewat | trigger_missed |
| modul_diajukan | module_proposed |
| modul_diaktifkan | module_activated |
| laporan_salah | false_report |
| persetujuan_data | data_consent |

Note: `false_report` / `data_consent` are SINGULAR event names. The
tables that store them are PLURAL (`false_reports`, `data_consents`).

## Tables

| Indonesian | English |
|---|---|
| kejadian | events |
| pengguna | users |
| sesi | sessions |
| vault | vault |
| agen | agents |
| agen_meta | agent_meta |
| fakta_profil | profile_facts |
| episodik | episodic |
| prosedur | procedures |
| pemberian_irisan | slice_grants |
| ringkasan | summaries |
| contoh_gaya | style_examples |
| onboarding | onboarding |
| persetujuan_data | data_consents |
| tautan_email_digunakan | used_email_links |
| tugas | tasks |
| handoff | handoffs |
| bukti | evidence |
| pemicu | triggers |
| pemicu_jalan | trigger_runs |
| konektor_agen | agent_connectors |
| kaset | cassettes |
| langganan_push | push_subscriptions |
| detak_sistem | system_heartbeats |
| produk_kanonik | canonical_products |
| tautan_listing | listing_links |
| harga_seri | price_series |
| watchlist | watchlist |
| anggaran_baca | read_budgets |
| cache_bukti | evidence_cache |
| pembacaan_sumber | source_readings |
| prosedur_versi | procedure_versions |
| pemakaian_prosedur | procedure_uses |
| canary_jalan | canary_runs |
| laporan_salah | false_reports |
| kredit_bulanan | monthly_credits |
| metrik_token | token_metrics |
| pustaka_alur | workflow_library |
| permintaan_blok | block_requests |
| cek_syarat_layanan | tos_checks |
| alur | workflows |
| alur_versi | workflow_versions |
| jalan_alur | workflow_runs |
| persetujuan_alur | workflow_approvals |
| modul | modules |
| modul_versi | module_versions |
| tabel_modul | module_tables |
| baris_modul | module_rows |

## Columns / fields (selection — full map in `rename_map.json`)

| Indonesian | English |
|---|---|
| tugas_id | task_id |
| agen_id | agent_id |
| sub_agen_id | subagent_id |
| jenis | type |
| isi | content |
| id_induk | parent_id |
| hash_sebelumnya | previous_hash |
| waktu | created_at |
| dibuat | created_at |
| diubah | updated_at |
| terakhir_dilihat | last_seen_at |
| dicabut | revoked |
| kunci | key |
| nilai | value |
| asal | source |
| status | status |
| berlaku_dari | valid_from |
| berlaku_sampai | valid_until |
| kejadian_id | event_id |
| layanan | service |
| langkah | step |
| skema_keluaran | output_schema |
| pemeriksaan_wajib | required_checks |
| jalan_sukses | successful_runs |
| jalan_gagal | failed_runs |
| jalan_gagal_berturut | consecutive_failed_runs |
| agen_ids | agent_ids |
| kejadian_asal_id | source_event_id |
| periode_awal | period_start |
| periode_akhir | period_end |
| teks | text |
| langkah_saat_ini | current_step |
| nama | name |
| gaya | style |
| notif_uji | test_notification |
| konektor | connector |
| tugas_pertama | first_task |
| hasil_pertama_terbukti | first_result_verified |
| dimulai | started_at |
| selesai | completed_at |
| durasi_detik | duration_seconds |
| versi_teks | text_version |
| disetujui_pada | approved_at |
| token_hash | token_hash |
| dipakai_pada | used_at |
| tanggal_cek | checked_on |
| url_sumber | source_url |
| baca_ulang_diminta | reread_requested |
| langkah_hasil | step_results |
| versi_aktif | active_version |
| rating_toko | store_rating |
| ambang_harga | price_threshold |
| nomor_model | model_number |
| potongan_teks | text_excerpt |
| hash_halaman | page_hash |
| tangkapan_layar | screenshot |
| id_pembaca | reader_id |
| id_pemeriksa | verifier_id |
| kelas_izin | permission_class |
| dikonfirmasi_user | user_confirmed |
| ditolak_user | user_rejected |
| diucapkan_user | user_stated |
| disimpulkan | inferred |
| diserahkan | handed_over_at |
| batas_waktu_detik | timeout_seconds |
| detak_terakhir | last_heartbeat |
| keluaran_diharapkan | expected_output |
| kunci_idempotensi | idempotency_key |
| definisi_selesai | completion_definition |
| pemicu_berikutnya | next_trigger_at |
| gagal_berturut | consecutive_failures |
| jumlah | amount |
| judul | title |
| konteks | context |
| merek | brand |
| baris | rows |

## Statuses / enums

| Indonesian | English |
|---|---|
| diterima | accepted |
| diajukan | proposed |
| ditolak | rejected |
| diberikan | granted |
| menunggu | pending |
| kedaluwarsa | expired (status value) / expires_at (column) |
| dibatalkan | cancelled |
| draf | draft |
| aktif | active |
| dijeda | paused |
| dihapus | deleted |
| selesai | completed (status value) / completed_at (column) |
| usulan | proposed |
| berlaku | active |
| diganti | replaced |
| percobaan | trial |
| terverifikasi | verified |
| rusak | broken |
| otomatis | automatic |
| Terverifikasi | Verified |
| Belum pasti | Uncertain |
| Tidak bisa dipastikan | Cannot be determined |
| tidak_diketahui | unknown |
| menunggu_keputusan_nero | awaiting_nero_decision |
| HP | phone |
| konsol game | game console |
| kamera | camera |
| peralatan rumah tangga bermerek | branded home appliances |
| earphone/TWS | earphones/TWS |

## Workflow blocks (15)

| Indonesian | English |
|---|---|
| cek_tagihan | check_bill |
| bandingkan_harga | compare_prices |
| pantau_harga | watch_prices |
| cari_email | search_email |
| baca_email | read_email |
| buat_draf | create_draft |
| buat_event | create_event |
| siapkan_bayar | prepare_payment |
| kirim_notif | send_notification |
| tanya_user | ask_user |
| tunggu_persetujuan | wait_for_approval |
| kondisi | condition |
| untuk_tiap | for_each |
| simpan_memori | save_memory |
| simpan_ke_tabel_modul | save_to_module_table |

## Tools

| Indonesian | English |
|---|---|
| baca_file | read_file |
| jalankan_kode | run_code |
| baca_harga | read_price |
| cari_listing | search_listings |

## PWA screens / templates

| Indonesian | English |
|---|---|
| tugas.html | tasks.html |
| memori.html | memory.html |
| konektor.html | connectors.html |
| modul.html | modules.html |
| pengaturan.html | settings.html |
| masuk.html | login.html |
| kantor.html | office.html |
| panel_bukti.html | evidence_panel.html |
| base.html, chat.html | (unchanged) |

## File / directory renames

| Old | New |
|---|---|
| infra/setup_db.sql | infra/schema_core.sql |
| infra/schema_fase2.sql | infra/schema_runtime.sql |
| infra/schema_fase3.sql | infra/schema_graphs.sql |
| infra/schema_fase4.sql | infra/schema_workflow.sql |
| puspa/chassis/skills.py | puspa/chassis/skill_loader.py |
| puspa/policy/skills.py | puspa/policy/skill_signing.py |
| puspa/chassis/session.py | puspa/chassis/agent_session.py |
| puspa/accounts/sessions.py | puspa/accounts/user_sessions.py |
| puspa/accounts/schema_agen.sql | puspa/accounts/schema_agent.sql |
| puspa/modules/tagihan.py | puspa/modules/bills.py |
| cassettes/buat_kaset_acuan.py | cassettes/create_reference_cassettes.py |
| tests/test_gates.py (fase-2) | tests/test_runtime_gates.py |
| tests/test_gates.py (fase-4) | tests/test_workflow_gates.py |
| tests/test_gates_fase3.py | tests/test_integration_gates.py |
| tests/test_rls_fase1.py | tests/test_rls_memory.py |
| nanobot-INVENTORY.md | docs/nanobot-inventory.md |

## Decisions

- `isi` → `content` everywhere (not `payload` for the ledger).
- Template file uses underscore `evidence_panel.html`.
- `waktu` → `created_at` globally (ledger/recordings).
- `jumlah` → `amount` (component); `anggaran_baca.jumlah` (a count) is a
  review item.
- `rupiah_max` is KEPT (rupiah is the currency name, like `usd_max`).
  The glossary's earlier `rupiah_max → max_idr` mapping is withdrawn.
- `daftar` → `list` is FORBIDDEN as an identifier (shadows the builtin);
  use `steps` / `items` / a descriptive name instead. The rename script
  never returns a Python keyword or builtin name.
- `profil` (local variable) vs the imported `profile` module: rename the
  local to `profile_data` — never shadow an imported module.
- `tanggal`/`date` (local variable) vs the imported `date` class: rename
  the local to `day`.
- Injection regex patterns and the quarantined-reader unit regex match
  Indonesian text; their PATTERNS stay Indonesian, only the pattern
  NAMES (labels) are English.
- The workflow composer matches Indonesian user text; its keywords stay
  Indonesian, with English alternatives added (bilingual).
- Test data that is Indonesian user content (episode text, bill text,
  chat text) stays Indonesian; only the test's identifiers and
  assertions that name code concepts are English.
- `test_kering` → `dry_run` (a `test_*` name in non-test code is
  collected by pytest as a test).
- `sub_agent_id` (Python parameter / dataclass field) vs `subagent_id`
  (token payload key / DB column) are intentionally different names.
  `_payload(**overrides)` kwargs must match the payload keys exactly.
- `connector` (singular, function parameter) vs `connectors` (plural,
  token payload key) are intentionally different. `_payload` kwargs
  must use `connectors=`.
- Event types `false_report` / `data_consent` are SINGULAR; the tables
  `false_reports` / `data_consents` are PLURAL.
- `kedaluwarsa` is ambiguous: as a status VALUE → `expired`; as a
  COLUMN → `expires_at`. `selesai` likewise: value → `completed`,
  column → `completed_at`.
- Standard English terms are kept as-is (never renamed): sink, turn,
  chassis, span, panel, manifest, profile, version, active, card,
  template, handoff, cassette, trigger, workflow, module, gate, ledger,
  verdict, canary, data, domain, hash, id, model.
