"""Autentikasi Puspa — Fase 1: login Google + tautan email (keputusan 16)."""

from .email_link import (
    LINK_TTL_SECONDS,
    EmailLinkError,
    create_email_link,
    verify_email_link,
)
from .google_oauth import (
    LOGIN_SCOPES,
    OAuthError,
    build_auth_url,
    complete_google_login,
    create_google_user,
    delete_tokens,
    exchange_code,
    fetch_userinfo,
    find_user_by_email,
    google_client_secret,
    load_tokens,
    refresh_tokens,
    revoke_tokens,
    store_tokens,
    verify_id_token,
)

__all__ = [
    "LINK_TTL_SECONDS",
    "LOGIN_SCOPES",
    "EmailLinkError",
    "OAuthError",
    "build_auth_url",
    "complete_google_login",
    "create_email_link",
    "create_google_user",
    "delete_tokens",
    "exchange_code",
    "fetch_userinfo",
    "find_user_by_email",
    "google_client_secret",
    "load_tokens",
    "refresh_tokens",
    "revoke_tokens",
    "store_tokens",
    "verify_email_link",
    "verify_id_token",
]
