from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from quant_agent.llm.secrets import get_secret
from quant_agent.llm.structured import parse_structured_object


DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekChatClient:
    provider = "deepseek"

    def __init__(self, model: str | None = None, timeout_seconds: int = 90) -> None:
        self.api_key = get_secret("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set.")
        self.model = model or os.environ.get("QUANT_AGENT_DEEPSEEK_MODEL", DEFAULT_MODEL)
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, instructions: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": 1800,
            "thinking": {"type": "disabled"},
        }
        return extract_chat_completion_text(self._post(payload))

    def decide_with_tools(
        self,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": instructions}, *chat_messages(messages)],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                }
                for tool in tools
            ],
            "tool_choice": "auto",
            "stream": False,
            "max_tokens": 1800,
            "thinking": {"type": "disabled"},
        }
        return extract_chat_tool_decision(self._post(payload))

    def complete_structured(
        self,
        instructions: str,
        prompt: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        del schema, schema_name
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            "max_tokens": 1800,
            "thinking": {"type": "disabled"},
        }
        return parse_structured_object(extract_chat_completion_text(self._post(payload)))

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API error {exc.code}: {error_body}") from exc
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise RuntimeError("DeepSeek response was not a JSON object.")
        return parsed


def chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {"role", "content", "tool_calls", "tool_call_id"}
    return [{key: value for key, value in message.items() if key in allowed} for message in messages]


def extract_chat_tool_decision(payload: dict[str, Any]) -> dict[str, Any]:
    message = extract_chat_message(payload)
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list):
        raise RuntimeError("DeepSeek tool_calls was not a list.")
    if len(calls) > 1:
        raise RuntimeError("DeepSeek response returned multiple tool calls; this agent executes one tool per turn.")
    if calls:
        call = calls[0]
        function = call.get("function", {}) if isinstance(call, dict) else {}
        if not isinstance(function, dict):
            raise RuntimeError("DeepSeek function tool call was malformed.")
        return {
            "type": "tool_call",
            "call_id": str(call.get("id") or ""),
            "name": str(function.get("name") or ""),
            "args": parse_tool_arguments(function.get("arguments")),
        }
    return {"type": "final", "content": extract_chat_completion_text(payload)}


def parse_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise RuntimeError("Tool-call arguments were not a JSON object or JSON string.")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Tool-call arguments were not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Tool-call arguments must decode to a JSON object.")
    return parsed


def extract_chat_message(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("DeepSeek response did not contain choices.")
    first = choices[0]
    if not isinstance(first, dict):
        raise RuntimeError("DeepSeek response choice was not an object.")
    message = first.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("DeepSeek response did not contain a message.")
    return message


def extract_chat_completion_text(payload: dict[str, Any]) -> str:
    message = extract_chat_message(payload)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek response did not contain readable text.")
    return content
