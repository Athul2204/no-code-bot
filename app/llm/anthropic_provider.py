from __future__ import annotations

from .base import LLMProvider, LLMResponse, ToolCall, LLMError
from .http import post_json

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_BASE = "https://api.anthropic.com"


def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []

    def push(role: str, blocks: list[dict]):
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        if m["role"] == "user":
            push("user", [{"type": "text", "text": m["content"] or "(empty)"}])
        elif m["role"] == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["arguments"]})
            if blocks:
                push("assistant", blocks)
        elif m["role"] == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}])
    return out


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "", base_url: str = ""):
        self.api_key, self.model = api_key, model or DEFAULT_MODEL
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")

    def chat(self, system, messages, tools, *, temperature=0.2, max_tokens=1024, model=None):
        payload = {"model": model or self.model, "max_tokens": max_tokens, "temperature": temperature,
                   "system": system, "messages": to_anthropic_messages(messages)}
        if tools:
            payload["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        data = post_json(f"{self.base_url}/v1/messages",
                         {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}, payload)
        text, calls = [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                args = block.get("input")
                calls.append(ToolCall(block["id"], block["name"], args if isinstance(args, dict) else {}))
        if not text and not calls:
            raise LLMError("The AI service returned an empty reply.")
        return LLMResponse("\n".join(text).strip(), calls)
