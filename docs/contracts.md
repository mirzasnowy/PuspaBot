# KONTRAK BERSAMA — Fase 0 MVP Puspa

Sumber kebenaran: `~/workspace/match-hunt/definisi-mvp-puspa-2026-11-17-revisi-7.md` (revisi 7, disetujui Nero 17 Nov 2026 ~17:42 WIB). Dokumen ini adalah kontrak antar-subagen agar antarmuka tidak melenceng. Kalau kontrak bertentangan dengan definisi, definisi yang menang; catat selisihnya di STATUS.md.

## Lokasi dan tooling

- Root build: `~/workspace/goals/hard-ai-engineering-portfolio-project/build/fase-0/`
- Venv: `build/fase-0/.venv` (Python 3.12.3). Dependensi di `requirements.txt`: `psycopg[binary]`, `argon2-cffi`, `cryptography`, `pytest` + dependensi deklarasi nanobot (pydantic, pydantic-settings, loguru, pyyaml, json_repair, tiktoken, jinja2, regex, jsonschema, filelock, httpx, tzlocal, dsb) + nanobot sendiri sebagai dependency git pin (`nanobot-ai @ git+https://github.com/HKUDS/nanobot@e767174`, nama paket PyPI `nanobot-ai`, import tetap `nanobot`). Clone lokal `./nanobot` hanya referensi baca + fallback `sys.path` di `tests/conftest.py` kalau direktorinya memang ada.
- Jalankan tes dari `build/fase-0` dengan `.venv/bin/python -m pytest tests -q`.
- `nanobot/` adalah hasil clone, JANGAN diubah (NOL baris kode nanobot diubah di Fase 0). Remote: `origin` SUDAH DICABUT (fork mirzasnowy/nanobot diabaikan, jangan dipakai); yang tersisa `upstream` = https://github.com/HKUDS/nanobot sebagai referensi baca. Repo Puspa adalah repo STANDALONE **mirzasnowy/puspa-ai** (bukan fork, bukan org); org puspa-ai DITUNDA. Aturan tanpa-fork Nero berlaku untuk semua kode reuse (lihat STATUS.md).
- JANGAN menyimpan secret, token, password, atau kunci di file, log, atau chat. Kunci dibuat saat runtime/tes. Master key vault hanya dari env `PUSPA_VAULT_MASTER_KEY`.

## Layout paket

```
build/fase-0/
  nanobot/            # clone HKUDS/nanobot, jangan diubah
  puspa/
    __init__.py
    ledger/           # Buku Kerja (2.3)
      __init__.py  schema.sql  ledger.py  errors.py
    policy/           # Token izin, skill, gerbang, belanja (2.6)
      __init__.py  tokens.py  skills.py  gate.py  spending.py
    accounts/         # Akun dasar (2.13) + RLS (2.15) + akses lintas pengguna (2.6)
      __init__.py  users.py  sessions.py  vault.py  rls.py  access.py
      schema.sql  schema_agen.sql
    quarantine/       # Pembaca terkarantina + injection (2.6)
      __init__.py  reader.py  injection.py
    sandbox/          # Sandbox fail-closed (2.6)
      __init__.py  runner.py  allowlist.py
    chassis/          # Integrasi dengan nanobot sebagai chassis (2.6)
      __init__.py  context.py  tools.py  registry.py  hooks.py
      skills.py  session.py  sandbox_tool.py  factory.py
  tests/
    conftest.py  test_ledger.py  test_ledger_event_types.py  test_policy.py
    test_gate.py  test_accounts.py  test_quarantine.py  test_sandbox.py
    test_attacks.py  test_integration.py  test_rls.py  test_vault.py
    test_access.py  test_byok_provider.py
  demo/
    byok_demo.py  byok_provider.py  openrouter_provider.py
    mock_provider_server.py  MATRIKS_BYOK.md  LAPORAN_DEMO.md  NOTES.md
    mock_logs/  data/
  infra/
    setup_db.sql
  requirements.txt  pytest.ini  README.md  CONTRACTS.md  STATUS.md
  NOTICE.md  nanobot-INVENTORY.md
```

## Database (Postgres)

- Server Postgres lokal. Test DB `puspa_test`. DSN dari env `PUSPA_TEST_DSN`, default `dbname=puspa_test user=root host=/var/run/postgresql` (peer auth, tanpa password).
- Peran `root` dibuat dengan LOGIN; DB `puspa_test` milik `root`.
- Semua tabel aplikasi punya kolom `user_id`, kecuali `pengguna` (PK-nya
  `id`, tanpa kolom `user_id`) dan `kejadian` (memakai `agen_id` +
  `tugas_id`; isolasi pengguna di `kejadian` lewat `agen_id` yang terikat
  ke `user_id` pemilik agen — catat pemetaan ini di ledger.py).

## Buku Kerja — `puspa/ledger` (definisi 2.3)

- Tabel `kejadian`, append-only. Kolom: `id UUID PK DEFAULT gen_random_uuid()`, `waktu TIMESTAMPTZ NOT NULL DEFAULT now()`, `tugas_id TEXT NOT NULL`, `agen_id TEXT NOT NULL`, `sub_agen_id TEXT`, `jenis TEXT NOT NULL CHECK (jenis IN (...31 jenis...))`, `isi JSONB NOT NULL DEFAULT '{}'`, `id_induk UUID REFERENCES kejadian(id)`, `hash_sebelumnya TEXT NOT NULL`, `hash TEXT NOT NULL UNIQUE`. Index: `(tugas_id, id)`, `jenis`, `waktu`.
- Himpunan jenis tertutup (31, revisi 7: 22 lama + 9 baru): tugas_dibuat, rencana_dibuat, subagen_dibuat, handoff_diajukan, handoff_diterima, handoff_diserahkan, handoff_ditolak, handoff_kedaluwarsa, detak, tool_dipanggil, tool_selesai, bukti_dicatat, pemeriksaan_selesai, persetujuan_diminta, persetujuan_diberikan, persetujuan_ditolak, pesan_user, pesan_agen, anggaran_habis, tugas_selesai, tugas_gagal, tugas_dihentikan, alur_diajukan, alur_diaktifkan, alur_dijeda, pemicu_terpicu, pemicu_terlewat, modul_diajukan, modul_diaktifkan, laporan_salah, persetujuan_data.
- Migrasi CHECK jenis (idempotent, di `puspa/ledger/schema.sql`): `CREATE TABLE IF NOT EXISTS` tidak mengubah constraint tabel yang sudah ada (mis. dibuat saat himpunan masih 22 jenis), jadi blok migrasi di akhir skema men-DROP constraint lama lalu ADD constraint baru dengan daftar 31 jenis revisi 7, dengan nama constraint yang sama (`kejadian_jenis_check`).
- Izin DB: peran aplikasi hanya INSERT + SELECT pada `kejadian`. Tanpa UPDATE/DELETE (REVOKE).
- API:
  - `Ledger(dsn)`; `create_schema()` — menjalankan `schema.sql` (CREATE TABLE IF NOT EXISTS + REVOKE).
  - `append_event(*, tugas_id, agen_id, sub_agen_id=None, jenis, isi: dict, id_induk=None) -> dict` — validasi `jenis`; `hash_sebelumnya` = hash kejadian sebelumnya dalam `tugas_id` yang sama (urut `(waktu, id)`), atau string `"GENESIS"` untuk kejadian pertama; `hash = sha256(canonical_json({tugas_id, agen_id, sub_agen_id, jenis, isi, id_induk, hash_sebelumnya, waktu_iso}))`; `waktu` dari DB (`now()`), dibaca kembali setelah insert.
  - `verify_chain(tugas_id) -> (bool, list[str])` — hitung ulang hash tiap kejadian berurutan; kembalikan (True, []) atau (False, [id kejadian yang putus]).
  - `events(tugas_id, jenis=None) -> list[dict]`.
- Tombstone (keputusan yang dicatat): hapus tugas TIDAK memakai UPDATE. Hapus = append `tugas_dihentikan` dengan `isi={"dihapus": true, ...}`; proyeksi menyembunyikan tugas itu. Alasan: append-only adalah invarian yang lebih kuat daripada "isi diganti" di definisi 2.3; kalau Nero tidak setuju, ini pertanyaan untuknya (lihat STATUS.md).
- Penyimpangan urutan rantai (keputusan 5 STATUS.md, perlu konfirmasi Nero): kontrak di atas menulis rantai diurutkan `id`, tapi `id` memakai `gen_random_uuid()` sehingga tidak terurut waktu sisip — implementasi mengurutkan penyisipan `(waktu, id)`. Alternatif: id UUIDv7.

## Token izin — `puspa/policy/tokens.py` (definisi 2.6)

- Ed25519 (`cryptography`). Token = `base64url(payload_json_kanonis).base64url(signature)`; signature atas bytes payload kanonis. Kunci dibuat saat runtime/tes, tidak disimpan di file.
- Payload: `user_id, agen_id, sub_agen_id (null untuk token agen), tools: [str], domains: [str], konektor: {nama: [scope]}, anggaran: {langkah_max, token_max, rupiah_max}, exp (unix), iat (unix), parent (hash token induk atau null)`.
- `issue_token(payload, signing_key) -> str`; `verify_token(token, public_key) -> dict` (raise `TokenError` kalau signature salah, `exp` lewat, atau field hilang).
- `is_subset(child, parent) -> bool`: `tools` ⊆, `domains` ⊆, tiap scope konektor ⊆, tiap anggaran ≤, `exp` ≤, `user_id` sama, `agen_id` sama; `sub_agen_id` hanya boleh terisi kalau parent adalah token agen.
- `issue_child(parent_token, requested_payload, signing_key, public_key) -> str` — verifikasi parent, cek subset, tandatangani anak. Gagal → `TokenError`, tidak ada token yang keluar.
- `revoke(token_id)` / `is_revoked(token_id)` — set pencabutan in-memory (persistensi fase 2). `revoke_all_for_tugas(tugas_id)` untuk Hentikan.

## Gerbang tunggal — `puspa/policy/gate.py` (definisi 2.6)

- `Gate(log=None, spending=None, public_key=None)` — `log` adalah callable pencatat kejadian (agar tidak circular import dengan ledger; tes boleh memakai stub pencatat). `public_key` (opsional di luar kontrak awal, lihat STATUS.md keputusan 6): bila token diberikan sebagai string, signature HARUS diverifikasi dengan `public_key` ini; tanpa `public_key`, token string ditolak. Token boleh juga diberikan sebagai payload dict yang sudah diverifikasi pemanggil.
- `request_approval(*, user_id, agen_id, token, kind, isi: dict) -> ApprovalRequest` — `kind` ∈ {kirim, buat_event, siapkan_bayar, hapus}. Hitung `isi_hash = sha256(canonical_json(isi))`. Cek token mengizinkan kind + target. Catat `persetujuan_diminta` lewat `log` bila ada.
- `approve(request_id, user_id)` — hanya pemilik user. Catat `persetujuan_diberikan`.
- `execute(request_id, *, executor)` — cek ulang SEMUA: sudah disetujui, `isi_hash` masih sama dengan isi saat ini, token masih valid (belum `exp`, belum dicabut), batas belanja OK. Baru panggil `executor(isi)` tepat sekali (kunci idempotensi = `request_id`; sudah dieksekusi → kembalikan hasil yang sama tanpa memanggil lagi). Gagal cek mana pun → raise `GateDenied`, TIDAK ada eksekusi, catat `persetujuan_ditolak` dengan alasan.
- `spending.py`: `SpendingLimit(user_id)` — default Rp0. `set_limit(user_id, rupiah)`; `spent_in_period(user_id)` (dari ledger bila tersedia, kalau tidak dari memori); `check(user_id, nominal) -> bool` (nominal + spent ≤ limit). Aturan MVP: persetujuan DAN batas adalah dua cek independen; limit default Rp0 berarti pembayaran apa pun tetap diblokir sampai user menaikkan limit di Pengaturan (API `set_limit` sudah ada; UI Pengaturan fase 2).

## Skill bertanda tangan — `puspa/policy/skills.py` (definisi 2.6)

- Skill = direktori berisi `SKILL.md` + `manifest.json` {name, version, tools[], domains[], konektor{}, author, signature}.
- `sign_skill(dirpath, signing_key)` — tanda tangani manifest kanonis (tanpa field `signature`).
- `verify_skill(dirpath, public_key) -> bool`; `load_skill(dirpath, public_key) -> dict` — raise `SkillRejected` kalau tidak ada signature, signature salah, atau tool di luar manifes saat dipakai.
- `check_tool(skill, tool, public_key=None)` — **kontrak diperbaiki 17 Nov 2026 (celah ditutup, lihat STATUS.md keputusan 7):** `public_key` WAJIB (None -> `SkillRejected`, fail-closed). Untuk dirpath: baca manifes dari disk lalu verifikasi signature; untuk dict: verifikasi field `signature` terhadap manifes unsigned; baru setelah lolos, cek keanggotaan tool. Manifes yang diubah setelah ditandatangani DITOLAK. Aturan pemanggil: JANGAN pernah memanggil `check_tool` dengan manifes mentah tanpa `public_key`.
- Kunci Puspa untuk skill bawaan dibuat saat runtime; kunci komunitas terdaftar terpisah (fase 6).

## Akun dasar — `puspa/accounts` (definisi 2.13)

- `users.py`: `create_user(email, password) -> user_id` (email lower/strip, unik; hash Argon2id via argon2-cffi, time_cost=2, memory_cost=65536, parallelism=1); `authenticate(email, password) -> user_id` (raise `AuthError` kalau salah); `delete_user(user_id)` — hapus baris pengguna + cabut semua sesi + hapus vault; kejadian di Buku Kerja TIDAK dihapus (audit append-only). CATATAN revisi 7: login kata sandi ini digantikan login Google di fase 1; sesi + vault dipakai ulang (STATUS.md keputusan 20).
- `sessions.py`: `create_session(user_id, device_label) -> token` (`secrets.token_urlsafe(32)`, yang disimpan hanya sha256-nya); `validate_session(token) -> user_id` (memperbarui `terakhir_dilihat`); `owner_of_token(token) -> user_id` (lookup pemilik TANPA efek samping — dipakai `resolve_session_as`, supaya penolakan tidak mengubah `terakhir_dilihat` sesi orang lain); `revoke_session(token)`; `revoke_all_sessions(user_id)`; `list_sessions(user_id)`.
- `vault.py`: kunci per pengguna = `derive_user_key(master_key, user_id)` — HKDF-SHA256(master_key, info=str(user_id)), length 32, salt=None — lalu Fernet. `put(user_id, name, value)`, `get(user_id, name)`, `delete(user_id, name)`, `list_names(user_id)`. Master key dari env `PUSPA_VAULT_MASTER_KEY` (base64 urlsafe 32 byte); kalau env tidak ada, raise — jangan buat kunci default. **Parameter `derive_user_key` DIBEKUKAN** (STATUS.md keputusan 16): mengubahnya membuat semua ciphertext lama tidak bisa didekripsi.
- Tabel (`schema.sql`): `pengguna(id UUID PK, email TEXT UNIQUE, password_hash TEXT, dibuat TIMESTAMPTZ DEFAULT now())`, `sesi(id UUID PK, user_id UUID FK, token_hash TEXT UNIQUE, device_label TEXT, dibuat, terakhir_dilihat, dicabut BOOL DEFAULT false)`, `vault(user_id UUID, name TEXT, ciphertext TEXT, diubah TIMESTAMPTZ, PRIMARY KEY (user_id, name))`. Semua query berfilter `user_id`.

## Row Level Security — `puspa/accounts/rls.py` + `schema.sql` (definisi 2.15)

- RLS aktif di tabel `pengguna`, `sesi`, `vault` — TANPA `FORCE ROW LEVEL SECURITY` (STATUS.md keputusan 15): pemilik tabel/superuser melewati RLS (sifat Postgres); penegakan nyata hanya untuk koneksi sebagai peran `puspa_app` (NOLOGIN; koneksi jalan sebagai pemilik lalu `SET ROLE puspa_app` — bukti di `tests/test_rls.py`).
- 6 policy (dihitung dari `schema.sql`): `pengguna` — SELECT milik sendiri, INSERT daftar-atau-milik-sendiri, UPDATE milik sendiri, DELETE milik sendiri; `sesi` — FOR ALL milik sendiri; `vault` — FOR ALL milik sendiri. Policy membaca `app.current_user_id` (`coalesce(current_setting(..., true), '')`).
- Pengecualian INSERT registrasi: policy INSERT `pengguna` mengizinkan `app.current_user_id` kosong (sebelum sesi divalidasi, user_id belum diketahui). Login/registrasi/validasi sesi jalan sebagai pemilik tabel; setelah user_id diketahui, semua query berikutnya lewat helper RLS sebagai `puspa_app`.
- Helper `rls.py` (satu-satunya tempat kode aplikasi memasang pengguna saat ini): `set_current_user(conn, user_id)` / `clear_current_user(conn)` / `current_user_id(conn)` / `require_current_user(conn)` (raise `RlsError` kalau belum dipasang — fail-closed) / `as_user(conn, user_id)` (context manager, `SET LOCAL` — nilai hilang sendiri saat transaksi berakhir). `user_id` divalidasi sebagai UUID sebelum dipasang.
- Setup + pemulihan: `infra/setup_db.sql` (idempotent) membuat role `root` + `puspa_app` + DB `puspa_test` lalu menerapkan skema ledger + accounts. Postgres di VM ini tidak tahan restart — pulihkan dengan file itu (STATUS.md keputusan 14, README bagian "Setup database").
- RLS tabel `kejadian` BELUM ada (fase 2): isolasi ledger saat ini lewat registry agen + `require_owner` di pembaca bermilik (lihat bagian Akses di bawah).

## Akses lintas pengguna — `puspa/accounts/access.py` (definisi 2.6, jalur serangan ke-8)

- `require_owner(requesting_user_id, owner_user_id)` — SATU-SATUNYA tempat pemeriksaan kepemilikan; setiap API baca WAJIB memanggilnya SEBELUM query isi / dekripsi. Fail-closed: tolak (`AccessDeniedError`) kalau salah satu id None/kosong/bukan-string, atau keduanya berbeda. TIDAK menyentuh database.
- Fungsi baca bermilik (scoped readers) (pola sama: tentukan pemilik -> `require_owner` -> baru baca): `read_vault_entry(requesting_user_id, owner_user_id, name)` (ditolak SEBELUM dekripsi; pemilik benar + nama tidak ada -> `KeyError`); `resolve_session_as(requesting_user_id, token)` (token itu sendiri kredensial kepemilikan; memakai `owner_of_token` tanpa efek samping; token tidak dikenal/dicabut -> `SessionError`); `read_ledger_event(requesting_user_id, event_id)` (cek keberadaan dulu -> pemilik dari registry -> `require_owner` -> baru baca isi; agen tak terdaftar -> `AccessDeniedError`); `read_ledger_events_for_task(requesting_user_id, tugas_id)` (semua agen di tugas dicek dulu; satu saja milik orang lain -> seluruh tugas ditolak).
- Registry agen (`schema_agen.sql`, tabel `agen`: `agen_id TEXT PK`, `user_id UUID FK -> pengguna(id) ON DELETE CASCADE`): `create_agent_schema()`, `register_agent(agen_id, user_id)` (idempotent ke pemilik sama; ke pemilik lain -> `AccessDeniedError`), `owner_of(agen_id)` (None kalau belum terdaftar = ditolak fail-closed). Tabel `agen` dibuat sesuai kebutuhan oleh `create_agent_schema()`, bukan oleh `infra/setup_db.sql`.
- Alur/modul BELUM punya fungsi baca bermilik (tabelnya baru fase 4): saat tabelnya ada, pakai `require_owner` yang sama dengan `owner_user_id` dari baris alur/modul itu.
- Uji: `tests/test_access.py` (23 tes) + 3 tes j8 di `tests/test_attacks.py` (total 26 tes jalur 8).

## Pembaca terkarantina + injection — `puspa/quarantine` (definisi 2.6)

- `reader.py`: `QuarantinedReader(allowed_domains)`; `read_text(source, text) -> dict` dengan skema ketat `{source, judul (maks 200 karakter, ditandai data), angka: [{nilai, satuan, konteks_maks_200}], potongan (maks 200 karakter)}`. TIDAK punya alat tulis/konektor. TIDAK mengembalikan teks mentah sebagai instruksi; semua teks bebas ditandai `data`. Fase 0: reader menerima teks yang sudah diambil; pengambilan jaringan ada di sandbox/runner.
- `injection.py`: `scan(text) -> list[Finding{span, pola, tingkat}]` — deteksi pola instruksi (imperatif ID/EN: "kirim", "bayar", "abaikan instruksi", "kamu harus", "ignore previous", dll. + URL); `mark(text) -> str` — bungkus span mencurigakan sebagai `[DATA: ...]`. Ini lapis tambahan; pertahanan utama tetap karantina struktural.

## Sandbox fail-closed — `puspa/sandbox` (definisi 2.6)

- `runner.py`: `Sandbox(allowlist)`; `run(code: str, *, timeout_s=30) -> dict` — Docker-backed bila tersedia; bila Docker tidak tersedia, raise `SandboxUnavailable` (fail-closed; TIDAK PERNAH fallback ke eksekusi host). `check_available() -> bool`.
- `allowlist.py`: allowlist default — API model, API konektor, domain marketplace (tokopedia.com, shopee.co.id, blibli.com), domain provider tagihan (pln.co.id + situs PDAM). Localhost hanya untuk test double lewat flag eksplisit `allow_test_localhost=True`, tidak pernah di jalur prod.
- Instalasi Docker dicoba di fase ini; kalau gagal, catat sebagai blocker di STATUS.md dan tes menegaskan perilaku fail-closed.

## Integrasi chassis — `puspa/chassis` (definisi 2.6, 2.3, 2.13)

Prinsip: NOL baris kode nanobot diubah. Semua kait memakai titik ekstensi resmi nanobot (parameter konstruktor `AgentLoop` / `Nanobot.run`, subclass `AgentHook` / `ToolRegistry` / `SkillsLoader` / `Tool`).

- `context.py`: `token_context(payload)` (ContextVar; hanya payload dict yang SUDAH diverifikasi — string mentah diabaikan), `resolve_token_payload()` (ContextVar dulu, lalu `RequestContext.attributes["puspa_token"]`), `bind_token(payload)` / `reset_token(token)`, `make_ledger_sink(ledger, tugas_id, agen_id)` -> callable `log(jenis, isi)` (best-effort: eksepsi tulis ditelan supaya turn tidak jatuh).
- `tools.py`: `PuspaTool(Tool)` membungkus satu tool nanobot (mengikuti pola `_LegacyErrorPrefixTool` nanobot). Urutan `execute`: (1) token ada, kalau tidak -> tolak fail-closed; (2) nama tool ada di `payload["tools"]`, kalau tidak -> tolak; (3) kalau tool punya manifes skill terpasang, `check_tool(manifest, nama, public_key)` (signature wajib, lihat bagian Skill); (4) kalau tool memetakan ke kind aksi keluar (`KIND_TOOLS`: kirim/buat_event/siapkan_bayar/hapus) -> `Gate.request_approval`; tanpa gerbang -> tolak; kalau gerbang membuat permintaan, kembalikan `ToolResult.error("Menunggu persetujuan...")` TANPA eksekusi; (5) eksekusi tool asli; hasil string dipindai `puspa.quarantine.scan` — temuan dicatat di `tool_selesai` (deteksi saja; pemblokiran tugas Pemeriksa fase 2). Audit: `tool_dipanggil` + `tool_selesai` di setiap pemanggilan; isi TIDAK PERNAH memuat parameter tool (bisa berisi data sensitif).
- `registry.py`: `PuspaRegistry(ToolRegistry)` — `register` membungkus semua tool sekali (anti-bungkus-ganda via `isinstance`); `prepare_call` override sebagai backstop (tool yang lolos tanpa bungkus tetap dicek tokennya; ditolak + dicatat `tool_dipanggil`/`tool_selesai` status ditolak). `get_definitions` TIDAK dicek (skema yang dilihat model bukan eksekusi).
- `hooks.py`: `PuspaHook(AgentHook)` — observasi saja (hook nanobot tidak bisa mem-veto): `before_run` -> `tugas_dibuat`; `after_run` -> `tugas_selesai` + `pesan_agen` (final content, potong 2000 karakter); `on_error` -> `tugas_gagal`. `make_puspa_hook_factory(tugas_id, agen_id, log)` -> `AgentTurnHookFactory` untuk `build_agent_turn_hook`.
- `skills.py`: `PuspaSkillsLoader(SkillsLoader)` override `_load_skill` — skill sumber `workspace`/`plugin` WAJIB lolos `verify_skill` (tanpa `public_key` -> semua ditolak); sumber `builtin` dipercaya sebagai bagian chassis. Gagal verifikasi -> `None` (skill tidak terlihat, mengikuti semantik nanobot).
- `session.py`: `bind_turn(*, session_token, signing_key, public_key, agen_id, tools, domains=None, konektor=None, anggaran=None, exp_seconds=3600, tugas_id=None, dsn=None) -> (user_id, token_string, payload_dict)` — validasi sesi lalu terbitkan token izin agen (anggaran default {langkah_max 25, token_max 100000, rupiah_max 0}). `payload_from_token(token, public_key)` = `verify_token`.
- `sandbox_tool.py`: `SandboxTool(Tool)` — nama `jalankan_kode`, parameter {code (wajib), timeout_s (default 30)}, `read_only=False`, `concurrency_safe=True`. Eksekusi didelegasikan ke `puspa.sandbox.Sandbox`; `SandboxUnavailable` -> `ToolResult.error` (tidak pernah eksekusi di host). Tool shell bawaan nanobot TIDAK masuk daftar token Puspa.
- `factory.py`: `PuspaChassis` (dataclass config: tugas_id, agen_id, ledger, gate, public_key, sandbox, skill_manifests, log) + `build_registry(chassis)` (daftarkan `SandboxTool` dulu, sebelum `AgentLoop` dibuat) + `build_hook_factory(chassis)` + `assemble(chassis, config, bus=None)` -> `AgentLoop.from_config(config, bus=bus, tool_registry=registry, hooks=[...], hook_factories=[...])`.
- Token sub-agen: TIDAK ADA slot token di `SubagentManager.spawn` nanobot; mekanismenya adalah ContextVar Puspa yang di-set di sekitar `spawn()` (terlihat di sub-agen karena `asyncio.create_task` menyalin konteks — diuji di `test_integration.py`).

Yang BELUM di fase 0 (fase 2): uji turn ujung-ke-ujung dengan `AgentLoop` asli + provider model; penutupan alur persetujuan->eksekusi async; pemblokiran hasil beracun oleh Pemeriksa; kebijakan ledger saat Postgres mati; `Sandbox.run` di executor.

## Tes

- pytest. `tests/conftest.py`: fixture `test_dsn` (env `PUSPA_TEST_DSN` atau default peer-auth), `ephemeral_keys` (Ed25519 baru per sesi tes), `tmp_skill_dir`, `vault_master_key` (master key acak per tes, hanya di env proses tes). Fixture autouse: terapkan skema accounts lalu `TRUNCATE pengguna, sesi, vault CASCADE` sebelum dan sesudah tiap tes — TRUNCATE CASCADE ikut mengosongkan tabel `agen` (FK ke pengguna), jadi baris agen tidak bocor antar tes. Fallback `sys.path` ke clone lokal `./nanobot` hanya dipakai kalau direktorinya memang ada (guard `Path.exists()`); dari fresh clone, tes memakai nanobot dari dependency git pin.
- Tiap modul tes punya komentar header yang memetakan ke bagian definisi + gerbang.
- `test_attacks.py` (8 jalur, definisi 2.6) ditulis SETELAH paket mendarat, oleh subagen terpisah, berdasarkan antarmuka di kontrak ini.
- Jangan klaim lolos sebelum `pytest` hijau. Sertakan output tes di laporan akhir.
