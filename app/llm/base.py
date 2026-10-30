"""Provider-neutral LLM interface.

Internal message format (what the chat engine stores and passes to providers):
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments"}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str(JSON)}
Tools: [{"name", "description", "parameters": <JSON schema>}]
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class LLMError(Exception):
    """A user-presentable LLM problem (bad key, rate limit, network...). Never contains the API key."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider(ABC):
    name = ""

    @abstractmethod
    def chat(self, system: str, messages: list[dict], tools: list[dict], *, temperature: float = 0.2,
             max_tokens: int = 1024, model: str | None = None) -> LLMResponse: ...

    def test_connection(self) -> str:
        resp = self.chat("You are a connection test. Reply with the single word OK.", [{"role": "user", "content": "ping"}], [], max_tokens=16)
        return resp.text.strip() or "(empty reply)"
