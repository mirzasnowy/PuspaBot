"""Tests for per-type secret redaction (G22).

Every secret type must be redacted, and the original value
must never appear in the output. Run in-memory:
``pytest tests/test_credentials_redact.py --noconftest``.
"""

import pytest

from puspa.credentials.redact import (
    REDACTED,
    clear_registered_secrets,
    redact_record,
    redact_text,
    redact_trajectory,
    register_secret,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registered_secrets()
    yield
    clear_registered_secrets()


def test_api_keys_redacted():
    text = redact_text("key is sk-abcdefghij1234567890 and done")
    assert "sk-abcdefghij1234567890" not in text
    assert REDACTED in text
    text = redact_text("key sk-ant-api03-abcdefghij1234567890")
    assert "sk-ant-api03" not in text
    text = redact_text("token ghp_abcdefghij1234567890xyz")
    assert "ghp_" not in text


def test_aws_key_and_jwt_redacted():
    text = redact_text(
        "aws AKIAIOSFODNN7EXAMPLE and jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature-part"
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert "eyJhbGciOiJIUzI1NiJ9" not in text


def test_private_key_block_redacted():
    block = "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0B\n-----END PRIVATE KEY-----"
    text = redact_text(f"leaked {block} end")
    assert "MIIEvgIBADANBgkqhkiG9w0B" not in text
    assert "PRIVATE KEY" not in text


def test_assignments_keep_name_hide_value():
    text = redact_text("password=hunter2 and api_key=sk-abcdefghij1234567890")
    assert "hunter2" not in text
    assert "password=" in text
    assert REDACTED in text


def test_command_segments_redacted():
    text = redact_text("run curl --api-key sk-abcdefghij1234567890 --token mytoken1234567890")
    assert "sk-abcdefghij1234567890" not in text
    assert "mytoken1234567890" not in text
    assert "--api-key" in text
    text = redact_text("export OPENAI_API_KEY=sk-abcdefghij1234567890")
    assert "sk-abcdefghij1234567890" not in text


def test_db_connection_url_redacted():
    url = "postgres://dbuser:dbpass123@db.example.com:5432/puspa"
    text = redact_text(f"connecting to {url} now")
    assert "dbpass123" not in text
    assert "dbuser" not in text
    assert "db.example.com" not in text
    assert "postgres://" in text
    url2 = "mongodb+srv://admin:s3cretPass@cluster.example.net/app"
    text2 = redact_text(url2)
    assert "s3cretPass" not in text2
    assert "admin" not in text2


def test_headers_redacted():
    text = redact_text("Authorization: Bearer mybearer-token-abcdefghij1234")
    assert "mybearer-token-abcdefghij1234" not in text
    assert "Authorization:" in text
    text = redact_text("x-api-key: someapikeyvalue1234567890")
    assert "someapikeyvalue1234567890" not in text
    assert "x-api-key:" in text


def test_phone_numbers_redacted():
    text = redact_text("call me at +62 812-3456-7890 please")
    assert "812-3456-7890" not in text
    assert REDACTED in text
    text = redact_text("my number is 081234567890")
    assert "081234567890" not in text


def test_home_paths_redacted():
    text = redact_text("file at /home/nero/secrets.txt")
    assert "nero" not in text
    assert "~" in text
    text = redact_text(r"file at C:\Users\nero\secrets.txt")
    assert "nero" not in text


def test_vault_values_redacted():
    register_secret("vault-value-abcdef-12345678")
    text = redact_text("stored vault-value-abcdef-12345678 here")
    assert "vault-value-abcdef-12345678" not in text
    assert REDACTED in text
    # A different value is untouched.
    assert redact_text("nothing secret here") == "nothing secret here"


def test_partial_keys_redacted():
    token = "Ab3dEf9hIj2kLm4nOp6qRs8tUv0wXy1zA2b4Cd6eF8g"
    text = redact_text(f"partial key ...{token[-8:]} and full {token}")
    assert token not in text
    assert token[-8:] not in text


def test_clean_text_unchanged():
    text = "hello world, this is a normal sentence."
    assert redact_text(text) == text


def test_redact_record_recursive_and_secret_keys():
    record = {
        "user": "nero",
        "api_key": "sk-abcdefghij1234567890",
        "nested": {"password": "hunter2", "note": "call 081234567890"},
        "args": ["--token", "mytoken1234567890"],
        "count": 3,
    }
    cleaned = redact_record(record)
    assert cleaned["user"] == "nero"
    assert cleaned["api_key"] == REDACTED
    assert cleaned["nested"]["password"] == REDACTED
    assert "081234567890" not in cleaned["nested"]["note"]
    assert "mytoken1234567890" not in str(cleaned["args"])
    assert cleaned["count"] == 3
    # Input is not mutated.
    assert record["api_key"] == "sk-abcdefghij1234567890"


def test_redact_record_tuple_and_set_shapes():
    cleaned = redact_record(("sk-abcdefghij1234567890", 1))
    assert isinstance(cleaned, tuple)
    assert "sk-abcdefghij1234567890" not in str(cleaned)


def test_redact_trajectory_before_storage():
    steps = [
        {"step": "call", "args": {"api_key": "sk-abcdefghij1234567890"}},
        {"step": "observe", "output": "connected to postgres://u:p@h/db ok"},
        {"step": "answer", "text": "done, call 081234567890"},
    ]
    cleaned = redact_trajectory(steps)
    joined = str(cleaned)
    assert "sk-abcdefghij1234567890" not in joined
    assert "postgres://u:p@h" not in joined
    assert "081234567890" not in joined
    assert cleaned[0]["step"] == "call"
    assert cleaned[2]["text"].startswith("done, call")


def test_redact_text_rejects_non_string():
    with pytest.raises(ValueError):
        redact_text(123)


def test_redact_trajectory_rejects_non_list():
    with pytest.raises(ValueError):
        redact_trajectory("not a list")
