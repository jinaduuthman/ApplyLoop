"""LLM client: OpenAI-style body, param fallback, per-task routing, usage logging."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from applyloop import store as S
from applyloop.llm import LLM, LLMConfig, LLMPool, resolve_config


@pytest.fixture
def fake_openai():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            if "temperature" in body:          # mimic a reasoning model
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Unsupported parameter: \'temperature\' is not supported with this model."}}')
                return
            out = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(out).encode())

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


def test_openai_body_fallback_and_usage(fake_openai):
    url, seen = fake_openai
    st = S.Store(":memory:")
    llm = LLM(LLMConfig("openai", "gpt-x", "sk-test", url, "low", {"gpt-x": [0.2, 1.2]}, "score"),
              usage_cb=st.record_usage)
    assert llm.complete_json("say ok") == {"ok": True}
    first, second = seen
    assert "max_completion_tokens" in first and "max_tokens" not in first
    assert first["response_format"] == {"type": "json_object"} and first["reasoning_effort"] == "low"
    assert "temperature" not in second                      # dropped after the 400
    row = st.usage_summary()[0]
    assert (row["model"], row["task"], row["inp"], row["out"]) == ("gpt-x", "score", 1000, 500)
    assert abs(row["cost"] - (1000 * 0.2 + 500 * 1.2) / 1e6) < 1e-12


def test_per_task_models(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
    monkeypatch.delenv("APPLYLOOP_MODEL", raising=False)
    monkeypatch.delenv("APPLYLOOP_PROVIDER", raising=False)
    cfg = {"provider": "openai", "model": "cheap", "tasks": {"tailor": "strong",
                                                            "score": "anthropic:claude-haiku-4-5-20251001"}}
    assert resolve_config(cfg).model == "cheap"
    t = resolve_config(cfg, "tailor")
    assert (t.provider, t.model, t.api_key) == ("openai", "strong", "sk-o")
    s = resolve_config(cfg, "score")
    assert (s.provider, s.model, s.api_key) == ("anthropic", "claude-haiku-4-5-20251001", "sk-a")
    pool = LLMPool(cfg)
    assert pool.for_task("answers").config.model == "cheap"
    assert "tailor=openai:strong" in pool.describe()


def test_gemini_uses_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.delenv("APPLYLOOP_PROVIDER", raising=False)
    c = resolve_config({"provider": "gemini", "model": "gemini-3.1-flash-lite"})
    assert c.base_url.endswith("/v1beta/openai") and c.api_key == "g-key" and c.configured


def test_rejected_param_retried_even_if_already_dropped():
    from applyloop.llm import LLM, LLMConfig
    llm = LLM(LLMConfig())
    msg = "Unsupported value: 'temperature' does not support 0.2 with this model."
    assert llm._drop_rejected_param(msg) is True
    assert llm._drop_rejected_param(msg) is True        # second thread's in-flight request
    assert "temperature" in llm._dropped
