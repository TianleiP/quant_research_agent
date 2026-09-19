from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from quant_agent.llm.secrets import get_secret
from quant_agent.llm.structured import parse_structured_object


DEFAULT_MODEL = "gpt-5.1-mini"
RESPONSES_URL = "https://api.openai.com/v1/responses"


class OpenAIResponsesClient:
    provider = "openai"

    def __init__(self, model: str | None = None, timeout_seconds: int = 60) -> None:
        self.api_key = get_secret("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        self.model = model or os.environ.get("QUANT_AGENT_LLM_MODEL", DEFAULT_MODEL)
        self.timeout_seconds = timeout_seconds

    def complete(self, instructions: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
            "max_output_tokens": 1800,
        }
        return extract_response_text(self._post(payload))

    def decide_with_tools(
        self,
        instructions: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": responses_input(messages),
            "tools": [
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                    "strict": False,
                }
                for tool in tools
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "max_output_tokens": 1800,
        }
        return extract_response_tool_decision(self._post(payload))

    def complete_structured(
        self,
        instructions: str,
        prompt: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": 1800,
        }
        return parse_structured_object(extract_response_text(self._post(payload)))

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            RESPONSES_URL,
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
            raise RuntimeError(f"OpenAI API error {exc.code}: {error_body}") from exc
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise RuntimeError("OpenAI response was not a JSON object.")
        return parsed


def responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            items.append({"role": "user", "content": str(message.get("content") or "")})
            continue
        if role == "assistant":
            provider_output = message.get("provider_output")
            if isinstance(provider_output, list) and provider_output:
                items.extend(item for item in provider_output if isinstance(item, dict))
                continue
            for call in message.get("tool_calls", []):
                function = call.get("function", {}) if isinstance(call, dict) else {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id"),
                        "name": function.get("name"),
                        "arguments": function.get("arguments", "{}"),
                    }
                )
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id"),
                    "output": str(message.get("content") or ""),
                }
            )
    return items


def extract_response_tool_decision(payload: dict[str, Any]) -> dict[str, Any]:
    calls = [
        item
        for item in payload.get("output", [])
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    if len(calls) > 1:
        raise RuntimeError("OpenAI response returned multiple tool calls; this agent executes one tool per turn.")
    if calls:
        call = calls[0]
        return {
            "type": "tool_call",
            "call_id": str(call.get("call_id") or call.get("id") or ""),
            "name": str(call.get("name") or ""),
            "args": parse_tool_arguments(call.get("arguments")),
            "provider_output": payload.get("output", []),
        }
    return {"type": "final", "content": extract_response_text(payload)}


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


def extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    parts = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    if parts:
        return "\n".join(parts)
    raise RuntimeError("OpenAI response did not contain readable text.")
