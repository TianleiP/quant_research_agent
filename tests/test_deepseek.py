from quant_agent.llm.deepseek_client import extract_chat_completion_text, extract_chat_tool_decision


def test_extract_chat_completion_text():
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "diagnosis text",
                }
            }
        ]
    }

    assert extract_chat_completion_text(payload) == "diagnosis text"


def test_extract_chat_tool_decision_reads_native_function_call():
    decision = extract_chat_tool_decision(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_ds_1",
                                "type": "function",
                                "function": {
                                    "name": "inspect_latest_run",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )

    assert decision == {
        "type": "tool_call",
        "call_id": "call_ds_1",
        "name": "inspect_latest_run",
        "args": {},
    }


def test_deepseek_complete_structured_uses_json_mode_and_parses_object():
    from quant_agent.llm.deepseek_client import DeepSeekChatClient

    captured = {}
    client = object.__new__(DeepSeekChatClient)
    client.model = "test-model"

    def fake_post(payload):
        captured.update(payload)
        return {"choices": [{"message": {"content": '{"verdict":"continue"}'}}]}

    client._post = fake_post
    result = client.complete_structured("evaluate JSON", "context", {}, "evaluation")

    assert result == {"verdict": "continue"}
    assert captured["response_format"] == {"type": "json_object"}
