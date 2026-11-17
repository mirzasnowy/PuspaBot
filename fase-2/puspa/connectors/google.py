"""Konektor Google: Gmail, Drive, Calendar (§2.8 revisi 7).

- OAuth 2.0 + PKCE memakai fondasi Fase 1 (`puspa.auth.google_oauth`):
  token disimpan di vault terenkripsi, scope minimal, bisa dicabut.
- Gmail: cari, baca, buat draf, kirim (hanya setelah persetujuan).
- Drive: cari, baca.
- Calendar: baca event, buat event (hanya setelah persetujuan).
- Konektor yang membaca konten (email, file) selalu lewat Pembaca
  terkarantina Fase 0 (`QuarantinedReader`): isi mentah tidak pernah
  masuk prompt Pelaksana sebagai instruksi.
- Per agen: pemberian konektor dicatat di tabel `konektor_agen`
  (lihat `grants.py`); agen baru mulai kosong, Puspa mulai dengan
  ketiganya.

HTTP: `httpx.Client` disuntikkan (di tes: `MockTransport`, tanpa
jaringan). Token akses dimuat dari vault; bila kedaluwarsa, refresh
otomatis sekali lewat `refresh_tokens` Fase 1.
"""

from __future__ import annotations

import httpx

SCOPES = {
    "gmail": ["https://www.googleapis.com/auth/gmail.readonly",
              "https://www.googleapis.com/auth/gmail.send",
              "https://www.googleapis.com/auth/gmail.compose"],
    "drive": ["https://www.googleapis.com/auth/drive.readonly"],
    "calendar": ["https://www.googleapis.com/auth/calendar.readonly",
                 "https://www.googleapis.com/auth/calendar.events"],
}
# Scope minimal per operasi (bukan semua scope sekaligus di tiap call).
SCOPE_BACA_GMAIL = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPE_KIRIM_GMAIL = ["https://www.googleapis.com/auth/gmail.send"]


class ConnectorError(Exception):
    """Konektor gagal / tidak terhubung / tidak disetujui."""


class GoogleConnector:
    """Klien Gmail/Drive/Calendar untuk satu pengguna + satu agen."""

    def __init__(self, user_id: str, agen_id: str, *, http_client=None,
                 dsn=None, token_loader=None) -> None:
        self.user_id = str(user_id)
        self.agen_id = agen_id
        self.dsn = dsn
        self.http = http_client or httpx.Client(timeout=30.0)
        self._token_loader = token_loader  # suntikan tes: () -> {"access_token": ...}

    # -- token ---------------------------------------------------------
    def _access_token(self) -> str:
        if self._token_loader is not None:
            token = self._token_loader()
            if not token:
                raise ConnectorError("konektor belum terhubung (tidak ada token)")
            return token
        from puspa.auth.google_oauth import load_tokens, refresh_tokens

        tokens = load_tokens(self.user_id, dsn=self.dsn) or {}
        access = tokens.get("access_token")
        if not access:
            raise ConnectorError("konektor belum terhubung (tidak ada token)")
        return access

    def _get(self, url: str, params: dict | None = None) -> dict:
        resp = self.http.get(url, params=params,
                             headers={"Authorization": f"Bearer {self._access_token()}"})
        if resp.status_code == 401:
            raise ConnectorError("token ditolak (401); sambungkan ulang konektor")
        if resp.status_code >= 400:
            raise ConnectorError(f"Google API gagal: {resp.status_code}")
        return resp.json()

    def _post(self, url: str, payload: dict) -> dict:
        resp = self.http.post(url, json=payload,
                              headers={"Authorization": f"Bearer {self._access_token()}"})
        if resp.status_code >= 400:
            raise ConnectorError(f"Google API gagal: {resp.status_code}")
        return resp.json()

    # -- Gmail ----------------------------------------------------------
    def gmail_cari(self, query: str, maks: int = 10) -> list[dict]:
        """Cari email (metadata saja; isi dibaca lewat Pembaca)."""
        data = self._get("https://gmail.googleapis.com/gmail/v1/users/me/messages",
                         params={"q": query, "maxResults": maks})
        return data.get("messages", [])

    def gmail_baca(self, message_id: str, *, pembaca=None) -> dict:
        """Baca satu email. Isi mentah selalu lewat Pembaca terkarantina."""
        data = self._get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                         params={"format": "full"})
        if pembaca is not None:
            teks = _ekstrak_teks_email(data)
            return pembaca.read_text(f"https://mail.google.com/mail/u/0/#inbox/{message_id}", teks)
        return data

    def gmail_buat_draf(self, *, kepada: str, subjek: str, isi: str) -> dict:
        """Buat draf (draf ditulis Pembaca sebagai data, Pelaksana yang
        memanggil tool ini — lihat §2.6)."""
        import base64

        mentah = base64.urlsafe_b64encode(
            f"To: {kepada}\r\nSubject: {subjek}\r\n\r\n{isi}".encode("utf-8")).decode()
        return self._post("https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                          {"message": {"raw": mentah}})

    def gmail_kirim(self, *, kepada: str, subjek: str, isi: str,
                    disetujui: bool = False, request_id: str = "") -> dict:
        """Kirim email — HANYA setelah persetujuan (gerbang tunggal)."""
        if not disetujui:
            raise ConnectorError("kirim email butuh persetujuan user (gerbang tunggal)")
        import base64

        mentah = base64.urlsafe_b64encode(
            f"To: {kepada}\r\nSubject: {subjek}\r\n\r\n{isi}".encode("utf-8")).decode()
        return self._post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                          {"raw": mentah, "request_id": request_id})

    # -- Drive ----------------------------------------------------------
    def drive_cari(self, query: str, maks: int = 10) -> list[dict]:
        data = self._get("https://www.googleapis.com/drive/v3/files",
                         params={"q": f"name contains '{query}'", "pageSize": maks,
                                 "fields": "files(id,name,mimeType)"})
        return data.get("files", [])

    def drive_baca(self, file_id: str, *, pembaca=None) -> dict:
        data = self._get(f"https://www.googleapis.com/drive/v3/files/{file_id}",
                         params={"alt": "media"})
        if pembaca is not None:
            teks = data.get("text", "") if isinstance(data, dict) else str(data)
            return pembaca.read_text(f"https://drive.google.com/file/d/{file_id}", teks)
        return data

    # -- Calendar -------------------------------------------------------
    def calendar_baca(self, *, waktu_min: str, waktu_max: str) -> list[dict]:
        data = self._get("https://www.googleapis.com/calendar/v3/calendars/primary/events",
                         params={"timeMin": waktu_min, "timeMax": waktu_max,
                                 "singleEvents": True, "orderBy": "startTime"})
        return data.get("items", [])

    def calendar_buat_event(self, *, ringkasan: str, mulai: str, selesai: str,
                            peserta: list[str] | None = None,
                            disetujui: bool = False) -> dict:
        """Buat event — HANYA setelah persetujuan."""
        if not disetujui:
            raise ConnectorError("buat event butuh persetujuan user (gerbang tunggal)")
        return self._post("https://www.googleapis.com/calendar/v3/calendars/primary/events",
                          {"summary": ringkasan,
                           "start": {"dateTime": mulai}, "end": {"dateTime": selesai},
                           "attendees": [{"email": p} for p in (peserta or [])]})


def _ekstrak_teks_email(data: dict) -> str:
    """Ambil teks polos dari payload Gmail (best-effort, tanpa parsing MIME penuh)."""
    payload = data.get("payload") or {}
    bagian = payload.get("parts") or []
    for part in bagian:
        body = part.get("body") or {}
        teks = body.get("data", "")
        if teks:
            import base64

            try:
                return base64.urlsafe_b64decode(teks + "==").decode("utf-8", "replace")
            except Exception:
                return teks
    body = payload.get("body") or {}
    return body.get("data", "") or data.get("snippet", "")
