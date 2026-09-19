from quant_agent.llm.openai_client import (
    extract_response_text,
    extract_response_tool_decision,
    responses_input,
)


def test_extract_response_text_uses_output_text():
    assert extract_response_text({"output_text": "hello"}) == "hello"


def test_extract_response_text_falls_back_to_output_content():
    payload = {
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": "part one"},
                    {"type": "output_text", "text": "part two"},
                ]
            }
        ]
    }

    assert extract_response_text(payload) == "part one\npart two"


def test_extract_response_tool_decision_reads_native_function_call():
    decision = extract_response_tool_decision(
        {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_123",
                    "name": "metrics_latest",
                    "arguments": '{"run": "latest"}',
                }
            ]
        }
    )

    assert decision == {
        "type": "tool_call",
        "call_id": "call_123",
        "name": "metrics_latest",
        "args": {"run": "latest"},
        "provider_output": [
            {
                "type": "function_call",
                "call_id": "call_123",
                "name": "metrics_latest",
                "arguments": '{"run": "latest"}',
            }
        ],
    }


def test_responses_input_preserves_tool_call_and_output_pair():
    items = responses_input(
        [
            {"role": "user", "content": "inspect metrics"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "function": {"name": "metrics_latest", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_123", "content": '{"cagr": 0.12}'},
        ]
    )

    assert items[1]["type"] == "function_call"
    assert items[1]["call_id"] == "call_123"
    assert items[2] == {
        "type": "function_call_output",
        "call_id": "call_123",
        "output": '{"cagr": 0.12}',
    }


def test_openai_complete_structured_uses_responses_json_schema():
    from quant_agent.llm.openai_client import OpenAIResponsesClient

    captured = {}
    client = object.__new__(OpenAIResponsesClient)
    client.model = "test-model"

    def fake_post(payload):
        captured.update(payload)
        return {"output_text": '{"goal":"ok"}'}

    client._post = fake_post
    schema = {
        "type": "object",
        "properties": {"goal": {"type": "string"}},
        "required": ["goal"],
        "additionalProperties": False,
    }

    result = client.complete_structured("plan", "context", schema, "agent_plan")

    assert result == {"goal": "ok"}
    assert captured["text"]["format"] == {
        "type": "json_schema",
        "name": "agent_plan",
        "strict": True,
        "schema": schema,
    }
