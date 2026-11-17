"""Mock harness BYOK: server HTTP localhost yang merekam request persis.

Dipakai untuk membuktikan kompatibilitas protokol per provider TANPA kunci
nyata dan TANPA memanggil layanan luar: client provider nanobot (lewat
factory generik ``demo/byok_provider.py``) diarahkan ``base_url``-nya ke
server ini, server merekam URL + header auth + body, lalu mengembalikan
respons kalengan dalam format provider itu.

Format respons kalengan:
- OpenAI chat-completions (openai, gemini-via-compat, groq, deepseek,
  together, ollama, openai_compatible generik): ``POST */chat/completions``
  -> ``choices[0].message.tool_calls`` berisi satu ``baca_file`` dengan
  arguments ``{"path": "/tmp/contoh.txt"}``.
- Anthropic Messages API: ``POST */v1/messages`` -> content ``tool_use``
  ``baca_file`` dengan input yang sama.
- Responses API (Azure OpenAI): ``POST */responses`` (Azure: base
  ``{endpoint}/openai/v1/``) -> ``output`` berisi ``function_call``
  ``baca_file`` dengan arguments yang sama.
- Bedrock: BUKAN HTTP — boto3 tidak terpasang di venv ini dan Converse API
  memakai SigV4; mock-nya berupa fake client yang di-inject lewat
  ``build_byok_provider(..., client=FakeBedrockClient())`` dan merekam
  kwargs ``converse`` (``modelId``, ``messages``, ``toolConfig``).

Semua kunci di harness ini JELAS-JELAS palsu (``MOCK_KEY``). Rekaman request
disimpan sebagai JSON di ``demo/mock_logs/<provider>.json`` (header auth
terekam apa adanya — nilainya mock, jadi aman disimpan sebagai bukti).

Pemakaian::

    NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \\
      .venv/bin/python demo/mock_provider_server.py [--out demo/mock_logs]

    # atau dari Python / tes:
    from demo.mock_provider_server import MockProviderServer, run_mock_checks
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_FASE0 = Path(__file__).resolve().parent.parent
_DEMO_DIR = Path(__file__).resolve().parent
for _p in (str(_FASE0), str(_DEMO_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_NANOBOT_LOCAL = _FASE0 / "nanobot"
if str(_NANOBOT_LOCAL) not in sys.path and (_NANOBOT_LOCAL / "nanobot" / "__init__.py").exists():
    sys.path.insert(0, str(_NANOBOT_LOCAL))

from byok_provider import MOCK_KEY, build_byok_provider  # noqa: E402

#: Tool kalengan yang dipakai di semua format protokol.
TOOL_NAME = "read_file"
TOOL_ARGS = {"path": "/tmp/contoh.txt"}
TOOL_ARGS_JSON = json.dumps(TOOL_ARGS, ensure_ascii=False)

#: Pesan + tool standar untuk setiap panggilan mock.
MOCK_MESSAGES = [{"role": "users", "content": "Baca /tmp/contoh.txt dengan tool read_file."}]
MOCK_TOOLS = [{
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Baca isi file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}]


def canned_openai_chat(model: str) -> dict:
    """Respons kalengan OpenAI chat-completions dengan satu tool_call."""
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_mock_1",
                    "type": "function",
                    "function": {"name": TOOL_NAME, "arguments": TOOL_ARGS_JSON},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def canned_anthropic(model: str) -> dict:
    """Respons kalengan Anthropic Messages API dengan satu tool_use."""
    return {
        "id": "msg_mock",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{
            "type": "tool_use",
            "id": "toolu_mock_1",
            "name": TOOL_NAME,
            "input": dict(TOOL_ARGS),
        }],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def canned_responses(model: str) -> dict:
    """Respons kalengan OpenAI Responses API dengan satu function_call."""
    return {
        "id": "resp_mock",
        "object": "response",
        "created_at": 1700000000,
        "status": "completed",
        "model": model,
        "output": [{
            "type": "function_call",
            "id": "fc_mock_1",
            "call_id": "call_mock_1",
            "name": TOOL_NAME,
            "arguments": TOOL_ARGS_JSON,
            "status": "completed",
        }],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }


class MockProviderServer:
    """Server HTTP localhost sekali pakai: rekam request, balas kalengan."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.requests: list[dict[str, Any]] = []
        self._server = self._build_server(host, port)
        self.port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _build_server(self, host: str, port: int) -> ThreadingHTTPServer:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # bungkam log stdlib
                pass

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except Exception:
                    body = {"_raw_not_json": raw.decode("utf-8", "replace")}
                record = {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
                outer.requests.append(record)
                payload = outer._canned_for(self.path, body)
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_POST = _handle
            do_GET = _handle

        return ThreadingHTTPServer((host, port), Handler)

    def _canned_for(self, path: str, body: Any) -> dict:
        model = "mock-model"
        if isinstance(body, dict) and isinstance(body.get("model"), str):
            model = body["model"]
        if path.rstrip("/").endswith("/v1/messages"):
            return canned_anthropic(model)
        if path.rstrip("/").endswith("/responses"):
            return canned_responses(model)
        return canned_openai_chat(model)

    def __enter__(self) -> "MockProviderServer":
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()

    def last_request(self) -> dict[str, Any]:
        if not self.requests:
            raise AssertionError("mock server tidak menerima request apa pun")
        return self.requests[-1]

    def save_log(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.requests, indent=2, ensure_ascii=False), encoding="utf-8")
        return target


class FakeBedrockClient:
    """Fake client boto3 ``bedrock-runtime``: rekam kwargs converse, balas kalengan.

    Bentuk respons mengikuti Bedrock Converse API (``output.message.content``
    dengan block ``toolUse``), supaya ``BedrockProvider._parse_response``
    nanobot bisa diparse sampai jadi ``ToolCallRequest``.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {
            "output": {"message": {
                "role": "assistant",
                "content": [{
                    "toolUse": {
                        "toolUseId": "toolu_mock_bedrock",
                        "name": TOOL_NAME,
                        "input": dict(TOOL_ARGS),
                    },
                }],
            }},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
            "metrics": {"latencyMs": 1},
        }


def assert_tool_call(resp: Any, *, provider_label: str) -> None:
    """Assert respons terparse sampai satu tool_call baca_file yang benar."""
    assert resp is not None, f"{provider_label}: respons None"
    assert getattr(resp, "finish_reason", None) not in ("error", None), (
        f"{provider_label}: finish_reason={getattr(resp, 'finish_reason', None)!r} "
        f"content={getattr(resp, 'content', None)!r}"
    )
    calls = list(getattr(resp, "tool_calls", None) or [])
    assert len(calls) == 1, f"{provider_label}: tool_calls={calls!r}"
    call = calls[0]
    assert call.name == TOOL_NAME, f"{provider_label}: tool name={call.name!r}"
    args = call.arguments
    if isinstance(args, str):
        args = json.loads(args)
    assert args == TOOL_ARGS, f"{provider_label}: arguments={args!r}"


async def check_openai_compat(provider: str, model: str, server: MockProviderServer,
                              *, key: str = MOCK_KEY) -> dict[str, Any]:
    """Satu cek mock OpenAI-compatible: bangun provider via factory, chat, assert."""
    prov = build_byok_provider(provider, model, key=key, base_url=f"{server.url}/v1")
    resp = await prov.chat(MOCK_MESSAGES, tools=MOCK_TOOLS, max_tokens=200, temperature=0.0)
    try:
        await prov.aclose()
    except Exception:
        pass
    assert_tool_call(resp, provider_label=provider)
    req = server.last_request()
    assert req["path"].rstrip("/").endswith("/chat/completions"), f"{provider}: path={req['path']!r}"
    assert req["headers"].get("authorization") == f"Bearer {key}", (
        f"{provider}: header auth={req['headers'].get('authorization')!r}")
    body = req["body"] or {}
    assert body.get("model"), f"{provider}: body tanpa model"
    assert body.get("messages"), f"{provider}: body tanpa messages"
    assert body.get("tools"), f"{provider}: body tanpa tools"
    return {"provider": provider, "model": model, "endpoint": req["path"],
            "auth_header": "Authorization: Bearer <mock-key>",
            "message_format": "openai chat-completions", "result": "mock passed"}


async def check_anthropic(server: MockProviderServer, *, key: str = MOCK_KEY) -> dict[str, Any]:
    """Satu cek mock Anthropic native: endpoint /v1/messages, header x-api-key."""
    model = "claude-sonnet-4-6"
    prov = build_byok_provider("anthropic", model, key=key, base_url=server.url)
    resp = await prov.chat(MOCK_MESSAGES, tools=MOCK_TOOLS, max_tokens=200, temperature=0.0)
    try:
        await prov.aclose()
    except Exception:
        pass
    assert_tool_call(resp, provider_label="anthropic")
    req = server.last_request()
    assert req["path"].rstrip("/").endswith("/v1/messages"), f"anthropic: path={req['path']!r}"
    assert req["headers"].get("x-api-key") == key, (
        f"anthropic: header x-api-key={req['headers'].get('x-api-key')!r}")
    assert "anthropic-version" in req["headers"], "anthropic: header anthropic-version hilang"
    body = req["body"] or {}
    assert body.get("model") == model, f"anthropic: body model={body.get('model')!r}"
    assert isinstance(body.get("messages"), list), "anthropic: body tanpa messages"
    return {"provider": "anthropic", "model": model, "endpoint": req["path"],
            "auth_header": "x-api-key: <mock-key> (+ anthropic-version)",
            "message_format": "anthropic messages", "result": "mock passed"}


async def check_azure(server: MockProviderServer, *, key: str = MOCK_KEY) -> dict[str, Any]:
    """Satu cek mock Azure OpenAI: Responses API di {endpoint}/openai/v1/responses."""
    model = "gpt-4o-deployment-mock"
    prov = build_byok_provider("azure_openai", model, key=key, base_url=server.url)
    resp = await prov.chat(MOCK_MESSAGES, tools=MOCK_TOOLS, max_tokens=200, temperature=0.0)
    try:
        await prov.aclose()
    except Exception:
        pass
    assert_tool_call(resp, provider_label="azure_openai")
    req = server.last_request()
    assert req["path"].rstrip("/").endswith("/responses"), f"azure: path={req['path']!r}"
    assert req["headers"].get("authorization") == f"Bearer {key}", (
        f"azure: header auth={req['headers'].get('authorization')!r}")
    body = req["body"] or {}
    assert body.get("model") == model, f"azure: body model={body.get('model')!r}"
    return {"provider": "azure_openai", "model": model, "endpoint": req["path"],
            "auth_header": "Authorization: Bearer <mock-key>",
            "message_format": "openai responses", "result": "mock passed"}


async def check_bedrock_fake_client() -> dict[str, Any]:
    """Satu cek mock Bedrock: fake client di-inject, kwargs converse direkam."""
    fake = FakeBedrockClient()
    model = "anthropic.claude-sonnet-4-6"
    prov = build_byok_provider("bedrock", model, region="us-east-1", client=fake)
    resp = await prov.chat(MOCK_MESSAGES, tools=MOCK_TOOLS, max_tokens=200, temperature=0.0)
    try:
        await prov.aclose()
    except Exception:
        pass
    assert_tool_call(resp, provider_label="bedrock")
    assert len(fake.calls) == 1, f"bedrock: converse dipanggil {len(fake.calls)}x"
    kwargs = fake.calls[0]
    assert kwargs.get("modelId") == model, f"bedrock: modelId={kwargs.get('modelId')!r}"
    assert kwargs.get("messages"), "bedrock: kwargs tanpa messages"
    assert kwargs.get("toolConfig", {}).get("tools"), "bedrock: kwargs tanpa toolConfig.tools"
    return {"provider": "bedrock", "model": model, "endpoint": "converse (fake client, bukan HTTP)",
            "auth_header": "kredensial AWS (SigV4) — tidak lewat HTTP mock",
            "message_format": "bedrock converse", "result": "mock passed (fake client)"}


async def run_mock_checks(out_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Jalankan SEMUA cek mock, simpan log request per provider, kembalikan ringkasan."""
    result: list[dict[str, Any]] = []
    out = Path(out_dir) if out_dir else _DEMO_DIR / "mock_logs"
    out.mkdir(parents=True, exist_ok=True)
    with MockProviderServer() as server:
        # OpenAI-compatible: satu server, base_url diganti per provider.
        compat_cases = [
            ("openai", "gpt-4o-mini"),
            ("gemini", "gemini-2.5-flash"),
            ("groq", "llama-3.1-8b-instant"),
            ("deepseek", "deepseek-chat"),
            ("together", "meta-llama/Llama-3.1-8b-Instruct"),
            ("ollama", "llama3.1:8b"),
            ("openai_compatible", "model-generik-mock"),
        ]
        for name, model in compat_cases:
            sebelum = len(server.requests)
            summarize = await check_openai_compat(name, model, server)
            summarize["request_log"] = str(out / f"{name}.json")
            result.append(summarize)
            # Simpan HANYA request milik cek ini.
            (out / f"{name}.json").write_text(
                json.dumps(server.requests[sebelum:], indent=2, ensure_ascii=False), encoding="utf-8")
        sebelum = len(server.requests)
        summarize = await check_anthropic(server)
        summarize["request_log"] = str(out / "anthropic.json")
        result.append(summarize)
        (out / "anthropic.json").write_text(
            json.dumps(server.requests[sebelum:], indent=2, ensure_ascii=False), encoding="utf-8")
        sebelum = len(server.requests)
        summarize = await check_azure(server)
        summarize["request_log"] = str(out / "azure_openai.json")
        result.append(summarize)
        (out / "azure_openai.json").write_text(
            json.dumps(server.requests[sebelum:], indent=2, ensure_ascii=False), encoding="utf-8")
    summarize = await check_bedrock_fake_client()
    summarize["request_log"] = "(fake client boto3 — kwargs converse, bukan HTTP; lihat tes)"
    result.append(summarize)
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Mock harness BYOK (tanpa kunci, tanpa internet).")
    parser.add_argument("--out", default=str(_DEMO_DIR / "mock_logs"))
    args = parser.parse_args()
    result = asyncio.run(run_mock_checks(args.out))
    print(f"Mock BYOK: {len(result)}/{len(result)} provider lolos")
    for h in result:
        print(f"  - {h['provider']:18s} {h['model']:35s} {h['endpoint']}  [{h['result']}]")
    print(f"Log request per provider: {args.out}/<provider>.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
