"""Tes factory BYOK generik + mock harness (demo/byok_provider.py).

Konvensi suite: tes sync, panggilan async dibungkus ``asyncio.run``
(seperti tests/test_integration.py). Tidak ada panggilan ke layanan luar
di file ini kecuali satu tes wrapper OpenRouter yang hanya mengambil
surrogate lokal (tanpa request HTTP). Semua cek protokol memakai mock
server localhost dengan kunci yang JELAS-JELAS palsu.
"""

import asyncio
import sys
from pathlib import Path

import pytest

_FASE0 = Path(__file__).resolve().parent.parent
_DEMO_DIR = _FASE0 / "demo"
for _p in (str(_FASE0), str(_DEMO_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from byok_provider import (  # noqa: E402
    MOCK_KEY,
    build_byok_provider,
    get_provider_key,
    put_provider_key,
    register_provider,
    resolve_provider_name,
    vault_key_name,
)
from mock_provider_server import (  # noqa: E402
    FakeBedrockClient,
    MockProviderServer,
    check_anthropic,
    check_azure,
    check_bedrock_fake_client,
    check_openai_compat,
    run_mock_checks,
)
from nanobot.providers.base import LLMProvider  # noqa: E402


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    """Mock localhost tidak boleh lewat proxy egress (lihat TOOLS.md)."""
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")


# --- resolusi nama / prefix ---------------------------------------------------

@pytest.mark.parametrize("model,provider", [
    ("openai/gpt-4o-mini", "openai"),
    ("anthropic/claude-sonnet-4-6", "anthropic"),
    ("gemini/gemini-2.5-flash", "gemini"),
    ("deepseek/deepseek-chat", "deepseek"),
    ("groq/llama-3.1-8b-instant", "groq"),
    ("gpt-4o-mini", "openai"),
    ("claude-sonnet-4-6", "anthropic"),
    ("gemini-2.5-flash", "gemini"),
])
def test_resolve_provider_dari_prefix_model(model, provider):
    assert resolve_provider_name(None, model) == provider


def test_resolve_provider_nama_eksplisit_menang():
    assert resolve_provider_name("openai", "anthropic/claude-sonnet-4-6") == "openai"


def test_resolve_provider_prefix_tak_dikenal_gagal_terlihat():
    with pytest.raises(ValueError, match="provider tidak bisa ditebak"):
        resolve_provider_name(None, "model-tanpa-prefix-jelas")


def test_resolve_provider_alias():
    assert resolve_provider_name("grok", "grok-4.6") == "xai"
    assert resolve_provider_name("custom", "m") == "openai_compatible"


# --- konstruksi factory (tanpa jaringan) --------------------------------------

def test_build_openai_mock_key():
    prov = build_byok_provider("openai", "gpt-4o-mini", key=MOCK_KEY)
    assert isinstance(prov, LLMProvider)
    assert prov.provider_name == "openai"
    assert prov.get_default_model() == "gpt-4o-mini"
    assert prov.api_base == "https://api.openai.com/v1"


def test_build_gemini_base_url_google():
    prov = build_byok_provider("gemini", "gemini-2.5-flash", key=MOCK_KEY)
    assert prov.provider_name == "gemini"
    assert prov.api_base == "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_build_groq_deepseek_dari_registry_nanobot():
    groq = build_byok_provider("groq", "llama-3.1-8b-instant", key=MOCK_KEY)
    assert groq.api_base == "https://api.groq.com/openai/v1"
    deepseek = build_byok_provider("deepseek", "deepseek-chat", key=MOCK_KEY)
    assert deepseek.api_base == "https://api.deepseek.com"


def test_build_together_bukti_satu_baris_config():
    # "together" TIDAK ada di registry nanobot; cukup satu baris
    # register_provider di byok_provider.py untuk masuk factory.
    from nanobot.providers.registry import find_by_name

    assert find_by_name("together") is None
    prov = build_byok_provider("together", "meta-llama/Llama-3.1-8b-Instruct", key=MOCK_KEY)
    assert prov.provider_name == "together"
    assert prov.api_base == "https://api.together.xyz/v1"


def test_register_provider_baru_satu_baris():
    nama = register_provider("vendor-contoh-tes", "https://api.vendor.example/v1")
    prov = build_byok_provider(nama, "model-contoh", key=MOCK_KEY)
    assert prov.api_base == "https://api.vendor.example/v1"
    with pytest.raises(ValueError, match="harus http"):
        register_provider("vendor-buruk", "bukan-url")


def test_build_anthropic_native():
    from nanobot.providers.anthropic_provider import AnthropicProvider

    prov = build_byok_provider("anthropic", "claude-sonnet-4-6", key=MOCK_KEY)
    assert isinstance(prov, AnthropicProvider)
    assert prov.provider_name == "anthropic"


def test_build_ollama_tanpa_kunci():
    prov = build_byok_provider("ollama", "llama3.1:8b")
    assert prov.provider_name == "ollama"
    assert prov.api_base == "http://localhost:11434/v1"


def test_build_openai_compatible_generik_butuh_base_url():
    with pytest.raises(ValueError, match="butuh base_url"):
        build_byok_provider("openai_compatible", "m", key=MOCK_KEY)
    prov = build_byok_provider("openai_compatible", "m", key=MOCK_KEY,
                               base_url="https://api.x.ai/v1")
    assert prov.api_base == "https://api.x.ai/v1"


def test_build_azure_butuh_endpoint():
    with pytest.raises(ValueError, match="butuh base_url"):
        build_byok_provider("azure_openai", "dep-mock", key=MOCK_KEY)


def test_build_provider_tak_dikenal_gagal_terlihat():
    with pytest.raises(ValueError, match="tidak dikenal"):
        build_byok_provider("vendor_tidak_ada", "model-x", key=MOCK_KEY)


def test_build_tanpa_kunci_gagal_terlihat(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(KeyError, match="kunci provider 'openai' belum ada"):
        build_byok_provider("openai", "gpt-4o-mini")


def test_build_model_kosong_gagal():
    with pytest.raises(ValueError, match="model tidak boleh kosong"):
        build_byok_provider("openai", "  ", key=MOCK_KEY)


# --- OAuth + Bedrock: konstruksi tanpa kredensial ------------------------------

@pytest.mark.parametrize("nama,model,kelas", [
    ("xai", "xai-grok/grok-4.6", "XAIGrokProvider"),
    ("github_copilot", "github-copilot/gpt-4.1", "GitHubCopilotProvider"),
    ("openai_codex", "openai-codex/gpt-5.6-sol", "OpenAICodexProvider"),
])
def test_build_oauth_konstruksi_tanpa_kredensial(nama, model, kelas):
    prov = build_byok_provider(nama, model)
    assert isinstance(prov, LLMProvider)
    assert type(prov).__name__ == kelas


def test_build_bedrock_tanpa_boto3_gagal_di_konstruksi():
    # boto3 tidak terpasang di venv ini: konstruksi tanpa client inject
    # harus gagal jelas (bukan klaim mock). Dengan fake client, jalan.
    with pytest.raises(RuntimeError, match="boto3"):
        build_byok_provider("bedrock", "anthropic.claude-sonnet-4-6", region="us-east-1")


def test_build_bedrock_dengan_fake_client():
    prov = build_byok_provider("bedrock", "anthropic.claude-sonnet-4-6",
                               region="us-east-1", client=FakeBedrockClient())
    assert prov.provider_name == "bedrock"
    assert prov.get_default_model() == "anthropic.claude-sonnet-4-6"


# --- vault per provider --------------------------------------------------------

def test_vault_key_name():
    assert vault_key_name("openai") == "byok:openai"
    assert vault_key_name("openai_compatible") == "byok:openai_compatible"


def test_vault_put_get_provider_key(vault_master_key, test_dsn):
    from puspa.accounts import create_user

    user_id = create_user("byok-tes@example.com", "pw", dsn=test_dsn)
    put_provider_key(user_id, "openai", "sk-mock-vault", dsn=test_dsn)
    assert get_provider_key(user_id, "openai", dsn=test_dsn) == "sk-mock-vault"
    # Provider lain tidak bisa membaca kunci provider ini (nama entri beda).
    with pytest.raises(KeyError):
        get_provider_key(user_id, "anthropic", dsn=test_dsn)
    # Factory memakai vault bila user_id diberi.
    prov = build_byok_provider("openai", "gpt-4o-mini", user_id=user_id, dsn=test_dsn)
    assert prov.api_key == "sk-mock-vault"


# --- wrapper lama tetap jalan --------------------------------------------------

def test_wrapper_openrouter_provider():
    from openrouter_provider import build_openrouter_provider

    prov = build_openrouter_provider("qwen/qwen3.7-flash")
    assert isinstance(prov, LLMProvider)
    assert prov.provider_name == "openrouter"
    assert prov.api_base == "https://openrouter.ai/api/v1"


# --- mock harness: satu tes per format protokol -------------------------------

def test_mock_openai_chat_completions():
    with MockProviderServer() as server:
        ringkas = asyncio.run(check_openai_compat("openai", "gpt-4o-mini", server))
    assert ringkas["hasil"] == "mock lolos"
    assert ringkas["endpoint"] == "/v1/chat/completions"


def test_mock_gemini_via_compat():
    with MockProviderServer() as server:
        ringkas = asyncio.run(check_openai_compat("gemini", "gemini-2.5-flash", server))
    assert ringkas["hasil"] == "mock lolos"


def test_mock_groq_deepseek():
    with MockProviderServer() as server:
        assert asyncio.run(check_openai_compat("groq", "llama-3.1-8b-instant", server))["hasil"] == "mock lolos"
        assert asyncio.run(check_openai_compat("deepseek", "deepseek-chat", server))["hasil"] == "mock lolos"


def test_mock_together_satu_baris_config():
    with MockProviderServer() as server:
        ringkas = asyncio.run(check_openai_compat(
            "together", "meta-llama/Llama-3.1-8b-Instruct", server))
    assert ringkas["hasil"] == "mock lolos"


def test_mock_openai_compatible_generik():
    with MockProviderServer() as server:
        ringkas = asyncio.run(check_openai_compat("openai_compatible", "model-generik-mock", server))
    assert ringkas["hasil"] == "mock lolos"


def test_mock_ollama_lokal():
    with MockProviderServer() as server:
        ringkas = asyncio.run(check_openai_compat("ollama", "llama3.1:8b", server))
    assert ringkas["hasil"] == "mock lolos"


def test_mock_anthropic_messages():
    with MockProviderServer() as server:
        ringkas = asyncio.run(check_anthropic(server))
    assert ringkas["hasil"] == "mock lolos"
    assert ringkas["endpoint"] == "/v1/messages"


def test_mock_azure_responses():
    with MockProviderServer() as server:
        ringkas = asyncio.run(check_azure(server))
    assert ringkas["hasil"] == "mock lolos"
    assert ringkas["endpoint"] == "/openai/v1/responses"


def test_mock_bedrock_fake_client():
    ringkas = asyncio.run(check_bedrock_fake_client())
    assert ringkas["hasil"] == "mock lolos (fake client)"


def test_mock_semua_provider_sekaligus(tmp_path):
    hasil = asyncio.run(run_mock_checks(tmp_path))
    assert len(hasil) == 10
    assert all("lolos" in h["hasil"] for h in hasil)
    assert {h["provider"] for h in hasil} == {
        "openai", "gemini", "groq", "deepseek", "together", "ollama",
        "openai_compatible", "anthropic", "azure_openai", "bedrock",
    }
