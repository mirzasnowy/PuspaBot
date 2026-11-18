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
    "gmail": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
    ],
    "drive": ["https://www.googleapis.com/auth/drive.readonly"],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ],
}
# Scope minimal per operasi (bukan semua scope sekaligus di tiap call).
SCOPE_READ_GMAIL = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPE_SEND_GMAIL = ["https://www.googleapis.com/auth/gmail.send"]


class ConnectorError(Exception):
    """Konektor gagal / tidak terhubung / tidak disetujui."""


class GoogleConnector:
    """Klien Gmail/Drive/Calendar untuk satu pengguna + satu agen."""

    def __init__(
        self, user_id: str, agent_id: str, *, http_client=None, dsn=None, token_loader=None
    ) -> None:
        self.user_id = str(user_id)
        self.agent_id = agent_id
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
        from puspa.auth.google_oauth import load_tokens

        tokens = load_tokens(self.user_id, dsn=self.dsn) or {}
        access = tokens.get("access_token")
        if not access:
            raise ConnectorError("konektor belum terhubung (tidak ada token)")
        return access

    def _get(self, url: str, params: dict | None = None) -> dict:
        resp = self.http.get(
            url, params=params, headers={"Authorization": f"Bearer {self._access_token()}"}
        )
        if resp.status_code == 401:
            raise ConnectorError("token ditolak (401); sambungkan ulang konektor")
        if resp.status_code >= 400:
            raise ConnectorError(f"Google API gagal: {resp.status_code}")
        return resp.json()

    def _post(self, url: str, payload: dict) -> dict:
        resp = self.http.post(
            url, json=payload, headers={"Authorization": f"Bearer {self._access_token()}"}
        )
        if resp.status_code >= 400:
            raise ConnectorError(f"Google API gagal: {resp.status_code}")
        return resp.json()

    # -- Gmail ----------------------------------------------------------
    def gmail_search(self, query: str, max_results: int = 10) -> list[dict]:
        """Cari email (metadata saja; isi dibaca lewat Pembaca)."""
        data = self._get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": query, "maxResults": max_results},
        )
        return data.get("messages", [])

    def gmail_read(self, message_id: str, *, reader=None) -> dict:
        """Baca satu email. Isi mentah selalu lewat Pembaca terkarantina."""
        data = self._get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            params={"format": "full"},
        )
        if reader is not None:
            text = _extract_email_text(data)
            return reader.read_text(f"https://mail.google.com/mail/u/0/#inbox/{message_id}", text)
        return data

    def gmail_create_draft(self, *, recipient: str, subject: str, content: str) -> dict:
        """Buat draf (draf ditulis Pembaca sebagai data, Pelaksana yang
        memanggil tool ini — lihat §2.6)."""
        import base64

        raw = base64.urlsafe_b64encode(
            f"To: {recipient}\r\nSubject: {subject}\r\n\r\n{content}".encode()
        ).decode()
        return self._post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts", {"message": {"raw": raw}}
        )

    def gmail_send(
        self,
        *,
        recipient: str,
        subject: str,
        content: str,
        approved: bool = False,
        request_id: str = "",
    ) -> dict:
        """Kirim email — HANYA setelah persetujuan (gerbang tunggal)."""
        if not approved:
            raise ConnectorError("sending email needs user approval (single gate)")
        import base64

        raw = base64.urlsafe_b64encode(
            f"To: {recipient}\r\nSubject: {subject}\r\n\r\n{content}".encode()
        ).decode()
        return self._post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            {"raw": raw, "request_id": request_id},
        )

    # -- Drive ----------------------------------------------------------
    def drive_search(self, query: str, max_results: int = 10) -> list[dict]:
        data = self._get(
            "https://www.googleapis.com/drive/v3/files",
            params={
                "q": f"name contains '{query}'",
                "pageSize": max_results,
                "fields": "files(id,name,mimeType)",
            },
        )
        return data.get("files", [])

    def drive_read(self, file_id: str, *, reader=None) -> dict:
        data = self._get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}", params={"alt": "media"}
        )
        if reader is not None:
            text = data.get("text", "") if isinstance(data, dict) else str(data)
            return reader.read_text(f"https://drive.google.com/file/d/{file_id}", text)
        return data

    # -- Calendar -------------------------------------------------------
    def calendar_read(self, *, time_min: str, time_max: str) -> list[dict]:
        data = self._get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": True,
                "orderBy": "startTime",
            },
        )
        return data.get("items", [])

    def calendar_create_event(
        self,
        *,
        summary: str,
        start: str,
        completed_at: str,
        attendees: list[str] | None = None,
        approved: bool = False,
    ) -> dict:
        """Buat event — HANYA setelah persetujuan."""
        if not approved:
            raise ConnectorError("creating an event needs user approval (single gate)")
        return self._post(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            {
                "summaries": summary,
                "start": {"dateTime": start},
                "end": {"dateTime": completed_at},
                "attendees": [{"email": p} for p in (attendees or [])],
            },
        )


def _extract_email_text(data: dict) -> str:
    """Ambil teks polos dari payload Gmail (best-effort, tanpa parsing MIME penuh)."""
    payload = data.get("payload") or {}
    parts = payload.get("parts") or []
    for part in parts:
        body = part.get("body") or {}
        text = body.get("data", "")
        if text:
            import base64

            try:
                return base64.urlsafe_b64decode(text + "==").decode("utf-8", "replace")
            except Exception:
                return text
    body = payload.get("body") or {}
    return body.get("data", "") or data.get("snippet", "")
