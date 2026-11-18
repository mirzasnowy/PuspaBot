"""Tests for own-model selection in PWA Settings (G33).

Persistence: the selection lives in Postgres (table
``model_settings``), the BYOK key lives only in the existing
per-user vault (``byok:<provider>``, envelope-encrypted).
Router wiring: every completion goes through an
``httpx.MockTransport`` that records the request, so the tests
prove the request really went to the selected base URL with
the selected key — the selection is not just stored.

No test touches the network or a real key: the only key values
used are obviously fake (``"test-byok-key-not-real"``) and are
only ever sent to the mock transport, never to a real host.
"""

import httpx
import psycopg
import pytest

from puspa.pwa.model_settings import (
    DEFAULT_LOCAL_BASE_URL,
    DEFAULT_LOCAL_MODEL,
    ModelSelection,
    complete_with_selection,
    create_schema,
    delete_byok_key,
    get_selection,
    has_byok_key,
    llm_complete_for_user,
    resolve_completion_kwargs,
    save_byok_key,
    save_selection,
    validate_base_url,
)
from puspa.router.complete import OPENROUTER_API_BASE

FAKE_BYOK_KEY = "test-byok-key-not-real"


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture(autouse=True)
def _model_settings_table(test_dsn):
    create_schema(test_dsn)
    yield
    with psycopg.connect(test_dsn) as conn:
        conn.execute("TRUNCATE model_settings")
        conn.commit()


def mock_reply(text="hello from the model"):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "model": "test-model",
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


class CapturingTransport(httpx.MockTransport):
    """A mock transport that records every request it receives."""

    def __init__(self):
        super().__init__(self._handle)
        self.requests = []

    def _handle(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=mock_reply())


# -- defaults and persistence -------------------------------------------


def test_default_selection_is_hosted(user, test_dsn):
    selection = get_selection(user["user_id"], dsn=test_dsn)
    assert selection.source == "hosted"
    assert selection.base_url is None
    assert selection.model is None
    assert selection.provider is None
    assert selection.has_byok_key is False


def test_local_selection_persists_across_reloads(user, test_dsn):
    saved = save_selection(
        user["user_id"],
        ModelSelection(source="local", base_url="http://localhost:11434/v1", model="llama3.1"),
        dsn=test_dsn,
    )
    assert saved.source == "local"
    # A fresh read (a new connection, as after a restart) sees it.
    reloaded = get_selection(user["user_id"], dsn=test_dsn)
    assert reloaded == saved
    assert reloaded.base_url == "http://localhost:11434/v1"
    assert reloaded.model == "llama3.1"


def test_local_selection_defaults_to_ollama(user, test_dsn):
    saved = save_selection(user["user_id"], {"source": "local"}, dsn=test_dsn)
    assert saved.base_url == DEFAULT_LOCAL_BASE_URL
    assert saved.model == DEFAULT_LOCAL_MODEL


def test_byok_selection_persists_and_reports_key_presence(user, test_dsn, vault_master_key):
    assert (
        save_selection(
            user["user_id"], {"source": "byok", "provider": "OpenRouter"}, dsn=test_dsn
        ).provider
        == "openrouter"
    )
    assert get_selection(user["user_id"], dsn=test_dsn).has_byok_key is False
    save_byok_key(user["user_id"], "openrouter", FAKE_BYOK_KEY, dsn=test_dsn)
    assert get_selection(user["user_id"], dsn=test_dsn).has_byok_key is True


def test_hosted_selection_clears_overrides(user, test_dsn):
    save_selection(
        user["user_id"],
        ModelSelection(source="local", base_url="http://localhost:11434/v1"),
        dsn=test_dsn,
    )
    saved = save_selection(user["user_id"], {"source": "hosted"}, dsn=test_dsn)
    assert saved.source == "hosted"
    assert saved.base_url is None and saved.model is None and saved.provider is None


# -- validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "ftp://example.com/v1",
        "localhost:11434/v1",
        "javascript:alert(1)",
        "http://",
        "https://user:secret@example.com/v1",
        "",
    ],
)
def test_validate_base_url_rejects_non_http(bad_url):
    with pytest.raises(ValueError):
        validate_base_url(bad_url)


@pytest.mark.parametrize("good_url", ["http://localhost:11434/v1", "https://api.example.com/v1"])
def test_validate_base_url_accepts_http(good_url):
    assert validate_base_url(good_url) == good_url


def test_save_selection_rejects_bad_base_url(user, test_dsn):
    with pytest.raises(ValueError):
        save_selection(
            user["user_id"],
            {"source": "local", "base_url": "ftp://example.com/v1"},
            dsn=test_dsn,
        )
    assert get_selection(user["user_id"], dsn=test_dsn).source == "hosted"


def test_save_selection_rejects_unknown_source(user, test_dsn):
    with pytest.raises(ValueError):
        save_selection(user["user_id"], {"source": "quantum"}, dsn=test_dsn)


def test_byok_selection_needs_provider(user, test_dsn):
    with pytest.raises(ValueError):
        save_selection(user["user_id"], {"source": "byok"}, dsn=test_dsn)


# -- keys live in the vault only -----------------------------------------


@pytest.mark.parametrize("key_field", ["api_key", "apiKey", "key", "secret", "token", "password"])
def test_save_selection_refuses_key_material(user, test_dsn, key_field):
    with pytest.raises(ValueError, match="vault"):
        save_selection(
            user["user_id"],
            {"source": "byok", "provider": "openrouter", key_field: "sk-should-not-land"},
            dsn=test_dsn,
        )
    assert get_selection(user["user_id"], dsn=test_dsn).source == "hosted"


def test_byok_key_never_in_selection_row(user, test_dsn, vault_master_key):
    save_selection(user["user_id"], {"source": "byok", "provider": "openrouter"}, dsn=test_dsn)
    save_byok_key(user["user_id"], "openrouter", FAKE_BYOK_KEY, dsn=test_dsn)
    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT source, base_url, model, provider FROM model_settings")
        row = cur.fetchone()
        cur.execute("SELECT ciphertext FROM vault")
        vault_rows = cur.fetchall()
    assert FAKE_BYOK_KEY not in repr(row)
    assert all(FAKE_BYOK_KEY not in (cipher or "") for (cipher,) in vault_rows)
    assert has_byok_key(user["user_id"], "openrouter", dsn=test_dsn) is True
    delete_byok_key(user["user_id"], "openrouter", dsn=test_dsn)
    assert has_byok_key(user["user_id"], "openrouter", dsn=test_dsn) is False


def test_save_byok_key_refuses_empty_key(user, test_dsn, vault_master_key):
    with pytest.raises(ValueError):
        save_byok_key(user["user_id"], "openrouter", "   ", dsn=test_dsn)


# -- router wiring: the selected endpoint is really used ------------------


def test_hosted_selection_uses_openrouter_default(user, test_dsn):
    transport = CapturingTransport()
    result = complete_with_selection(
        "hi",
        user_id=user["user_id"],
        dsn=test_dsn,
        transport=transport,
        key_resolver=lambda provider: FAKE_BYOK_KEY,
    )
    assert result.text == "hello from the model"
    assert len(transport.requests) == 1
    assert str(transport.requests[0].url) == OPENROUTER_API_BASE + "/chat/completions"


def test_local_selection_goes_to_local_base_url(user, test_dsn):
    save_selection(
        user["user_id"],
        ModelSelection(source="local", base_url="http://127.0.0.1:11434/v1", model="llama3.1"),
        dsn=test_dsn,
    )
    transport = CapturingTransport()
    result = complete_with_selection(
        "hi", user_id=user["user_id"], dsn=test_dsn, transport=transport
    )
    assert result.text == "hello from the model"
    assert result.provider == "ollama"
    assert result.model == "llama3.1"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer ollama"


def test_byok_selection_goes_to_provider_with_vault_key(user, test_dsn, vault_master_key):
    save_selection(
        user["user_id"],
        {"source": "byok", "provider": "openai", "model": "gpt-4o-mini"},
        dsn=test_dsn,
    )
    save_byok_key(user["user_id"], "openai", FAKE_BYOK_KEY, dsn=test_dsn)
    transport = CapturingTransport()
    result = complete_with_selection(
        "hi", user_id=user["user_id"], dsn=test_dsn, transport=transport
    )
    assert result.text == "hello from the model"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_BYOK_KEY}"
    import json

    assert json.loads(request.content)["model"] == "gpt-4o-mini"


def test_byok_selection_without_key_fails_loudly(user, test_dsn, vault_master_key):
    save_selection(user["user_id"], {"source": "byok", "provider": "openrouter"}, dsn=test_dsn)
    with pytest.raises(KeyError, match="no BYOK key stored"):
        resolve_completion_kwargs(user["user_id"], dsn=test_dsn)
    with pytest.raises(KeyError):
        complete_with_selection(
            "hi", user_id=user["user_id"], dsn=test_dsn, transport=CapturingTransport()
        )


def test_llm_complete_for_user_uses_selection_on_each_call(user, test_dsn):
    save_selection(user["user_id"], {"source": "local"}, dsn=test_dsn)
    transport = CapturingTransport()
    complete = llm_complete_for_user(
        user_id=user["user_id"],
        dsn=test_dsn,
        transport=transport,
        key_resolver=lambda provider: FAKE_BYOK_KEY,
    )
    assert complete("hi") == "hello from the model"
    assert str(transport.requests[0].url).startswith(DEFAULT_LOCAL_BASE_URL)
    # A Settings change takes effect without rebuilding the callable.
    save_selection(user["user_id"], {"source": "hosted"}, dsn=test_dsn)
    assert complete("hi") == "hello from the model"
    assert str(transport.requests[1].url) == OPENROUTER_API_BASE + "/chat/completions"


def test_explicit_caller_kwargs_win_over_selection(user, test_dsn):
    save_selection(user["user_id"], {"source": "local"}, dsn=test_dsn)
    transport = CapturingTransport()
    complete_with_selection(
        "hi",
        user_id=user["user_id"],
        dsn=test_dsn,
        transport=transport,
        base_url="http://localhost:9999/v1",
    )
    assert str(transport.requests[0].url) == "http://localhost:9999/v1/chat/completions"
