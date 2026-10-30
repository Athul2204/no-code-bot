from __future__ import annotations

import json

from .base import LLMProvider, LLMResponse, ToolCall, LLMError
from .http import post_json

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE = "https://api.openai.com/v1"


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"id": tc["id"], "type": "function",
                                      "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                                     for tc in m["tool_calls"]]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
    return out


class OpenAIProvider(LLMProvider):
    """Any OpenAI-compatible chat-completions API (OpenAI, OpenRouter, Ollama, vLLM, ...)."""
    name = "openai"

    def __init__(self, api_key: str, model: str = "", base_url: str = ""):
        self.api_key, self.model = api_key, model or DEFAULT_MODEL
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")

    def chat(self, system, messages, tools, *, temperature=0.2, max_tokens=1024, model=None):
        payload = {"model": model or self.model, "temperature": temperature, "max_tokens": max_tokens,
                   "messages": to_openai_messages(system, messages)}
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = post_json(f"{self.base_url}/chat/completions", headers, payload)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("The AI service returned an unexpected response.") from exc
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append(ToolCall(tc["id"], tc["function"]["name"], args if isinstance(args, dict) else {}))
        return LLMResponse((msg.get("content") or "").strip(), calls)
